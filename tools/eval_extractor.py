#!/usr/bin/env python3
"""Spec eval on EXTRACTOR output.

Profile-driven extraction (no visualization) must feed the compiler + verifier to
the same verdicts as the synthetic scenarios — proving the neutral extractor path
is correct on the correctness oracle before any real bag is touched.

For each synthetic scenario: generate its bag -> extract neutral assets with the
synthetic profile -> compile the obstacle-stop spec -> run the UNCHANGED verifier
-> assert level/verdict. Run TWICE: declared-window mode (legacy fixed window) and
derived-window mode (search_window_strategy: window follows the stop event, falls
back to declared when the event is absent). Both must reproduce all verdicts.

Real-compatibility fixtures (mutated hero extract, derived window):
  - missing front_distance_m  -> the if_available source is unavailable; expect
    medium/ok (single distance source cannot reach high, does not fall to low).
  - no positive observation   -> zones all clear but stop event + halt present;
    expect low/ok (positive_required kills the stop+halt-only false positive,
    which would otherwise verify as high).
  - reversing, never halts    -> actual_speed_mps is a constant -0.2 (signed
    reverse); expect low/ok (velocity_halt declares abs, so a reverse velocity
    must not satisfy the <= 0.01 halt condition).

Generated assets are git-ignored (eval_build/).
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


def verify(inc, window_mode):
    """Compile + verify with the SAME window the compiler resolved."""
    ann = SP.compile_spec(SPEC, inc, CONC, window_mode=window_mode)
    win = tuple(ann["compile_info"]["window"])
    es = R.evidence_strength(inc.replace(annotations=ann), CONC, win)
    return ann, es


def mutate_metrics(inc, fn):
    """Deep-copied incident whose every timeline metric row went through fn."""
    tl = json.loads(json.dumps(inc.timeline))
    for m in tl["tracks"]["metrics"]:
        fn(m)
    mbt = {round(m["t"], 1): m for m in tl["tracks"]["metrics"]}
    return inc.replace(timeline=tl, metrics_by_t=mbt)


BUILD.mkdir(exist_ok=True)
incs = {name: build(name) for name in sorted(EXP)}
allok = True

for window_mode, title in (("declared", "declared-window"), ("auto", "derived-window")):
    print(f"=== spec eval on EXTRACTOR output [{title}] (verifier unchanged) ===")
    n_ok = 0
    for name in sorted(EXP):
        ann, es = verify(incs[name], window_mode)
        if window_mode == "auto":
            (BUILD / "extract" / name / "derived_annotations.json").write_text(json.dumps(ann, indent=2))
        want = EXP[name]
        ok = es["level"] == want["level"] and es["verdict"] == want["verdict"]
        allok &= ok
        n_ok += ok
        print(f"{name:24} window={ann['compile_info']['window_mode']:18} "
              f"expect={want['level']}/{want['verdict']:12} "
              f"got={es['level']}/{es['verdict']:12} {'PASS' if ok else 'FAIL'}")
        if not ok:
            print(f"    checks={es['checks']} conflicts={es.get('conflicts')} missing={es['missing']}")
    print(f"--- {n_ok}/{len(EXP)} scenarios passed ({title}) ---")

hero = incs["obstacle_stop"]


def _all_clear(m):
    for k in ("front_distance_m", "front_min_range_m"):
        if k in m:
            m[k] = 2.6


def _reversing(m):
    if "actual_speed_mps" in m:
        m["actual_speed_mps"] = -0.2


FIXTURES = [
    ("missing_front_distance_m", mutate_metrics(hero, lambda m: m.pop("front_distance_m", None)),
     {"level": "medium", "verdict": "ok"}),
    ("no_positive_observation", mutate_metrics(hero, _all_clear),
     {"level": "low", "verdict": "ok"}),
    ("reversing_never_halts", mutate_metrics(hero, _reversing),
     {"level": "low", "verdict": "ok"}),
]
print("=== real-compatibility fixtures (mutated hero extract; derived window) ===")
n_ok = 0
for fname, finc, want in FIXTURES:
    ann, es = verify(finc, "auto")
    ok = es["level"] == want["level"] and es["verdict"] == want["verdict"]
    allok &= ok
    n_ok += ok
    ci = ann["compile_info"]
    print(f"{fname:24} unavailable={ci['unavailable_signals']} forced={ci['forced_placeholders']}")
    print(f"{'':24} expect={want['level']}/{want['verdict']:12} "
          f"got={es['level']}/{es['verdict']:12} {'PASS' if ok else 'FAIL'}")
    if not ok:
        print(f"    checks={es['checks']} conflicts={es.get('conflicts')} missing={es['missing']}")
print(f"--- {n_ok}/{len(FIXTURES)} fixtures passed ---")

inc = hero.replace(annotations=SP.compile_spec(SPEC, hero, CONC))
rec_ok = True
for win, exp_state in [((12.0, 18.0), "blocked"), ((19.0, 25.0), "conditions_met")]:
    got = R.check_recovery_readiness(inc, win)["recovery_readiness"]
    r = got == exp_state
    rec_ok &= r
    allok &= r
    print(f"recovery {str(list(win)):14} expect={exp_state:16} got={got:16} {'PASS' if r else 'FAIL'}")
print(f"--- recovery {'2/2 PASS' if rec_ok else 'FAIL'} (extractor path) ---")

sys.exit(0 if allok else 1)
