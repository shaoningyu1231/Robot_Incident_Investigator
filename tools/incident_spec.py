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

Real-compatible compilation (spec 0.2):
  - search_window_strategy {kind: "around_log_event", code, pre_s, post_s} derives
    the window from the incident's own logs ([t - pre_s, t + post_s] around the
    FIRST occurrence of the code); no occurrence -> fall back to the declared
    search_window. window_mode="declared" ignores the strategy (legacy fixed
    window); an explicit window_override wins over both.
  - required_signals entries may be {"id", "required": "always"|"if_available"}.
    if_available + metric absent from EVERY timeline row (this robot cannot
    provide the source at all) -> dropped from required_evidence but kept in its
    corroboration group as a label-less "source unavailable" evidence, so
    labels_corroborate fails and the verifier naturally caps the level at medium
    (single source, corroboration declared but unavailable). A metric that IS
    present but never fires stays a real negative observation — never skipped.
  - conclusions may declare positive_required groups {any_of, placeholder}: if NO
    member observed its positive label, the placeholder signal is emitted as an
    out-of-window placeholder -> required_present False -> low. A conclusion
    asserting an obstacle needs at least one positive observation; negative
    (clear) observations corroborate or conflict, they never satisfy it. This
    closes the false positive where stop event + halt + all-clear sensors could
    reach high.

See progress.md 'incident-metrics' and 'real-compatible spec compilation'.
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


def _conc(spec, conclusion_id):
    return next(c for c in spec["conclusions"] if c["id"] == conclusion_id)


def _required_entries(conc):
    """Normalize required_signals: plain string == {"id": s, "required": "always"}."""
    return [{"id": e, "required": "always"} if isinstance(e, str)
            else {"id": e["id"], "required": e.get("required", "always")}
            for e in conc["required_signals"]]


def resolve_search_window(spec, inc, conclusion_id, window_mode="auto", window_override=None):
    """Return ((lo, hi), mode). mode: override | derived | declared | declared_fallback."""
    conc = _conc(spec, conclusion_id)
    declared = tuple(conc["search_window"])
    if window_override is not None:
        return tuple(window_override), "override"
    strat = conc.get("search_window_strategy")
    if window_mode == "declared" or not strat:
        return declared, "declared"
    if strat["kind"] != "around_log_event":
        raise ValueError(f"unknown search_window_strategy kind {strat['kind']}")
    hits = [lg["t"] for lg in inc.logs if lg.get("code") == strat["code"]]
    if not hits:
        return declared, "declared_fallback"
    t = min(hits)
    return (round(t - strat["pre_s"], 3), round(t + strat["post_s"], 3)), "derived"


def _first_true(metrics, name, op, thr, window, after_t=None, crossing=False, use_abs=False):
    """First t inside window where OP(metric[name], thr). crossing => require the
    previous in-window sample to be false (a real transition). use_abs compares
    |value| (a signed velocity in reverse must not satisfy a halt's <=)."""
    lo, hi = window
    prev_true = False
    for m in metrics:
        t = m["t"]
        if t < lo - EPS or t > hi + EPS:
            prev_true = False
            continue
        v = m.get(name)
        cur = v is not None and R.OPS[op](abs(v) if use_abs else v, thr)
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


def compile_spec(spec, inc, conclusion_id, window_mode="auto", window_override=None):
    metrics = _sorted_metrics(inc)
    conc = _conc(spec, conclusion_id)
    (lo, hi), win_mode = resolve_search_window(spec, inc, conclusion_id,
                                               window_mode=window_mode,
                                               window_override=window_override)
    window = (lo, hi)
    placeholder_t = round(lo - PLACEHOLDER_OFFSET_S, 3)
    sigs = spec["signals"]

    # if_available demotion: only when the metric is absent from EVERY row —
    # present-but-never-firing is a real observation and stays fully required.
    req_entries = _required_entries(conc)
    unavailable = {e["id"] for e in req_entries
                   if e["required"] == "if_available"
                   and sigs[e["id"]].get("metric")
                   and not any(m.get(sigs[e["id"]]["metric"]) is not None for m in metrics)}

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
                               after_t=after_t, crossing=crossing, use_abs=s.get("abs", False))
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

    # positive_required: an unavailable member cannot fire, so it never satisfies
    # the group; the placeholder target degrades to out-of-window -> low.
    forced_placeholder = set()
    for grp in conc.get("positive_required", []):
        if not any(resolved[n]["fired"] for n in grp["any_of"]):
            forced_placeholder.add(grp["placeholder"])

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
            if name in unavailable:
                # source cannot be provided by this robot: label-less group member
                # (labels_corroborate fails -> capped at medium), never a fake "clear"
                evidence.append({"id": name, "modality": mod, "t": peer_anchor(name), "ref": "",
                                 "metric": {"name": s["metric"], "value": None},
                                 "expected_observation":
                                     "Corroborating source unavailable: metric absent from this dataset."})
                continue
            if name in forced_placeholder:
                evidence.append({"id": name, "modality": mod, "t": placeholder_t, "ref": "",
                                 "expected_observation":
                                     "No positive observation in the hypothesis window."})
                continue
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
                "required_evidence": [e["id"] for e in req_entries if e["id"] not in unavailable],
                "corroboration_groups": groups,
                "temporal_checks": temporal, "metric_checks": metric_checks}

    return {"incident_id": inc.metadata.get("incident_id"),
            "compile_info": {"window": [lo, hi], "window_mode": win_mode,
                             "unavailable_signals": sorted(unavailable),
                             "forced_placeholders": sorted(forced_placeholder)},
            "evidence": evidence, "conclusions": [out_conc],
            "stateful_events": spec.get("stateful_events", []),
            "recovery": spec.get("recovery", {"conditions": []})}
