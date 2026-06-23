#!/usr/bin/env python3
"""后端集成测试:启动真实 uvicorn 子进程,走真 HTTP(含 SSE),验证 A1–A6 + 多轮 + 校验。

不用 Starlette TestClient(某些环境下首请求会挂起);改起真实服务、httpx 打真 HTTP,
SSE 也按现场方式流式读取。强制离线(不带 GEMINI_API_KEY),确定性快速。
运行: <venv>/python3 backend/test_backend.py
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CID = "concl_obstacle_stop"
checks = []


def chk(name, ok, detail=""):
    checks.append((name, ok, detail))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main():
    port = free_port()
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)          # 强制离线,确定性
    env["PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen([sys.executable, str(ROOT / "backend" / "app.py")],
                            cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        # 等待起来
        up = False
        for _ in range(50):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    up = True
                    break
            except Exception:
                pass
            time.sleep(0.3)
        chk("server 起来 (/health 200)", up)
        if not up:
            return finish()

        c = httpx.Client(base_url=base, timeout=30)

        def es_level(s, e):
            j = c.post("/tools/inspect_incident_window", json={"start": s, "end": e, "conclusion_id": CID}).json()
            return j["evidence_strength"]["level"], j["evidence_strength"]["checks"]

        # health 完整性
        h = c.get("/health").json()
        chk("health integrity_ok", h.get("integrity_ok") is True, str(h))
        chk("health gemini=False(离线)", h.get("gemini") is False, str(h.get("gemini")))

        # A1–A3 via HTTP inspect
        l1, k1 = es_level(9.5, 11.8); chk("A1 [9.5,11.8]==high", l1 == "high", str(k1))
        l2, k2 = es_level(9.5, 10.8); chk("A2 [9.5,10.8]==low", l2 == "low" and not k2["required_present"], str(k2))
        l3, k3 = es_level(0.0, 10.0); chk("A3 [0,10]==low", l3 == "low" and not k3["required_present"], str(k3))

        # A4 recovery
        rb = c.post("/tools/check_recovery_readiness", json={"evaluation_window": [12, 18]}).json()
        chk("A4a recovery[12,18]==blocked", rb["recovery_readiness"] == "blocked", str(rb["recovery_readiness"]))
        rm = c.post("/tools/check_recovery_readiness", json={"evaluation_window": [19, 25]}).json()
        chk("A4b recovery[19,25]==conditions_met", rm["recovery_readiness"] == "conditions_met", str(rm["recovery_readiness"]))

        # A5/A6 篡改(后端规则模块,与 /tools 同一实现)
        sys.path.insert(0, str(ROOT / "tools"))
        import incident_rules as R
        inc = R.Incident.load(ROOT / "incident")
        esT = R.evidence_strength(inc.replace(logs=[l for l in inc.logs if l.get("code") != "DEMO_OBSTACLE_STOP_01"]),
                                  CID, (9.5, 11.8))
        chk("A5 删 stop 日志 != high", esT["level"] != "high" and not esT["checks"]["required_present"], "")
        mt = {t: dict(m) for t, m in inc.metrics_by_t.items()}; mt[11.3] = dict(mt[11.3]); mt[11.3]["actual_speed_mps"] = 0.5
        esV = R.evidence_strength(inc.replace(metrics_by_t=mt), CID, (9.5, 11.8))
        chk("A6 halt 速度非零 != high", esV["level"] != "high" and not esV["checks"]["metrics_crossed"], "")

        # 工具输入校验 + search + media
        chk("inspect start>end -> 400", c.post("/tools/inspect_incident_window", json={"start": 11, "end": 9}).status_code == 400)
        sl = c.post("/tools/search_logs", json={"query": "obstacle entered"}).json()
        chk("search_logs 命中 stop", any(m["code"] == "DEMO_OBSTACLE_STOP_01" for m in sl["matches"]))
        mg = c.get("/media/charts/velocity.png")
        chk("media png 200", mg.status_code == 200 and mg.headers["content-type"].startswith("image/png"))
        chk("media 目录穿越 404", c.get("/media/../tools/scenario.py").status_code == 404)

        # /investigate 离线
        inv = c.post("/investigate", json={"question": "Why did the robot stop?"}).json()
        chk("investigate 离线 used_gemini=False", inv.get("used_gemini") is False)
        chk("investigate 引用根因+非安全认证", "obstacle" in inv["answer"].lower() and "not a safety certification" in inv["answer"].lower())
        chk("investigate 附图候选>0", len(inv.get("attached_images", [])) > 0)

        # 多轮 history 0→2→4 + 保留上下文
        j1 = c.post("/investigate", json={"question": "why stop?"}).json()
        chk("history turn1==2", len(j1.get("history", [])) == 2, str(len(j1.get("history", []))))
        j2 = c.post("/investigate", json={"question": "and recovery?", "history": j1["history"]}).json()
        chk("history turn2==4", len(j2.get("history", [])) == 4, str(len(j2.get("history", []))))
        chk("turn2 保留 turn1", any(x.get("text") == "why stop?" for x in j2.get("history", [])))

        # SSE 真流式:含 progress + 恰一条 result
        with c.stream("POST", "/investigate/stream", json={"question": "why stop?"}) as r:
            body = "".join(r.iter_text())
        chk("SSE 含 progress", "event: progress" in body)
        chk("SSE 恰一条 result", body.count("event: result") == 1, str(body.count("event: result")))

        # 输入校验 → 400(含新边界:8 轮满 + 角色交替)
        def code(payload):
            return c.post("/investigate", json=payload).status_code
        chk("非 list history -> 400", code({"question": "q", "history": "x"}) == 400)
        chk("坏 role -> 400", code({"question": "q", "history": [{"role": "sys", "text": "x"}]}) == 400)
        chk("非交替(user,user) -> 400", code({"question": "q", "history": [{"role": "user", "text": "a"}, {"role": "user", "text": "b"}]}) == 400)
        chk("奇数条(半轮) -> 400", code({"question": "q", "history": [{"role": "user", "text": "a"}]}) == 400)
        seven = [{"role": "user" if i % 2 == 0 else "model", "text": "x"} for i in range(14)]
        eight = [{"role": "user" if i % 2 == 0 else "model", "text": "x"} for i in range(16)]
        chk("7 轮历史(14条)可提交", code({"question": "q", "history": seven}) == 200)
        chk("8 轮历史(16条)第9轮 -> 400", code({"question": "q", "history": eight}) == 400)
        chk("空 question -> 400", code({"question": ""}) == 400)
        chk("stream 非 list history -> 400", c.post("/investigate/stream", json={"question": "q", "history": [1, 2]}).status_code == 400)

        c.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return finish()


def finish():
    npass = sum(1 for _, ok, _ in checks if ok)
    print("=== backend integration test (real uvicorn / real HTTP) ===")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if (detail and not ok) else ""))
    print(f"--- {npass}/{len(checks)} passed ---")
    return 0 if npass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
