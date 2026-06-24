#!/usr/bin/env python3
"""Multi-scenario evaluation: prove the agent's deterministic layer DISCRIMINATES.

For each scenarios/*.json: generate its synthetic bag → export eval_incidents/<name>/ →
score the declared hypothesis with the shared rules → assert it matches the config's
"expected" verdict. Demonstrates the agent doesn't only narrate one scripted incident.

Live demo is unaffected (still reads incident/). Generated eval assets are gitignored.
Run: <venv>/python3 tools/eval_scenarios.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
SCEN = ROOT / "scenarios"
BUILD = ROOT / "eval_build"
EVAL = ROOT / "eval_incidents"
WINDOW = (9.5, 11.8)   # stop window used to score the obstacle hypothesis

sys.path.insert(0, str(TOOLS))
import incident_rules as R

BUILD.mkdir(exist_ok=True)
EVAL.mkdir(exist_ok=True)
rows = []


def run(script, *args, cfg):
    env = {**os.environ, "SCENARIO_CONFIG": str(cfg)}
    subprocess.run([sys.executable, str(TOOLS / script), *map(str, args)],
                   env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


for cfg_path in sorted(SCEN.glob("*.json")):
    cfg = json.loads(cfg_path.read_text())
    name = cfg_path.stem
    exp = cfg.get("expected", {})
    hyp = exp.get("hypothesis", "concl_obstacle_stop")
    want = exp.get("evidence_strength")
    bag = BUILD / f"{name}.bag"
    outdir = EVAL / name
    run("generate_synthetic_bag.py", bag, cfg=cfg_path)
    run("export_incident_assets.py", bag, outdir, cfg=cfg_path)
    inc = R.Incident.load(outdir)
    es = R.evidence_strength(inc, hyp, WINDOW)
    want_v = exp.get("verdict")                     # only checked when the config declares it
    got = es["level"]
    got_v = es.get("verdict")
    ok = (got == want) and (want_v is None or got_v == want_v)
    rows.append((name, want, got, want_v, got_v, ok, es))

print("=== scenario discrimination eval ===")
print(f"{'scenario':24} {'expect level/verdict':22} {'got level/verdict':22} result")
allok = True
for name, want, got, want_v, got_v, ok, es in rows:
    allok &= ok
    exp_s = f"{want or '-'}/{want_v or '-'}"
    got_s = f"{got}/{got_v or '-'}"
    print(f"{name:24} {exp_s:22} {got_s:22} {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"    checks={es['checks']} conflicts={es.get('conflicts')}")
print(f"--- {sum(1 for r in rows if r[5])}/{len(rows)} scenarios passed ---")
print("Pitch is only truthful when green: evaluated across a confirmed obstacle stop (high), "
      "a planned stop (true-negative, low), and conflicting sensor evidence (conflicting → insufficient).")
sys.exit(0 if allok else 1)
