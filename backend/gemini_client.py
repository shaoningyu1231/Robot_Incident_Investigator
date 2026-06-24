"""Gemini function-calling 循环(httpx 直连 REST,免 SDK)+ 离线回退。

工具执行复用 tools/incident_rules(不复制规则)。inspect 返回的 LiDAR/chart PNG
会作为 inlineData image part 再次提交给 Gemini,确保它真正"看到"图。
无 GEMINI_API_KEY 或网络/超时失败时,走确定性离线回退,保证 demo 可跑。
"""
from __future__ import annotations

import base64
import os
import re
import sys
import time
from pathlib import Path

import httpx

_KEY_RE = re.compile(r"(key=)[^&\s'\"]+", re.IGNORECASE)


def _scrub(s):
    """从任意日志文本里抹掉可能出现的 API key。"""
    return _KEY_RE.sub(r"\1***", str(s))


# --- 输入校验上限(demo 级:校验 + 截断;生产应改 server-side session,不信任客户端 model 历史)---
MAX_TURNS = 8            # 最多 8 轮 → 16 条
MAX_ITEM_CHARS = 4000    # 单条上限
MAX_TOTAL_CHARS = 24000  # 历史总字符上限
MAX_Q_CHARS = 4000       # 单次问题上限


class InputError(ValueError):
    pass


def validate_question(q):
    if not isinstance(q, str) or not q.strip():
        raise InputError("question must be a non-empty string")
    if len(q) > MAX_Q_CHARS:
        raise InputError(f"question exceeds {MAX_Q_CHARS} chars")
    return q


def validate_history(history):
    """只允许 [{role, text:str}] 且 user→model 成对交替;提交的历史最多 MAX_TURNS-1 轮。

    提交上限取 (MAX_TURNS-1) 轮,这样加上本轮后会话最多 MAX_TURNS 轮 —— 已满 8 轮时
    第 9 轮提交直接 400,不会先完成再报错(修边界 off-by-one)。
    """
    if history is None:
        return []
    if not isinstance(history, list):
        raise InputError("history must be a list")
    if len(history) > (MAX_TURNS - 1) * 2:
        raise InputError(f"history exceeds {MAX_TURNS - 1} prior turns (conversation capped at {MAX_TURNS})")
    if len(history) % 2 != 0:
        raise InputError("history must contain complete user/model pairs")
    total = 0
    for i, h in enumerate(history):
        if not isinstance(h, dict):
            raise InputError("history item must be an object")
        expected = "user" if i % 2 == 0 else "model"
        if h.get("role") != expected:
            raise InputError(f"history role at index {i} must be '{expected}' "
                             f"(turns must alternate user→model)")
        t = h.get("text")
        if not isinstance(t, str):
            raise InputError("history item text must be a string")
        if len(t) > MAX_ITEM_CHARS:
            raise InputError(f"history item exceeds {MAX_ITEM_CHARS} chars")
        total += len(t)
    if total > MAX_TOTAL_CHARS:
        raise InputError(f"history exceeds {MAX_TOTAL_CHARS} total chars")
    return history


def _hist_to_contents(history):
    """客户端多轮:把轻量 Q&A 历史([{role,text}])转成 contents 前缀(纯文本,不重传图片)。"""
    out = []
    for h in (history or []):
        role = "model" if h.get("role") == "model" else "user"
        out.append({"role": role, "parts": [{"text": h.get("text", "")}]})
    return out

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import incident_rules as R

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
SYSTEM = (
    "You are a robot incident investigator. Investigate using ONLY the provided tools and the "
    "evidence they return. Answer ONLY the question that was asked.\n"
    "For a 'why did it stop' / root-cause question: call inspect_incident_window once (window ~9-12s, "
    "conclusion_id='concl_obstacle_stop'), then state the root cause and CITE the specific evidence "
    "timestamps it returns — the LiDAR return, the front-distance threshold crossing, the stop event, "
    "and the velocity halt (around t=10.4, 10.5, 10.6 and 11.3 s). Report the evidence_strength level "
    "(high/medium/low) the tool returns; it reflects completeness and consistency, NOT a probability. "
    "Do NOT call check_recovery_readiness for a why/root-cause question, and do not discuss recovery.\n"
    "Only when the user EXPLICITLY asks about resuming / recovery / whether it can continue: call "
    "check_recovery_readiness and report conditions_met / blocked / insufficient_evidence as a "
    "recovery-condition check — never issue a safety certification.\n"
    "Use search_logs only to locate event times, never as a substitute for inspect_incident_window. "
    "When a tool returns LiDAR or chart images, ground your statements in what they show."
)

TOOL_DECLS = [
    {"name": "inspect_incident_window",
     "description": "Return all evidence in a time window: LiDAR frames, charts, logs, metric "
                    "summaries, and (if conclusion_id given) evidence_strength.",
     "parameters": {"type": "object", "properties": {
         "start": {"type": "number"}, "end": {"type": "number"},
         "conclusion_id": {"type": "string"},
         "reason": {"type": "string"}},
         "required": ["start", "end"]}},
    {"name": "check_recovery_readiness",
     "description": "Evaluate recovery preconditions over evaluation_window. Returns "
                    "conditions_met / blocked / insufficient_evidence (NOT a safety certification).",
     "parameters": {"type": "object", "properties": {
         "evaluation_window": {"type": "array", "items": {"type": "number"}}},
         "required": ["evaluation_window"]}},
    {"name": "search_logs",
     "description": "Search structured logs by free-text query, code, or node, optionally in a time range.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string"}, "code": {"type": "string"}, "node": {"type": "string"},
         "start": {"type": "number"}, "end": {"type": "number"}},
         "required": []}},
]


class ToolError(Exception):
    pass


def run_tool(inc, name, args):
    """执行确定性工具,返回 (json_result, media_refs)。"""
    try:
        if name == "inspect_incident_window":
            res = R.inspect_incident_window(inc, float(args["start"]), float(args["end"]),
                                            conclusion_id=args.get("conclusion_id"),
                                            reason=args.get("reason"))
            refs = [x["ref"] for x in res.get("lidar", [])][:2] + [x["ref"] for x in res.get("charts", [])]
            return res, refs
        if name == "check_recovery_readiness":
            w = args["evaluation_window"]
            return R.check_recovery_readiness(inc, (float(w[0]), float(w[1]))), []
        if name == "search_logs":
            return R.search_logs(inc, query=args.get("query"), code=args.get("code"),
                                 node=args.get("node"), start=args.get("start"), end=args.get("end")), []
        raise ToolError(f"unknown tool {name}")
    except ToolError:
        raise
    except Exception as e:  # 缺证据 / 参数错 → 把错误回给模型,不崩
        return {"error": f"{type(e).__name__}: {e}"}, []


def _image_part(inc, ref):
    p = inc.dir / ref
    data = base64.b64encode(p.read_bytes()).decode()
    return {"inlineData": {"mimeType": "image/png", "data": data}}


class GeminiInvestigator:
    def __init__(self, inc, api_key=None, model=None, timeout=30.0, max_steps=6, retries=3):
        self.inc = inc
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self.timeout = timeout
        self.max_steps = max_steps
        self.retries = retries

    def available(self):
        return bool(self.api_key)

    def investigate(self, question, history=None):
        """非流式:消费 _run,返回最终结果(history=None 即原单轮行为,保持在线验收不变)。"""
        result = None
        for kind, payload in self._run(question, history):
            if kind == "result":
                result = payload
        return result

    def _run(self, question, history=None):
        """唯一的调查循环。yield ("event", {...}) 进度,最后 yield 一次 ("result", {...})。
        流式端点和非流式 investigate() 都走这里,逻辑不重复。history 为轻量 Q&A 上下文。"""
        if not self.available():
            print("[gemini] FALLBACK reason=no_api_key", file=sys.stderr)
            yield ("event", {"event": "fallback", "reason": "no_api_key"})
            yield ("result", self._offline(question, why="no_api_key", history=history))
            return
        try:
            yield from self._run_online(question, history)
        except (httpx.HTTPError, httpx.TimeoutException, KeyError, ValueError) as e:
            print(f"[gemini] FALLBACK reason=gemini_unavailable:{type(e).__name__}: {_scrub(e)}",
                  file=sys.stderr)
            yield ("event", {"event": "fallback", "reason": f"gemini_unavailable:{type(e).__name__}"})
            yield ("result", self._offline(question, why=f"gemini_unavailable:{type(e).__name__}", history=history))

    def _post(self, client, url, headers, body):
        """带 429/500/503 退避重试的 POST(key 走 header,不进 URL)。"""
        delay = 1.0
        r = None
        for attempt in range(self.retries + 1):
            r = client.post(url, headers=headers, json=body)
            if r.status_code in (429, 500, 503) and attempt < self.retries:
                print(f"[gemini] transient http={r.status_code}, retry {attempt+1}/{self.retries} "
                      f"in {delay}s", file=sys.stderr)
                time.sleep(delay)
                delay *= 2
                continue
            return r
        return r

    # ---- 在线:真正的 function-calling 循环(生成器,边跑边 yield 进度)----
    def _run_online(self, question, history=None):
        url = f"{API_BASE}/{self.model}:generateContent"   # key 走 header,不进 URL
        headers = {"x-goog-api-key": self.api_key}
        contents = _hist_to_contents(history) + [{"role": "user", "parts": [{"text": question}]}]
        body_base = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
                     "tools": [{"functionDeclarations": TOOL_DECLS}],
                     "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
                     "generationConfig": {"temperature": 0}}  # determinism for stable demo behavior
        trace = []
        attached = set()
        print(f"[gemini] ONLINE model={self.model}", file=sys.stderr)
        yield ("event", {"event": "start", "model": self.model})
        with httpx.Client(timeout=self.timeout) as client:
            for step in range(self.max_steps):
                yield ("event", {"event": "thinking", "step": step})
                body = {**body_base, "contents": contents}
                r = self._post(client, url, headers, body)
                print(f"[gemini] step={step} http={r.status_code} bytes={len(r.content)}", file=sys.stderr)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"http {r.status_code}", request=r.request, response=r)
                cand = r.json()["candidates"][0]["content"]
                parts = cand.get("parts", [])
                calls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not calls:
                    text = "".join(p.get("text", "") for p in parts)
                    print(f"[gemini] DONE step={step} tool_calls={[t['tool'] for t in trace]} "
                          f"images_submitted={len(attached)}", file=sys.stderr)
                    yield ("result", self._result("online", text, trace, attached,
                                                  question=question, history=history))
                    return
                contents.append({"role": "model", "parts": parts})
                resp_parts = []
                for call in calls:
                    name, fargs = call["name"], call.get("args", {})
                    result, refs = run_tool(self.inc, name, fargs)
                    trace.append({"tool": name, "args": fargs})
                    print(f"[gemini] tool_call={name} args={fargs} imgs+={len(refs)}", file=sys.stderr)
                    new_imgs = [r for r in refs if r not in attached]
                    yield ("event", {"event": "tool_call", "tool": name, "args": fargs,
                                     "images": len(new_imgs)})
                    resp_parts.append({"functionResponse": {"name": name, "response": result}})
                    for ref in new_imgs:
                        resp_parts.append(_image_part(self.inc, ref))
                        attached.add(ref)
                contents.append({"role": "user", "parts": resp_parts})
        yield ("result", self._result("online", "(stopped: max tool steps reached)", trace, attached,
                                      question=question, history=history))

    def _result(self, mode, answer, trace, attached, question="", history=None, why=None):
        out = {"answer": answer, "mode": mode, "model": self.model,
               "used_gemini": mode == "online",
               "tool_calls": [t["tool"] for t in trace],
               "images_submitted": len(attached),
               "attached_images": sorted(attached) if isinstance(attached, set) else list(attached),
               "trace": trace,
               # 轻量多轮历史:供前端存下、下轮回传(纯文本,不含图片字节)
               "history": (history or []) + [{"role": "user", "text": question},
                                             {"role": "model", "text": answer}]}
        if why:
            out["fallback_reason"] = why
        return out

    # ---- 离线:确定性回退(无 Gemini 也能演示工具链)----
    def _offline(self, question, why, history=None):
        cid = self.inc.metadata["ground_truth"]["primary_conclusion_id"]
        insp = R.inspect_incident_window(self.inc, 9.5, 11.8, conclusion_id=cid,
                                         reason="offline default investigation window")
        es = insp["evidence_strength"]
        rec_block = R.check_recovery_readiness(self.inc, (12.0, 18.0))
        rec_clear = R.check_recovery_readiness(self.inc, (19.0, 25.0))
        ev = {e["id"]: e for e in self.inc.annotations["evidence"]}
        cited = ", ".join(f"{ev[e]['t']}s {e}" for e in
                          next(c for c in self.inc.annotations["conclusions"] if c["id"] == cid)["required_evidence"])
        answer = (
            f"[offline fallback — {why}] "
            f"Root cause: {self.inc.metadata['ground_truth']['root_cause']} "
            f"Evidence ({es['level']}): {cited}. "
            f"Recovery while stopped (12–18s): {rec_block['recovery_readiness']}; "
            f"after clear (19–25s): {rec_clear['recovery_readiness']}. "
            f"This is a recovery-condition check, not a safety certification."
        )
        media = [x["ref"] for x in insp["lidar"]][:2] + [x["ref"] for x in insp["charts"]]
        trace = [{"tool": "inspect_incident_window", "args": {"start": 9.5, "end": 11.8}},
                 {"tool": "check_recovery_readiness", "args": {"evaluation_window": [12, 18]}},
                 {"tool": "check_recovery_readiness", "args": {"evaluation_window": [19, 25]}}]
        out = self._result("offline", answer, trace, media, question=question, history=history, why=why)
        out["model"] = None  # 离线不归属任何模型
        return out
