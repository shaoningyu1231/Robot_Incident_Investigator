#!/usr/bin/env python3
"""Interactions API 三闸门技术验证(时间盒)。需要 GEMINI_API_KEY。绝不打印 key。

闸门:
  Q1: 在线 + 调 inspect_incident_window + images_submitted>0 + 该结论 evidence_strength=high
  Q2: 用 previous_interaction_id 续同一会话链 + 调 check_recovery_readiness + recovery=blocked
任何一项失败/报错 → 整体 FAIL,建议改走客户端 history 多轮。
运行: GEMINI_API_KEY=... <venv>/python3 backend/interactions_check.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))
import incident_rules as R
from interactions_client import InteractionsInvestigator

if not os.environ.get("GEMINI_API_KEY"):
    print("FAIL: GEMINI_API_KEY 未设置"); sys.exit(2)

inc = R.Incident.load(Path(os.environ.get("INCIDENT_DIR", ROOT / "incident")))
inv = InteractionsInvestigator(inc)
TS = ["10.4", "10.5", "10.6", "11.3"]
checks = []


def es_level(r):
    for tr in r.get("tool_results", []):
        if tr["tool"] == "inspect_incident_window":
            return (tr["result"].get("evidence_strength") or {}).get("level")
    return None


def rec_state(r):
    for tr in r.get("tool_results", []):
        if tr["tool"] == "check_recovery_readiness":
            return tr["result"].get("recovery_readiness")
    return None


print("===== Q1: why stop =====")
r1 = inv.investigate("Why did the robot stop? Inspect the incident around the stop and cite evidence timestamps.")
print("ok=", r1.get("ok"), "id=", r1.get("interaction_id"), "tools=", r1.get("tool_calls"),
      "images=", r1.get("images_submitted"), "es=", es_level(r1))
if not r1.get("ok"):
    print("error=", r1.get("error"), "detail=", r1.get("detail"))
print("answer:", (r1.get("answer") or "")[:300])

checks.append(("G1a Q1 在线无错(拿到 interaction id)", bool(r1.get("ok") and r1.get("interaction_id"))))
checks.append(("G1b 调用 inspect_incident_window", "inspect_incident_window" in r1.get("tool_calls", [])))
checks.append(("G1c 提交了 image part(images>0)", r1.get("images_submitted", 0) > 0))
checks.append(("G1d inspect 返回 evidence_strength=high", es_level(r1) == "high"))
checks.append(("G1e 答案引用正确时间戳", any(t in (r1.get("answer") or "") for t in TS)))

ok_q2 = bool(r1.get("ok") and r1.get("interaction_id"))
if ok_q2:
    print("\n===== Q2: recovery (continue same chain) =====")
    r2 = inv.investigate("Before resuming from this same stop, what conditions must be satisfied right now?",
                         previous_interaction_id=r1["interaction_id"])
    print("ok=", r2.get("ok"), "id=", r2.get("interaction_id"), "tools=", r2.get("tool_calls"),
          "recovery=", rec_state(r2))
    if not r2.get("ok"):
        print("error=", r2.get("error"), "detail=", r2.get("detail"))
    print("answer:", (r2.get("answer") or "")[:300])
    checks.append(("G2a Q2 续链在线无错", bool(r2.get("ok"))))
    checks.append(("G2b 调用 check_recovery_readiness", "check_recovery_readiness" in r2.get("tool_calls", [])))
    checks.append(("G2c recovery=blocked", rec_state(r2) == "blocked"))
else:
    checks.append(("G2 跳过(Q1 已失败)", False))

print("\n=== Interactions spike gates ===")
npass = sum(1 for _, ok in checks if ok)
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print(f"--- {npass}/{len(checks)} passed ---")
print("结论:" + ("全部通过 → 可继续完整叠加(加 /investigate/interactions 端点 + 复用在线 harness)。"
                  if npass == len(checks) else
                  "未全通过 → 按约定停止 Interactions,改走客户端 history 多轮(零新 API 风险)。"))
sys.exit(0 if npass == len(checks) else 1)
