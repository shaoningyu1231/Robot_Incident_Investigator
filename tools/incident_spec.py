#!/usr/bin/env python3
"""Compile a reusable incident spec into the annotations the verifier consumes.

specs/*.json declares HOW to observe evidence from data (signals + conclusion
structure); this compiler evaluates it against one incident's timeline/logs and
emits an annotations dict identical in shape to a hand-authored annotations.json.
tools/incident_rules.py (the deterministic verifier) is unchanged — only the
SOURCE of annotations changes from hand-authored to spec-derived.

Signal handling:
  - metric_threshold_state: ALWAYS emits one evidence. label = positive_label if
    the condition is observed inside the search window, else negative_label (a real
    negative observation). This is what turns sensor disagreement into a label
    conflict rather than a missing-evidence 'low'.
  - metric_threshold_crossing / log_event: emit real evidence when they fire; when
    absent, emit a PLACEHOLDER evidence anchored OUTSIDE the window (start - 1.0) so
    the verifier's required_evidence lookup does not KeyError and instead sees
    out_of_window -> required_present False -> low.
  - temporal_checks / metric_checks are emitted only for signals that actually
    fired; placeholders never participate in them.

See progress.md 'incident-metrics'.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import incident_rules as R  # reuse OPS / metric_at; verifier itself is untouched

EPS = 1e-9
PLACEHOLDER_OFFSET_S = 1.0  # placeholder t = search_window.start - this (out of window)


def _sorted_metrics(inc):
    return sorted(inc.timeline["tracks"]["metrics"], key=lambda m: m["t"])


def _first_true(metrics, name, op, thr, window, after_t=None, crossing=False):
    """First t inside window where OP(metric[name], thr). crossing => require the
    previous in-window sample to be false (a real transition)."""
    lo, hi = window
    prev_true = False
    for m in metrics:
        t = m["t"]
        if t < lo - EPS or t > hi + EPS:
            prev_true = False
            continue
        v = m.get(name)
        cur = v is not None and R.OPS[op](v, thr)
        if cur and (after_t is None or t >= after_t - EPS) and (not crossing or not prev_true):
            return t, v
        prev_true = cur
    return None, None


def _nearest_lidar_ref(inc, t):
    frames = inc.timeline["tracks"].get("lidar", [])
    if not frames:
        return ""
    return min(frames, key=lambda x: abs(x["t"] - t))["ref"]


def _ref_for(inc, modality, metric, code, t):
    if modality == "lidar":
        return _nearest_lidar_ref(inc, t)
    if modality == "metric":
        return ""  # metric evidence verifies via metric_at, not a viz file (viz is a separate layer)
    if modality == "log":
        return f"logs.jsonl#{code}"
    return ""


def compile_spec(spec, inc, conclusion_id):
    metrics = _sorted_metrics(inc)
    conc = next(c for c in spec["conclusions"] if c["id"] == conclusion_id)
    lo, hi = conc["search_window"]
    window = (lo, hi)
    placeholder_t = round(lo - PLACEHOLDER_OFFSET_S, 3)
    sigs = spec["signals"]

    # --- resolve each signal's fired state + raw anchor (recursive for after: deps) ---
    resolved = {}

    def resolve(name):
        if name in resolved:
            return resolved[name]
        s = sigs[name]
        kind = s["type"]
        strat = s.get("anchor_strategy", "first")
        after_t = None
        if isinstance(strat, str) and strat.startswith("first_true_after:"):
            dep = resolve(strat.split(":", 1)[1])
            after_t = dep["anchor"] if dep["fired"] else hi + 1e9  # dep absent -> block match
        crossing = strat == "first_crossing"
        if kind in ("metric_threshold_state", "metric_threshold_crossing"):
            t, v = _first_true(metrics, s["metric"], s["op"], s["threshold"], window,
                               after_t=after_t, crossing=crossing)
            resolved[name] = {"fired": t is not None, "anchor": t, "value": v, "sig": s}
        elif kind == "log_event":
            hit = next((lg for lg in inc.logs
                        if lg.get("code") == s["code"] and lo - EPS <= lg["t"] <= hi + EPS), None)
            resolved[name] = {"fired": hit is not None, "anchor": hit["t"] if hit else None,
                              "value": None, "sig": s}
        else:
            raise ValueError(f"unknown signal type {kind}")
        return resolved[name]

    for name in sigs:
        resolve(name)
    fired = {n: r["fired"] for n, r in resolved.items()}
    groups = conc.get("corroboration_groups", [])

    def peer_anchor(name):
        """Anchor a negative observation to a fired peer in its corroboration group
        (so clear vs obstacle land in the same window), else the window midpoint."""
        for grp in groups:
            if name in grp:
                for other in grp:
                    if other != name and resolved[other]["fired"]:
                        return resolved[other]["anchor"]
        return round((lo + hi) / 2.0, 3)

    # --- emit evidence ---
    evidence = []
    for name, r in resolved.items():
        s = r["sig"]
        mod = s["modality"]
        if s["type"] == "metric_threshold_state":
            if r["fired"]:
                t, label = r["anchor"], s["positive_label"]
            else:
                t, label = peer_anchor(name), s["negative_label"]
            ev = {"id": name, "modality": mod, "t": t,
                  "ref": _ref_for(inc, mod, s.get("metric"), None, t), "object_label": label}
            if s.get("metric"):
                ev["metric"] = {"name": s["metric"], "value": R.metric_at(inc, s["metric"], t)}
            evidence.append(ev)
        elif r["fired"]:
            t = r["anchor"]
            ev = {"id": name, "modality": mod, "t": t,
                  "ref": _ref_for(inc, mod, s.get("metric"), s.get("code"), t)}
            if mod == "metric" and s.get("metric"):
                ev["metric"] = {"name": s["metric"], "value": R.metric_at(inc, s["metric"], t)}
            if mod == "log":
                ev["code"] = s["code"]
            evidence.append(ev)
        else:  # absent crossing/log signal -> out-of-window placeholder (required_present -> False)
            ev = {"id": name, "modality": mod, "t": placeholder_t,
                  "ref": _ref_for(inc, mod, s.get("metric"), s.get("code"), placeholder_t),
                  "expected_observation": "Signal not observed in the hypothesis window."}
            if mod == "log":
                ev["code"] = s["code"]
            evidence.append(ev)

    # --- conclusion: keep only checks whose signals fired ---
    temporal = [tc for tc in conc.get("temporal_checks", [])
                if fired.get(tc["before"]) and fired.get(tc["after"])]
    metric_checks = []
    for mc in conc.get("metric_checks", []):
        if fired.get(mc["signal"]):
            s = sigs[mc["signal"]]
            metric_checks.append({"name": s["metric"], "op": s["op"],
                                  "threshold": s["threshold"], "evidence_id": mc["signal"]})

    out_conc = {"id": conc["id"], "statement": conc.get("statement", ""),
                "required_evidence": list(conc["required_signals"]),
                "corroboration_groups": groups,
                "temporal_checks": temporal, "metric_checks": metric_checks}

    return {"incident_id": inc.metadata.get("incident_id"),
            "evidence": evidence, "conclusions": [out_conc],
            "stateful_events": spec.get("stateful_events", []),
            "recovery": spec.get("recovery", {"conditions": []})}
