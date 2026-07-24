#!/usr/bin/env python3
"""PRIVATE, LOCAL-ONLY real-bag smoke test v2: a repeatable acceptance flow.

extract -> list_incident_candidates -> verify each candidate -> sanitized
summary + PASS/FAIL checklist (exit code 1 on any failure), so a private robot
bag has a repeatable local regression path.

This checks pipeline plumbing and honest degradation, NOT verdict ground truth:
verdict correctness is validated only on the synthetic scenarios
(tools/eval_extractor.py). Do not tune anything to push a real bag's verdict up.

Sanitized output contract — the summary and stdout contain ONLY:
  candidate count and windows, EVENT_* timestamps, verdict level/verdict,
  unavailable signals, missing evidence ids, structured warning counts, neutral
  metric names, and profile _status/_caveats COUNTS.
Never: topic names, raw log/diagnostic text, real source codes, frame ids. A
built-in self-check refuses to write a summary containing any string from the
profile (topics, msgtypes, matcher values).

Profile markers (local-only convention, ignored by the extractor): each role or
event may carry "_status": "confirmed" | "provisional" (missing means
provisional) and "_caveats": [ ... free text, never printed ... ]. Only counts
are reported.

DATA BOUNDARY — do not violate:
  - Run locally only. --out must live under private_eval/ (git-ignored; checked).
  - Never commit outputs, upload them, or route the bag/profile through any
    cloud/assistant/MCP tool.

Usage:
  python tools/private_eval_smoke.py --bag /path/real.bag \
      --profile private_eval/<case>/topic_mapping.local.json \
      --out private_eval/<case>/smoke_v2
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import extract_incident as EX
import incident_rules as R
import incident_spec as SP

checks = []


def chk(name, ok, detail=""):
    checks.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    return ok


def load_incident(out):
    md = json.loads((out / "metadata.json").read_text())
    tl = json.loads((out / "timeline.json").read_text())
    logs = [json.loads(x) for x in (out / "logs.jsonl").read_text().splitlines() if x.strip()]
    mbt = {round(m["t"], 1): m for m in tl["tracks"]["metrics"]}
    return R.Incident(out, md, tl, {}, logs, mbt)


def deny_strings(profile):
    """Every profile string that must never appear in sanitized output."""
    deny = set()
    for r in profile.get("roles", {}).values():
        for k in ("topic", "msgtype"):
            if r.get(k):
                deny.add(str(r[k]))
        ex = r.get("extract", {})
        for k in ("parent_frame", "child_frame"):  # real TF frame ids are private
            if ex.get(k):
                deny.add(str(ex[k]))
    for e in profile.get("events", {}).values():
        v = e.get("matcher", {}).get("value")
        if v:
            deny.add(str(v))
    return deny


def status_counts(items):
    return dict(Counter(v.get("_status", "provisional") for v in items.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True, type=Path)
    ap.add_argument("--profile", required=True, type=Path,
                    help="local topic mapping profile (*.local.json; never committed)")
    ap.add_argument("--out", required=True, type=Path, help="output dir under private_eval/")
    ap.add_argument("--spec", type=Path, default=ROOT / "specs" / "obstacle_stop.json")
    ap.add_argument("--conclusion", default="concl_obstacle_stop")
    args = ap.parse_args()

    out = args.out.resolve()
    priv = (ROOT / "private_eval").resolve()
    if priv != out and priv not in out.parents:
        sys.exit(f"--out must be under {priv} (git-ignored private area)")
    ignored = subprocess.run(["git", "check-ignore", "-q", str(out)], cwd=str(ROOT)).returncode == 0
    if not chk("output dir is git-ignored", ignored, str(out)):
        sys.exit(1)  # never write private output to a trackable location

    profile = json.loads(args.profile.read_text())
    spec = json.loads(args.spec.read_text())
    deny = deny_strings(profile)

    print("[smoke-v2] extracting (real content stays local)...")
    EX.run(args.bag, profile, out / "extract")
    inc = load_incident(out / "extract")
    chk("extraction completes", True)

    warnings = inc.metadata.get("warnings", [])
    chk("warnings are structured counts only",
        all(set(w) <= {"code", "role", "count"} for w in warnings), str(len(warnings)))
    chk("log codes are neutral EVENT_* only",
        all(str(lg.get("code", "")).startswith("EVENT_") for lg in inc.logs), str(len(inc.logs)))
    chk("no raw log text emitted", all(lg.get("message", "") == "" for lg in inc.logs))

    cands = SP.list_incident_candidates(spec, inc, args.conclusion)
    chk("list_incident_candidates count >= 1", len(cands) >= 1, str(len(cands)))

    results = []
    for cand in cands:
        ann = SP.compile_spec(spec, inc, args.conclusion, window_override=tuple(cand["window"]))
        ci = ann["compile_info"]
        es = R.evidence_strength(inc.replace(annotations=ann), args.conclusion, tuple(ci["window"]))
        results.append({"candidate_id": cand["candidate_id"], "event_t": cand["event_t"],
                        "window": ci["window"], "level": es["level"], "verdict": es["verdict"],
                        "unavailable_signals": ci["unavailable_signals"],
                        "forced_placeholders": ci["forced_placeholders"],
                        "missing": es["missing"]})
    chk("each candidate verified", len(results) == len(cands))
    chk("no dishonest high (high implies full positive evidence, no demotions)",
        all(r["level"] != "high" or (not r["unavailable_signals"] and not r["forced_placeholders"])
            for r in results))

    mnames = sorted({k for m in inc.timeline["tracks"]["metrics"] for k in m if k != "t"})
    summary = {
        "case": out.name,
        "profile_roles_status": status_counts(profile.get("roles", {})),
        "profile_events_status": status_counts(profile.get("events", {})),
        "profile_caveats_count": sum(len(v.get("_caveats", []))
                                     for d in (profile.get("roles", {}), profile.get("events", {}))
                                     for v in d.values()) + len(profile.get("_caveats", [])),
        "extraction": {"metric_rows": len(inc.timeline["tracks"]["metrics"]),
                       "metric_names": mnames, "warning_count": len(warnings),
                       "event_log": [{"t": lg["t"], "code": lg["code"]} for lg in inc.logs]},
        "candidates": results,
        "_note": "Sanitized. LOCAL ONLY — do not commit, upload, or send to any external service.",
    }
    blob = json.dumps(summary)
    leaked = sorted(d for d in deny if d in blob)
    if not chk("sanitization self-check: no profile strings in summary", not leaked,
               f"{len(leaked)} leaked strings (not shown)"):
        print("[smoke-v2] summary NOT written (would leak private strings)")
        return finish()
    (out / "summary.redacted.json").write_text(json.dumps(summary, indent=2))

    print("[smoke-v2] sanitized summary:")
    print(json.dumps(summary, indent=2))
    return finish()


def finish():
    ok = sum(1 for _, o in checks if o)
    print(f"--- {ok}/{len(checks)} smoke-v2 checks passed ---")
    sys.exit(0 if ok == len(checks) else 1)


if __name__ == "__main__":
    main()
