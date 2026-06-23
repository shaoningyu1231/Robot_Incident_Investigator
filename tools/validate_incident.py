#!/usr/bin/env python3
"""校验导出的 incident/ 资产:用共享规则模块 incident_rules 跑断言 A1–A6 + 文件完整性。

规则实现不在这里复制 —— 一律调用 incident_rules,与 FastAPI 后端同一份实现。
只读 incident/ 下的合成资产。返回非零退出码表示有断言失败。
用法: <venv>/python3 tools/validate_incident.py [incident_dir]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import incident_rules as R

ROOT = Path(__file__).resolve().parents[1]


def main():
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "incident"
    inc = R.Incident.load(d)
    checks = list(R.integrity_checks(inc))  # 文件完整性 + 表述边界

    CID = "concl_obstacle_stop"

    # A1: 含 halt 的完整窗口 → high
    es1 = R.evidence_strength(inc, CID, (9.5, 11.8))
    checks.append(("A1 evidence_strength[9.5,11.8]==high", es1["level"] == "high", str(es1["checks"])))

    # A2: halt 之前的窗口 → low(ev_velocity_halt 缺)
    es2 = R.evidence_strength(inc, CID, (9.5, 10.8))
    checks.append(("A2 evidence_strength[9.5,10.8]==low (halt未发生)",
                   es2["level"] == "low" and not es2["checks"]["required_present"], str(es2["checks"])))

    # A3: 正常段 → low
    es3 = R.evidence_strength(inc, CID, (0.0, 10.0))
    checks.append(("A3 evidence_strength[0,10]==low (正常段)",
                   es3["level"] == "low" and not es3["checks"]["required_present"], str(es3["checks"])))

    # A5: 删 stop 日志 → 不再 high(资产核对)
    inc_no_stop = inc.replace(logs=[l for l in inc.logs if l.get("code") != "DEMO_OBSTACLE_STOP_01"])
    esT = R.evidence_strength(inc_no_stop, CID, (9.5, 11.8))
    checks.append(("A5 删除 stop 日志后 != high (资产核对生效)",
                   esT["level"] != "high" and not esT["checks"]["required_present"], str(esT["checks"])))

    # A6: halt 速度非零 → 不再 high(speed<=0.01 检查)
    mt2 = {t: dict(m) for t, m in inc.metrics_by_t.items()}
    mt2[11.3] = dict(mt2[11.3]); mt2[11.3]["actual_speed_mps"] = 0.5
    esV = R.evidence_strength(inc.replace(metrics_by_t=mt2), CID, (9.5, 11.8))
    checks.append(("A6 halt 速度非零(0.5)后 != high (speed<=0.01 检查生效)",
                   esV["level"] != "high" and not esV["checks"]["metrics_crossed"], str(esV["checks"])))

    # A4: recovery 两窗口
    rb = R.check_recovery_readiness(inc, (12.0, 18.0))
    checks.append(("A4a recovery[12,18]==blocked", rb["recovery_readiness"] == "blocked",
                   str([(c["id"], c["status"]) for c in rb["conditions"]])))
    rm = R.check_recovery_readiness(inc, (19.0, 25.0))
    checks.append(("A4b recovery[19,25]==conditions_met", rm["recovery_readiness"] == "conditions_met",
                   str([(c["id"], c["status"]) for c in rm["conditions"]])))

    npass = sum(1 for _, ok, _ in checks if ok)
    print(f"=== validate incident: {d} ===")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if (detail and not ok) else ""))
    print(f"--- {npass}/{len(checks)} passed ---")
    return 0 if npass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
