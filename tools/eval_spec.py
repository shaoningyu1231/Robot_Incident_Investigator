#!/usr/bin/env python3
"""Spec-derived eval: ONE obstacle_stop spec compiled against three synthetic
datasets must reproduce the current verdicts — proving evidence verification no
longer needs per-incident hand-authored annotations.

For each scenarios/*.json named in the spec's expected.against: build the dataset
(generate + export), compile specs/obstacle_stop.json against it into
derived_annotations.json, run the UNCHANGED verifier on the derived annotations,
and assert level/verdict. Generated assets are gitignored.

Run: <venv>/python3 tools/eval_spec.py
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
WINDOW = (9.5, 11.8)

sys.path.insert(0, str(TOOLS))
import incident_rules as R
import incident_spec as SP

SPEC = json.loads((ROOT / "specs" / "obstacle_stop.json").read_text())
CONC = "concl_obstacle_stop"
EXP = SPEC["expected"]["against"]


def run(script, *args, cfg):
    env = {**os.environ, "SCENARIO_CONFIG": str(cfg)}
    subprocess.run([sys.executable, str(TOOLS / script), *map(str, args)],
                   env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


BUILD.mkdir(exist_ok=True)
EVAL.mkdir(exist_ok=True)
rows = []
allok = True

for cfg_path in sorted(SCEN.glob("*.json")):
    name = cfg_path.stem
    if name not in EXP:
        continue
    outdir = EVAL / name
    run("generate_synthetic_bag.py", BUILD / f"{name}.bag", cfg=cfg_path)
    run("export_incident_assets.py", BUILD / f"{name}.bag", outdir, cfg=cfg_path)
    inc = R.Incident.load(outdir)
    derived = SP.compile_spec(SPEC, inc, CONC)
    (outdir / "derived_annotations.json").write_text(json.dumps(derived, indent=2))
    es = R.evidence_strength(inc.replace(annotations=derived), CONC, WINDOW)
    want = EXP[name]
    ok = es["level"] == want["level"] and es["verdict"] == want["verdict"]
    allok &= ok
    rows.append((name, want, es, ok))

print("=== spec-derived discrimination (one spec, three datasets, verifier unchanged) ===")
for name, want, es, ok in rows:
    print(f"{name:24} expect={want['level']}/{want['verdict']:12} "
          f"got={es['level']}/{es['verdict']:12} {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"    checks={es['checks']} conflicts={es.get('conflicts')} missing={es['missing']}")
print(f"--- {sum(1 for r in rows if r[3])}/{len(rows)} scenarios passed (spec-derived) ---")

# recovery on the obstacle hero, using spec-derived recovery conditions
inc = R.Incident.load(EVAL / "obstacle_stop")
inc = inc.replace(annotations=SP.compile_spec(SPEC, inc, CONC))
rec_ok = True
for win, exp_state in [((12.0, 18.0), "blocked"), ((19.0, 25.0), "conditions_met")]:
    got = R.check_recovery_readiness(inc, win)["recovery_readiness"]
    r = got == exp_state
    rec_ok &= r
    allok &= r
    print(f"recovery {str(list(win)):14} expect={exp_state:16} got={got:16} {'PASS' if r else 'FAIL'}")
print(f"--- recovery {'2/2 PASS' if rec_ok else 'FAIL'} (spec-derived) ---")

sys.exit(0 if allok else 1)
