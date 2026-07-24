"""Robot Incident Investigator 后端(Starlette ASGI,FastAPI 的底座,免装 fastapi)。

只读 incident/ 合成资产。暴露三个确定性工具 + /investigate(Gemini function-calling)。
工具规则全部来自 tools/incident_rules,与 validator 同一实现。
运行: <venv>/uvicorn backend.app:app  (或 python backend/app.py)
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import iterate_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse, FileResponse, HTMLResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import incident_rules as R
from gemini_client import (GeminiInvestigator, validate_question, validate_history,
                           InputError, _scrub, run_verify_conclusion)

INCIDENT_DIR = Path(os.environ.get("INCIDENT_DIR", ROOT / "incident"))
INC = R.Incident.load(INCIDENT_DIR)
INTEGRITY = R.integrity_checks(INC)
_bad = [n for n, ok, _ in INTEGRITY if not ok]
if _bad:
    print(f"[app] WARNING incident integrity failures: {_bad}", file=sys.stderr)
else:
    print(f"[app] incident '{INC.metadata['incident_id']}' loaded, integrity OK "
          f"({len(INTEGRITY)} checks)", file=sys.stderr)


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"

# --- Rerun-linked page (local, optional). Served only when the git-ignored web
# viewer assets exist (materialized by tools/prepare_rerun_web_assets.py); the
# main app and Cloud Run image stay unaffected. ---
mimetypes.add_type("application/wasm", ".wasm")  # else compileStreaming rejects octet-stream
mimetypes.add_type("application/octet-stream", ".rrd")  # binary recording, not text/plain
STATIC_DIR = Path(__file__).resolve().parent / "static"
RERUN_HTML = Path(__file__).resolve().parent / "frontend" / "rerun.html"
RERUN_VENDOR_INDEX = STATIC_DIR / "vendor" / "rerun-web-viewer" / "0.33.1" / "index.js"
RERUN_RRD = STATIC_DIR / "rerun" / "demo_obstacle_stop_01.rrd"
RERUN_SETUP_HINT = """<!doctype html><meta charset="utf-8"><title>Rerun mode not prepared</title>
<body style="font-family:system-ui;max-width:640px;margin:48px auto;line-height:1.6;color:#222">
<h1>Rerun mode is not set up locally</h1>
<p>The Rerun web viewer assets and the recording are dev-only and git-ignored.
Materialize them, then reload:</p>
<pre style="background:#f4f4f4;padding:12px;border-radius:6px">python tools/export_to_rerun.py
python tools/prepare_rerun_web_assets.py</pre>
</body>"""


class ScopedCrossOriginIsolation:
    """Add COOP/COEP only to /rerun and /static responses (required by the
    multi-threaded Rerun web viewer). Scoped so the main frontend and any
    cross-origin resources it loads are unaffected. Pure ASGI: streaming-safe
    for the 47 MB wasm."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/rerun" or path.startswith("/static")):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Embedder-Policy"] = "require-corp"
            await send(message)

        await self.app(scope, receive, send_wrapper)


async def index(request):
    return FileResponse(FRONTEND, media_type="text/html")


async def rerun_page(request):
    # Local-only: serve the linked page when assets exist, else a setup hint
    # (never a startup failure).
    if RERUN_VENDOR_INDEX.exists() and RERUN_RRD.exists():
        return HTMLResponse(RERUN_HTML.read_text())
    return HTMLResponse(RERUN_SETUP_HINT)


async def incident(request):
    """前端一次性拉取:metadata + timeline + annotations。"""
    return JSONResponse({"metadata": INC.metadata, "timeline": INC.timeline,
                         "annotations": INC.annotations})


async def health(request):
    return JSONResponse({"status": "ok", "incident_id": INC.metadata["incident_id"],
                         "integrity_ok": not _bad,
                         "gemini": GeminiInvestigator(INC).available()})


async def media(request):
    rel = request.path_params["path"]
    # 只允许 incident/ 下的 lidar_frames / charts,防目录穿越
    target = (INCIDENT_DIR / rel).resolve()
    base = INCIDENT_DIR.resolve()
    if base not in target.parents or target.suffix.lower() != ".png" \
            or rel.split("/")[0] not in ("lidar_frames", "charts") or not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(target, media_type="image/png")


def _num(v, name):
    if not isinstance(v, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(v)


async def tool_inspect(request):
    try:
        b = await request.json()
        start, end = _num(b["start"], "start"), _num(b["end"], "end")
        if start > end:
            return JSONResponse({"error": "start must be <= end"}, status_code=400)
        res = R.inspect_incident_window(INC, start, end, modalities=b.get("modalities"),
                                        conclusion_id=b.get("conclusion_id"), reason=b.get("reason"))
        return JSONResponse(res)
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def tool_recovery(request):
    try:
        b = await request.json()
        w = b["evaluation_window"]
        if not (isinstance(w, list) and len(w) == 2):
            return JSONResponse({"error": "evaluation_window must be [start, end]"}, status_code=400)
        res = R.check_recovery_readiness(INC, (_num(w[0], "start"), _num(w[1], "end")))
        return JSONResponse(res)
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def tool_verify_conclusion(request):
    """Spec-compiled verification: recompiles evidence for a hypothesis window
    (derived from the incident's own stop event, or window_override) and runs the
    unchanged verifier. Additive — the hand-authored annotations path is untouched."""
    try:
        b = await request.json()
        return JSONResponse(run_verify_conclusion(INC, b))
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def tool_search_logs(request):
    b = await request.json()
    res = R.search_logs(INC, query=b.get("query"), code=b.get("code"), node=b.get("node"),
                        start=b.get("start"), end=b.get("end"),
                        max_matches=int(b.get("max_matches", 100)))
    return JSONResponse(res)


async def investigate(request):
    b = await request.json()
    try:
        q = validate_question(b.get("question"))
        history = validate_history(b.get("history"))
    except InputError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    inv = GeminiInvestigator(INC, max_steps=int(b.get("max_steps", 6)),
                             timeout=float(b.get("timeout", 30.0)))
    return JSONResponse(inv.investigate(q, history=history))


async def investigate_stream(request):
    """SSE 流式:边调边推进度事件,最后推一条 result。复用 GeminiInvestigator._run。"""
    b = await request.json()
    try:
        q = validate_question(b.get("question"))
        history = validate_history(b.get("history"))
    except InputError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    inv = GeminiInvestigator(INC, max_steps=int(b.get("max_steps", 6)),
                             timeout=float(b.get("timeout", 30.0)))

    def gen():
        try:
            for kind, payload in inv._run(q, history):
                ev = "result" if kind == "result" else "progress"
                yield f"event: {ev}\ndata: {json.dumps(payload)}\n\n"
        except Exception as e:  # 兜底:服务端记脱敏详情,前端只给固定信息(不泄漏内部细节)
            print(f"[app] stream error: {_scrub(repr(e))}", file=sys.stderr)
            safe = {"answer": "Investigation failed due to a server error. Please retry.",
                    "mode": "offline", "model": None, "tool_calls": [], "images_submitted": 0,
                    "attached_images": [], "trace": []}
            yield f"event: result\ndata: {json.dumps(safe)}\n\n"

    return StreamingResponse(iterate_in_threadpool(gen()), media_type="text/event-stream")


app = Starlette(routes=[
    Route("/", index),
    Route("/rerun", rerun_page),
    Route("/incident", incident),
    Route("/health", health),
    Route("/media/{path:path}", media),
    Route("/tools/inspect_incident_window", tool_inspect, methods=["POST"]),
    Route("/tools/check_recovery_readiness", tool_recovery, methods=["POST"]),
    Route("/tools/search_logs", tool_search_logs, methods=["POST"]),
    Route("/tools/verify_conclusion", tool_verify_conclusion, methods=["POST"]),
    Route("/investigate", investigate, methods=["POST"]),
    Route("/investigate/stream", investigate_stream, methods=["POST"]),
    Mount("/static", app=StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static"),
])
app.add_middleware(ScopedCrossOriginIsolation)


if __name__ == "__main__":
    import uvicorn
    # 0.0.0.0 so the container is reachable on Cloud Run (binds $PORT, default 8080).
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
