#!/usr/bin/env python3
"""Spec eval on EXTRACTOR output.

Profile-driven extraction (no visualization) must feed the compiler + verifier to
the same verdicts as the synthetic scenarios — proving the neutral extractor path
is correct on the correctness oracle before any real bag is touched.

For each synthetic scenario: generate its bag -> extract neutral assets with the
synthetic profile -> compile the obstacle-stop spec -> run the UNCHANGED verifier
-> assert level/verdict. Generated assets are git-ignored (eval_build/).

Run: <venv>/python3 tools/eval_extractor.py
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
WINDOW = (9.5, 11.8)

sys.path.insert(0, str(TOOLS))
import incident_rules as R
import incident_spec as SP
import extract_incident as EX

SPEC = json.loads((ROOT / "specs" / "obstacle_stop.json").read_text())
PROFILE = json.loads((ROOT / "profiles" / "synthetic_demo.example.json").read_text())
CONC = "concl_obstacle_stop"
EXP = SPEC["expected"]["against"]


def gen(cfg, bag):
    env = {**os.environ, "SCENARIO_CONFIG": str(cfg)}
    subprocess.run([sys.executable, str(TOOLS / "generate_synthetic_bag.py"), str(bag)],
                   env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def load_incident(out):
    md = json.loads((out / "metadata.json").read_text())
    tl = json.loads((out / "timeline.json").read_text())
    logs = [json.loads(x) for x in (out / "logs.jsonl").read_text().splitlines() if x.strip()]
    mbt = {round(m["t"], 1): m for m in tl["tracks"]["metrics"]}
    return R.Incident(out, md, tl, {}, logs, mbt)


def build(name):
    cfg = SCEN / f"{name}.json"
    bag, out = BUILD / f"{name}.bag", BUILD / "extract" / name
    gen(cfg, bag)
    EX.run(bag, PROFILE, out)
    return load_incident(out)


BUILD.mkdir(exist_ok=True)
rows = []
allok = True
for name in sorted(EXP):
    inc = build(name)
    derived = SP.compile_spec(SPEC, inc, CONC)
    (BUILD / "extract" / name / "derived_annotations.json").write_text(json.dumps(derived, indent=2))
    es = R.evidence_strength(inc.replace(annotations=derived), CONC, WINDOW)
    want = EXP[name]
    ok = es["level"] == want["level"] and es["verdict"] == want["verdict"]
    allok &= ok
    rows.append((name, want, es, ok))

print("=== spec eval on EXTRACTOR output (profile-driven, no viz; verifier unchanged) ===")
for name, want, es, ok in rows:
    print(f"{name:24} expect={want['level']}/{want['verdict']:12} "
          f"got={es['level']}/{es['verdict']:12} {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"    checks={es['checks']} conflicts={es.get('conflicts')} missing={es['missing']}")
print(f"--- {sum(1 for r in rows if r[3])}/{len(rows)} scenarios passed (extractor path) ---")

inc = build("obstacle_stop")
inc = inc.replace(annotations=SP.compile_spec(SPEC, inc, CONC))
rec_ok = True
for win, exp_state in [((12.0, 18.0), "blocked"), ((19.0, 25.0), "conditions_met")]:
    got = R.check_recovery_readiness(inc, win)["recovery_readiness"]
    r = got == exp_state
    rec_ok &= r
    allok &= r
    print(f"recovery {str(list(win)):14} expect={exp_state:16} got={got:16} {'PASS' if r else 'FAIL'}")
print(f"--- recovery {'2/2 PASS' if rec_ok else 'FAIL'} (extractor path) ---")

sys.exit(0 if allok else 1)
