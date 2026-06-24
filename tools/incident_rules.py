"""事故调查的确定性规则核心 —— validator 与 FastAPI 后端共用同一实现。

只读 incident/ 下的合成资产。所有 evidence_strength / recovery / inspect 逻辑都在这里,
不在别处复制。规则细节见 ../schema.md。
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

EPS = 1e-9
OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
       ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
       "==": lambda a, b: a == b}
NUMERIC_METRICS = ["planner_speed_mps", "applied_speed_mps", "actual_speed_mps", "front_distance_m"]


@dataclass
class Incident:
    dir: Path
    metadata: dict
    timeline: dict
    annotations: dict
    logs: list
    metrics_by_t: dict  # round(t,1) -> metric dict

    @classmethod
    def load(cls, d):
        d = Path(d)
        md = json.loads((d / "metadata.json").read_text())
        tl = json.loads((d / "timeline.json").read_text())
        ann = json.loads((d / "annotations.json").read_text())
        logs = [json.loads(l) for l in (d / "logs.jsonl").read_text().splitlines() if l.strip()]
        metrics_by_t = {round(m["t"], 1): m for m in tl["tracks"]["metrics"]}
        return cls(d, md, tl, ann, logs, metrics_by_t)

    def replace(self, **kw):
        return dataclasses.replace(self, **kw)


# ---------- 基础 ----------
def metric_at(inc, name, t):
    m = inc.metrics_by_t.get(round(t, 1))
    return None if m is None else m.get(name)


def infer_dt(metrics_by_t):
    ts = sorted(metrics_by_t.keys())
    diffs = [round(b - a, 3) for a, b in zip(ts, ts[1:]) if b - a > EPS]
    return min(diffs) if diffs else 0.1


def asset_exists(inc, ev):
    """按 modality 核对证据对应的实际资产是否存在。"""
    ref = ev.get("ref", "")
    if ref.startswith("lidar_frames/") or ref.startswith("charts/"):
        if not (inc.dir / ref).exists():
            return False, "ref_file_missing"
    mod = ev["modality"]
    if mod == "lidar":
        return (inc.dir / ref).exists(), "lidar_frame"
    if mod == "metric":
        name = ev.get("metric", {}).get("name")
        if name is None or metric_at(inc, name, ev["t"]) is None:
            return False, "metric_sample_missing"
        return True, "metric_sample"
    if mod == "log":
        code = ev.get("code")
        if not any(l.get("code") == code for l in inc.logs):
            return False, "log_entry_missing"
        return True, "log_entry"
    return True, "unknown_modality"


# ---------- evidence_strength ----------
def evidence_strength(inc, conclusion_id, window):
    start, end = window
    ev = {e["id"]: e for e in inc.annotations["evidence"]}
    conc = next(c for c in inc.annotations["conclusions"] if c["id"] == conclusion_id)
    default_skew = inc.metadata["synchronization"]["default_max_skew_s"]
    missing = []

    required_present = True
    for eid in conc["required_evidence"]:
        e = ev[eid]
        if not (start - EPS <= e["t"] <= end + EPS):
            required_present = False
            missing.append(f"{eid}:out_of_window")
            continue
        ok, why = asset_exists(inc, e)
        if not ok:
            required_present = False
            missing.append(f"{eid}:{why}")

    time_aligned = True
    for grp in conc.get("corroboration_groups", []):
        ts = [ev[g]["t"] for g in grp]
        if max(ts) - min(ts) > default_skew + EPS:
            time_aligned = False
            missing.append("skew:" + "/".join(grp))
    for tc in conc.get("temporal_checks", []):
        delay = ev[tc["after"]]["t"] - ev[tc["before"]]["t"]
        if not (tc["min_delay_s"] - EPS <= delay <= tc["max_delay_s"] + EPS):
            time_aligned = False
            missing.append(f"temporal:{tc['before']}->{tc['after']}")

    labels_ok = True
    for grp in conc.get("corroboration_groups", []):
        labels = {ev[g].get("object_label") for g in grp}
        if len(labels) != 1 or None in labels:
            labels_ok = False
            missing.append("label:" + "/".join(grp))

    metrics_ok = True
    for mc in conc.get("metric_checks", []):
        val = metric_at(inc, mc["name"], ev[mc["evidence_id"]]["t"])
        if val is None or not OPS[mc["op"]](val, mc["threshold"]):
            metrics_ok = False
            missing.append("metric:" + mc["name"])

    others = [time_aligned, labels_ok, metrics_ok]
    if required_present and all(others):
        level = "high"
    elif required_present and others.count(False) == 1:
        level = "medium"
    else:
        level = "low"
    checks = {"required_present": required_present, "time_aligned": time_aligned,
              "labels_corroborate": labels_ok, "metrics_crossed": metrics_ok}

    # Conflict detection (distinct from "missing"): within a corroboration group, if >=2 members
    # are BOTH present (in-window + asset exists + non-null label) and their labels explicitly
    # disagree, the sensors contradict each other → verdict "conflicting". Mere absence is NOT a
    # conflict (it stays "low" via required_present) — otherwise normal missing-evidence windows
    # would be mislabelled.
    conflicts = []
    for grp in conc.get("corroboration_groups", []):
        present = [ev[g] for g in grp
                   if (start - EPS <= ev[g]["t"] <= end + EPS)
                   and asset_exists(inc, ev[g])[0]
                   and ev[g].get("object_label") is not None]
        labels = sorted({m["object_label"] for m in present})
        if len(present) >= 2 and len(labels) >= 2:
            conflicts.append({"group": grp, "labels": labels})
    verdict = "conflicting" if conflicts else "ok"

    return {"conclusion_id": conclusion_id, "level": level, "verdict": verdict,
            "conflicts": conflicts, "checks": checks, "missing": missing,
            "note": "Reflects evidence completeness and consistency, NOT probability the root cause is correct. "
                    "verdict='conflicting' means sensors explicitly contradict (maps to insufficient_evidence)."}


# ---------- recovery ----------
def check_recovery_readiness(inc, evaluation_window):
    start, end = evaluation_window
    metrics = sorted(inc.metrics_by_t.values(), key=lambda m: m["t"])
    dt = infer_dt(inc.metrics_by_t)
    stateful = inc.annotations["stateful_events"]
    results = []
    for cond in inc.annotations["recovery"]["conditions"]:
        chk = cond["check"]
        observed = {}
        if "metric" in chk:
            agg = chk.get("aggregation")
            if agg != "continuous_at_end":
                raise ValueError(f"unsupported aggregation {agg}")
            dur = chk["duration_s"]
            sub = sorted([m for m in metrics if end - dur - EPS <= m["t"] <= end + EPS], key=lambda m: m["t"])
            sts = [m["t"] for m in sub]
            covered = (bool(sub)
                       and min(sts) <= end - dur + dt + EPS
                       and max(sts) >= end - dt - EPS
                       and all(b - a <= 1.5 * dt + EPS for a, b in zip(sts, sts[1:])))
            missing_metric = any(m.get(chk["metric"]) is None for m in sub)
            if not covered or missing_metric:
                status = "unknown"
            else:
                status = "met" if all(OPS[chk["op"]](m[chk["metric"]], chk["threshold"]) for m in sub) else "unmet"
            if sub:
                observed = {chk["metric"]: sub[-1].get(chk["metric"])}
        elif "event_state" in chk:
            code = chk["event_state"]
            clear_codes = {se.get("clears") for se in stateful if se.get("code") == code and se.get("clears")}
            events = []
            for lg in inc.logs:
                if lg["t"] > end + EPS:
                    continue
                if lg["code"] == code:
                    events.append((lg["t"], "active"))
                elif lg["code"] in clear_codes:
                    events.append((lg["t"], "cleared"))
            events.sort()
            state = events[-1][1] if events else "unknown"
            status = "met" if state == chk["must_be"] else ("unknown" if state == "unknown" else "unmet")
            observed = {"event_state": state}
        else:
            raise ValueError("unknown check")
        results.append({"id": cond["id"], "label": cond.get("label", ""), "status": status, "observed": observed})

    if any(r["status"] == "unmet" for r in results):
        readiness = "blocked"
    elif any(r["status"] == "unknown" for r in results):
        readiness = "insufficient_evidence"
    else:
        readiness = "conditions_met"
    return {"incident_id": inc.metadata["incident_id"], "evaluation_window": list(evaluation_window),
            "recovery_readiness": readiness, "conditions": results,
            "note": "Recovery-condition check only. NOT a safety certification."}


# ---------- search_logs ----------
def search_logs(inc, query=None, code=None, node=None, start=None, end=None, max_matches=100):
    out = []
    for lg in inc.logs:
        if start is not None and lg["t"] < start - EPS:
            continue
        if end is not None and lg["t"] > end + EPS:
            continue
        if code is not None and lg.get("code") != code:
            continue
        if node is not None and lg.get("node") != node:
            continue
        if query is not None:
            hay = f"{lg.get('code','')} {lg.get('message','')} {lg.get('node','')}".lower()
            if query.lower() not in hay:
                continue
        out.append(lg)
        if len(out) >= max_matches:
            break
    return {"matches": out, "count": len(out)}


# ---------- inspect_incident_window ----------
def inspect_incident_window(inc, start, end, modalities=None, conclusion_id=None, reason=None):
    mods = modalities or inc.metadata.get("modalities", ["lidar", "metrics", "log"])
    lidar = [{"t": x["t"], "ref": x["ref"]} for x in inc.timeline["tracks"].get("lidar", [])
             if start - EPS <= x["t"] <= end + EPS]
    charts = [dict(x) for x in inc.timeline["tracks"].get("charts", [])]
    logs = [lg for lg in inc.logs if start - EPS <= lg["t"] <= end + EPS]
    win = [m for m in inc.metrics_by_t.values() if start - EPS <= m["t"] <= end + EPS]
    win.sort(key=lambda m: m["t"])

    metrics = {}
    if win:
        for k in NUMERIC_METRICS:
            vals = [m[k] for m in win if m.get(k) is not None]
            if vals:
                metrics[k] = {"min": round(min(vals), 3), "max": round(max(vals), 3),
                              "at_end": round(win[-1].get(k), 3)}
        ss = [m["safety_state"] for m in win if "safety_state" in m]
        if ss:
            metrics["safety_state"] = {"values": sorted(set(ss)), "at_end": win[-1]["safety_state"]}

    present = []
    if "lidar" in mods and lidar:
        present.append("lidar")
    if "metrics" in mods and win:
        present.append("metrics")
    if "log" in mods and logs:
        present.append("log")
    missing = [m for m in mods if m not in present]

    result = {
        "window": {"start": start, "end": end},
        "reason": reason,
        "lidar": [{**x, "uri": f"/media/{x['ref']}"} for x in lidar],
        "charts": [{**x, "uri": f"/media/{x['ref']}"} for x in charts],
        "logs": logs,
        "metrics": metrics,
        "present_modalities": present,
        "missing_modalities": missing,
    }
    if conclusion_id:
        result["evidence_strength"] = evidence_strength(inc, conclusion_id, (start, end))
    return result


# ---------- 资产完整性(启动/校验共用)----------
def integrity_checks(inc):
    d = inc.dir
    checks = []
    for f in ["metadata.json", "timeline.json", "annotations.json", "logs.jsonl"]:
        checks.append((f"file:{f}", (d / f).exists(), ""))
    for c in ["front_distance.png", "velocity.png", "safety_state.png"]:
        checks.append((f"chart:{c}", (d / "charts" / c).exists(), ""))
    nfr = len(list((d / "lidar_frames").glob("*.png")))
    checks.append(("lidar_frames>0", nfr > 0, f"{nfr} frames"))
    miss_ref = [x["ref"] for x in inc.timeline["tracks"]["lidar"] if not (d / x["ref"]).exists()]
    checks.append(("timeline lidar refs exist", not miss_ref, f"missing={miss_ref[:3]}"))
    ann_refs = [e["ref"] for e in inc.annotations["evidence"] if e["ref"].startswith("lidar_frames/")]
    miss_ann = [r for r in ann_refs if not (d / r).exists()]
    checks.append(("annotation lidar refs exist", not miss_ann, f"missing={miss_ann}"))
    desc = inc.metadata.get("description", "")
    checks.append(("metadata.description 不提 105/error code",
                   "105" not in desc and "error" not in desc.lower(), repr(desc)))
    checks.append(("recovery.conditions 无 data_window",
                   all("data_window" not in c for c in inc.annotations["recovery"]["conditions"]), ""))
    rq = inc.annotations["conclusions"][0]["required_evidence"]
    checks.append(("required_evidence 含 ev_velocity_halt", "ev_velocity_halt" in rq, str(rq)))
    return checks
