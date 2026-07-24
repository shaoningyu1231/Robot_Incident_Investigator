#!/usr/bin/env python3
"""在线端到端验收:用真实 GEMINI_API_KEY 跑 Gemini function-calling,验证 5 项。

需要环境变量 GEMINI_API_KEY(可选 GEMINI_MODEL)。绝不打印 key。
1. "为什么停下?" 触发 inspect_incident_window
2. 返回 PNG image parts(images_submitted>0)
3. 回答引用正确证据时间戳(供人工确认,脚本做关键字命中)
4. 追问恢复条件,触发 check_recovery_readiness
5. 确认真实在线调用(mode==online),未进入 deterministic mode
运行: GEMINI_API_KEY=... <venv>/python3 backend/online_check.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))
import incident_rules as R
from gemini_client import GeminiInvestigator

if not os.environ.get("GEMINI_API_KEY"):
    print("FAIL: GEMINI_API_KEY 未设置 — 无法做在线验收。", file=sys.stderr)
    sys.exit(2)

inc = R.Incident.load(Path(os.environ.get("INCIDENT_DIR", ROOT / "incident")))
inv = GeminiInvestigator(inc)
TS = ["10.4", "10.5", "10.6", "11.3"]
checks = []


def show(tag, r):
    print(f"\n===== {tag} =====")
    print(f"mode={r['mode']} model={r['model']} tool_calls={r['tool_calls']} "
          f"images_submitted={r['images_submitted']}")
    if r.get("fallback_reason"):
        print(f"fallback_reason={r['fallback_reason']}")
    print("answer:", r["answer"])


# Q1 — 为什么停下
r1 = inv.investigate("Why did the robot stop? Inspect the incident and cite the evidence timestamps.")
show("Q1 why-stop", r1)
checks.append(("1 触发 inspect_incident_window", "inspect_incident_window" in r1["tool_calls"]))
checks.append(("2 提交了 PNG image parts", r1["images_submitted"] > 0))
checks.append(("3 回答引用正确时间戳", any(t in r1["answer"] for t in TS)))
checks.append(("5a Q1 在线(非 fallback)", r1["mode"] == "online" and not r1.get("fallback_reason")))

# Q2 — 追问恢复条件
r2 = inv.investigate("What conditions must be satisfied before the robot can resume after this stop?")
show("Q2 recovery", r2)
checks.append(("4 触发 check_recovery_readiness", "check_recovery_readiness" in r2["tool_calls"]))
checks.append(("5b Q2 在线(非 fallback)", r2["mode"] == "online" and not r2.get("fallback_reason")))

print("\n=== online acceptance ===")
npass = sum(1 for _, ok in checks if ok)
for name, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print(f"--- {npass}/{len(checks)} passed ---")
print("\n人工确认项:上面 Q1 的 answer 是否引用了正确的证据(LiDAR 10.4 / front_distance 10.5 / "
      "stop 10.6 / halt 11.3),且 Q2 给出 conditions_met/blocked/insufficient_evidence 而非安全许可。")
sys.exit(0 if npass == len(checks) else 1)
