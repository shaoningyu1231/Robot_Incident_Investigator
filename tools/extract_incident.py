#!/usr/bin/env python3
"""Profile-driven incident extractor (Phase 2; synthetic gate first).

Reads a rosbag + a topic-mapping profile and produces the NEUTRAL intermediate
representation the spec compiler consumes: timeline.json (metrics) + logs.jsonl
(abstract events) + metadata.json. Renders NO visualization. Verification runs on
scalars + abstract events only.

    bag + profile -> extract_incident -> timeline.json / logs.jsonl / metadata.json
                     incident_spec.py (compile) -> derived annotations
                     incident_rules.py (verify, unchanged)

Metrics: connection-filtered read; a topic may back several roles; explicit
floor-bucket resampling with per-metric aggregation (front_min_range=min,
scalar_field=last, tf_jump=max — a single-sample jump must survive bucketing).
metadata.resample records raw/valid/invalid/output + coverage.

tf_jump: per-sample discontinuity of ONE parent->child transform in a
tf2_msgs/msg/TFMessage stream — translation delta (output_metric) and optional
yaw delta (yaw_output_metric) between consecutive samples. Frame names come from
the profile (private-safe); thresholds live in the incident spec, not here.

Events (unified schema): profile.events maps each ABSTRACT event to
  {source_role, matcher:{kind, ...}, transition: assert|clear, output_code, emit}
The matcher (json_string_event | rosout_text | diagnostic_status; ops exact |
contains) decides a match on the private source; the extractor emits ONLY the
abstract output_code — never the raw log text, diagnostic name, or real code.
`transition` is declared, not guessed. `emit: edge` or `deduplicate_window_s`
collapses repeated state republishes.

warnings are structured counts ({code, role?, count}) with no real names/text.
Missing role/topic, unsupported kind, or a bad message degrade gracefully.

Usage:
  python tools/extract_incident.py --bag in.bag \\
    --profile profiles/synthetic_demo.example.json --out out_dir
"""
import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_types_from_msg, get_typestore

TS = get_typestore(Stores.ROS1_NOETIC)
# tf2_msgs/TFMessage is absent from the stock ROS1 store; real bags carry it on /tf.
TS.register(get_types_from_msg("geometry_msgs/TransformStamped[] transforms",
                               "tf2_msgs/msg/TFMessage"))
DEFAULT_AGG = {"front_min_range": "min", "scalar_field": "last", "tf_jump": "max"}
MATCHER_KINDS = {"json_string_event", "rosout_text", "diagnostic_status"}
BUCKET_EPS = 1e-6


def _get_field(msg, path):
    obj = msg
    for part in path.split("."):
        obj = getattr(obj, part)
    return float(obj)


def _front_min_range(ranges, angle_min, angle_inc, ex):
    half = math.radians(ex.get("front_sector_deg", 15.0))
    axis = ex.get("front_axis_rad", 0.0)
    rmin, rmax = ex.get("range_min_m", 0.0), ex.get("range_max_m", 1e9)
    best = None
    for i, r in enumerate(ranges):
        r = float(r)
        if r < rmin or r > rmax or math.isinf(r) or math.isnan(r):
            continue
        if abs((angle_min + i * angle_inc) - axis) <= half:
            if best is None or r < best:
                best = r
    return best


def _tf_jump(msg, ex, prev):
    """Translation / yaw delta between consecutive samples of one parent->child
    transform. Returns (d_translation_m, d_yaw_rad); (None, None) until two
    samples of the matching transform have been seen."""
    parent = ex["parent_frame"].lstrip("/")
    child = ex["child_frame"].lstrip("/")
    for tr in getattr(msg, "transforms", []):
        if getattr(tr.header, "frame_id", "").lstrip("/") != parent \
                or getattr(tr, "child_frame_id", "").lstrip("/") != child:
            continue
        tl = tr.transform.translation
        q = tr.transform.rotation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        last = prev.get("last")
        prev["last"] = (float(tl.x), float(tl.y), float(tl.z), yaw)
        if last is None:
            return None, None
        d = math.dist(last[:3], prev["last"][:3])
        dyaw = abs(math.atan2(math.sin(yaw - last[3]), math.cos(yaw - last[3])))
        return d, dyaw
    return None, None


def _aggregate(samples, how, center):
    if how == "min":
        return min(v for _, v in samples)
    if how == "max":
        return max(v for _, v in samples)
    if how == "mean":
        return sum(v for _, v in samples) / len(samples)
    if how == "nearest":
        return min(samples, key=lambda s: abs(s[0] - center))[1]
    return samples[-1][1]  # "last" (default)


def _bucketize(raw, agg_of, bucket_s):
    """Floor bucketing by integer index (robust to 0.1 being inexact); any rate."""
    buckets = {}
    for om, samples in raw.items():
        for (t, v) in samples:
            idx = int(math.floor(t / bucket_s + BUCKET_EPS))
            buckets.setdefault(idx, {}).setdefault(om, []).append((t, v))
    out_count = defaultdict(int)
    rows = []
    for idx in sorted(buckets):
        bt = round(idx * bucket_s, 3)
        row = {"t": bt}
        for om, ss in buckets[idx].items():
            ss.sort(key=lambda x: x[0])
            row[om] = round(_aggregate(ss, agg_of.get(om, "last"), bt + bucket_s / 2), 3)
            out_count[om] += 1
        rows.append(row)
    return rows, out_count


def _op(op, actual, value):
    if op == "exact":
        return actual == value
    if op == "contains":
        return value in str(actual) if actual is not None else False
    return False  # unbounded regex intentionally unsupported in v1


def _apply_matcher(kind, msg, m, warn):
    """Return True if `msg` matches. Never returns or emits raw content."""
    op = m.get("op", "exact")
    if kind == "json_string_event":
        try:
            payload = json.loads(msg.data)
        except (ValueError, TypeError, AttributeError):
            warn("event_parse_failed")
            return False
        return _op(op, payload.get(m.get("field", "code")), m.get("value"))
    if kind == "rosout_text":
        if "level_min" in m and int(getattr(msg, "level", 0)) < m["level_min"]:
            return False
        if "value" not in m:
            return True                       # level-only match
        return _op(m.get("op", "contains"), getattr(msg, "msg", ""), m["value"])
    if kind == "diagnostic_status":
        field = m.get("field", "name")
        for s in getattr(msg, "status", []):
            if "level_min" in m and int(getattr(s, "level", 0)) < m["level_min"]:
                continue
            if "value" not in m or _op(m.get("op", "contains"), getattr(s, field, ""), m["value"]):
                return True
        return False
    return False


def run(bag_path, profile, out_dir):
    roles = profile["roles"]
    topic_roles = defaultdict(list)
    for r in roles.values():
        topic_roles[r["topic"]].append(r)
    rate_hz = float(profile.get("resample", {}).get("rate_hz", 10.0))
    bucket_s = 1.0 / rate_hz

    warn_counts = Counter()

    def warn(code, role=None):
        warn_counts[(code, role)] += 1

    # --- validate events (unique output_code, source_role exists, matcher kind supported) ---
    valid_events = []   # (name, ev) in profile order -> deterministic multi-match
    seen_codes = set()
    for name, ev in profile.get("events", {}).items():
        oc, sr = ev.get("output_code"), ev.get("source_role")
        mk = ev.get("matcher", {}).get("kind")
        if oc in seen_codes:
            warn("duplicate_output_code"); continue
        if sr not in roles:
            warn("missing_source_role", role=name); continue
        if mk not in MATCHER_KINDS:
            warn("unsupported_matcher_kind"); continue
        seen_codes.add(oc)
        valid_events.append((name, ev))
    ev_topic = defaultdict(list)
    for name, ev in valid_events:
        ev_topic[roles[ev["source_role"]]["topic"]].append((name, ev))
    ev_state = {name: {"last_match": False, "last_emit": None} for name, _ in valid_events}

    metric_topics = {r["topic"] for r in roles.values() if r.get("extract")}
    event_topics = set(ev_topic)
    want = metric_topics | event_topics

    raw = defaultdict(list)
    raw_count = defaultdict(int)
    agg_of = {}
    logs = []
    t0 = t1 = None
    tf_prev = defaultdict(dict)  # per-output-metric last transform (tf_jump state)

    with Reader(bag_path) as r:
        present = {c.topic for c in r.connections}
        for topic in want - present:                     # warn+skip: mapped topic absent from bag
            role_name = next((n for n, rr in roles.items() if rr["topic"] == topic), None)
            warn("missing_role_connection", role=role_name)
        start = r.start_time
        conns = [c for c in r.connections if c.topic in want]
        for c, ts, rawdata in r.messages(connections=conns):
            t = (ts - start) / 1e9
            t0 = t if t0 is None else min(t0, t)
            t1 = t if t1 is None else max(t1, t)
            topic = c.topic
            ex_roles = [rr for rr in topic_roles.get(topic, [])
                        if rr.get("extract") and rr["extract"].get("output_metric")]
            is_event_src = topic in ev_topic
            if ex_roles or is_event_src:
                try:
                    msg = TS.deserialize_ros1(rawdata, c.msgtype)
                except Exception:
                    warn("deserialize_failed")
                    continue
            for rr in ex_roles:
                ex = rr["extract"]
                om = ex["output_metric"]
                agg_of[om] = ex.get("aggregation", DEFAULT_AGG.get(ex["kind"], "last"))
                raw_count[om] += 1
                try:
                    if ex["kind"] == "front_min_range":
                        v = _front_min_range(msg.ranges, float(msg.angle_min),
                                             float(msg.angle_increment), ex)
                    elif ex["kind"] == "scalar_field":
                        v = _get_field(msg, ex["field"])
                    elif ex["kind"] == "tf_jump":
                        v, dyaw = _tf_jump(msg, ex, tf_prev[om])
                        yom = ex.get("yaw_output_metric")
                        if yom and dyaw is not None:
                            agg_of[yom] = ex.get("yaw_aggregation", "max")
                            raw_count[yom] += 1
                            raw[yom].append((t, dyaw))
                    else:
                        v = None; warn("unsupported_extract_kind")
                except Exception:
                    v = None; warn("extract_field_error")
                if v is not None:
                    raw[om].append((t, v))
            if is_event_src:
                for name, ev in ev_topic[topic]:          # profile order -> deterministic
                    matched = _apply_matcher(ev["matcher"]["kind"], msg, ev["matcher"], warn)
                    st = ev_state[name]
                    if matched:
                        edge_ok = st["last_emit"] is None or not st["last_match"]
                        win = ev.get("deduplicate_window_s")
                        win_ok = win is None or st["last_emit"] is None or (t - st["last_emit"]) > win
                        emit = ({"edge": edge_ok}.get(ev.get("emit", "all"), True)) and win_ok
                        if emit:
                            st["last_emit"] = t
                            level = "WARN" if ev.get("transition") == "assert" else "INFO"
                            logs.append({"t": round(t, 3), "level": level, "node": "extractor",
                                         "code": ev["output_code"], "message": ""})
                    st["last_match"] = matched

    metric_list, out_count = _bucketize(raw, agg_of, bucket_s)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    timeline = {"incident_id": profile.get("profile_id", out.name),
                "t_start": round(t0, 3) if t0 is not None else 0.0,
                "t_end": round(t1, 3) if t1 is not None else 0.0,
                "tracks": {"lidar": [], "charts": [], "metrics": metric_list}}
    (out / "timeline.json").write_text(json.dumps(timeline, indent=2))
    logs.sort(key=lambda x: x["t"])
    (out / "logs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in logs))
    resample = {"rate_hz": rate_hz, "bucket_s": round(bucket_s, 4),
                "metrics": {om: {"raw": raw_count[om], "valid": len(raw[om]),
                                 "invalid": raw_count[om] - len(raw[om]),
                                 "output": out_count[om], "aggregation": agg_of.get(om, "last")}
                            for om in raw_count},
                "coverage_s": [timeline["t_start"], timeline["t_end"]]}
    warnings = [dict({"code": c, "count": n}, **({"role": role} if role else {}))
                for (c, role), n in sorted(warn_counts.items())]
    metadata = {"incident_id": timeline["incident_id"], "duration_s": timeline["t_end"],
                "synchronization": {"default_max_skew_s":
                                    profile.get("synchronization", {}).get("default_max_skew_s", 0.2)},
                "source": "extract_incident", "profile_id": profile.get("profile_id"),
                "resample": resample, "warnings": warnings}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"metrics": len(metric_list), "logs": len(logs), "t_end": timeline["t_end"],
            "resample": resample, "warnings": warnings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True, type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    profile = json.loads(args.profile.read_text())
    s = run(args.bag, profile, args.out)
    print(f"[extract] out={args.out} metrics={s['metrics']} logs={s['logs']} "
          f"t_end={s['t_end']} warnings={s['warnings']}")


if __name__ == "__main__":
    main()
