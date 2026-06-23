"""时间盒技术验证:Gemini Interactions API 路径(独立,不改 GeminiInvestigator)。

复用 gemini_client.run_tool / SYSTEM / TOOL_DECLS 和 tools/incident_rules。
只为验证三个闸门:能提交图像 part、能跑 inspect 工具循环、能用 previous_interaction_id 续轮调 recovery。
失败/超时 → 放弃,改走客户端 history 多轮。
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini_client import run_tool, SYSTEM, TOOL_DECLS, _scrub

INT_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# Interactions 工具格式: {type:"function", name, description, parameters}
INT_TOOLS = [{"type": "function", **d} for d in TOOL_DECLS]


def _img_part(inc, ref):
    data = base64.b64encode((inc.dir / ref).read_bytes()).decode()
    return {"type": "image", "mime_type": "image/png", "data": data}


def _collect_text(steps):
    out = []
    for s in steps:
        if s.get("type") in ("function_call", "function_result"):
            continue
        if isinstance(s.get("text"), str):
            out.append(s["text"])
        c = s.get("content")
        if isinstance(c, str):
            out.append(c)
        elif isinstance(c, list):
            out += [p.get("text", "") for p in c if isinstance(p, dict)]
    return "".join(out)


class InteractionsInvestigator:
    def __init__(self, inc, api_key=None, model=None, timeout=30.0, max_steps=6):
        self.inc = inc
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model or os.environ.get("GEMINI_INTERACTIONS_MODEL",
                                             os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"))
        self.timeout = timeout
        self.max_steps = max_steps

    def investigate(self, question, previous_interaction_id=None):
        headers = {"x-goog-api-key": self.api_key}
        prev = previous_interaction_id
        next_input = [{"type": "text", "text": question}]   # 首轮:文本 part 列表
        trace, tool_results, attached = [], [], set()
        last_id = None
        print(f"[interactions] model={self.model} prev={prev}", file=sys.stderr)
        with httpx.Client(timeout=self.timeout) as client:
            for step in range(self.max_steps):
                body = {"model": self.model, "system_instruction": SYSTEM,
                        "tools": INT_TOOLS, "store": True, "input": next_input}
                if prev:
                    body["previous_interaction_id"] = prev
                r = client.post(INT_URL, headers=headers, json=body)
                print(f"[interactions] step={step} http={r.status_code} bytes={len(r.content)}", file=sys.stderr)
                if r.status_code != 200:
                    return {"ok": False, "error": f"http {r.status_code}",
                            "detail": _scrub(r.text[:300]), "tool_calls": [t["tool"] for t in trace],
                            "images_submitted": len(attached), "trace": trace, "tool_results": tool_results,
                            "interaction_id": last_id, "model": self.model}
                j = r.json()
                last_id = j.get("id") or last_id
                prev = last_id
                steps = j.get("steps", [])
                calls = [s for s in steps if s.get("type") == "function_call"]
                if not calls:
                    return {"ok": True, "answer": _collect_text(steps), "interaction_id": last_id,
                            "tool_calls": [t["tool"] for t in trace], "images_submitted": len(attached),
                            "trace": trace, "tool_results": tool_results, "model": self.model,
                            "status": j.get("status")}
                results_input = []
                for c in calls:
                    name = c.get("name")
                    args = c.get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except Exception: args = {}
                    result, refs = run_tool(self.inc, name, args)
                    trace.append({"tool": name, "args": args})
                    tool_results.append({"tool": name, "result": result})
                    print(f"[interactions] tool_call={name} args={args} imgs+={len(refs)}", file=sys.stderr)
                    parts = [{"type": "text", "text": json.dumps(result)}]
                    for ref in refs:
                        if ref not in attached:
                            parts.append(_img_part(self.inc, ref))
                            attached.add(ref)
                    results_input.append({"type": "function_result", "call_id": c.get("id"),
                                          "name": name, "result": parts})
                next_input = results_input     # 续轮:回填工具结果(含图像 part)
        return {"ok": True, "answer": "(max steps reached)", "interaction_id": last_id,
                "tool_calls": [t["tool"] for t in trace], "images_submitted": len(attached),
                "trace": trace, "tool_results": tool_results, "model": self.model}
