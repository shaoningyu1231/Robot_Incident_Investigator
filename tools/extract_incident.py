#!/usr/bin/env python3
"""Profile-driven incident extractor (Phase 2; synthetic gate first).

Reads a rosbag + a topic-mapping profile and produces the NEUTRAL intermediate
representation the spec compiler consumes: timeline.json (metrics only) +
logs.jsonl + a minimal metadata.json. Renders NO visualization — no LiDAR frames,
charts, or spatial images. Verification runs on scalars + events.

    bag + profile -> extract_incident -> timeline.json / logs.jsonl / metadata.json
                     incident_spec.py (compile) -> derived annotations
                     incident_rules.py (verify, unchanged)

Hardening:
  - Connection filtering: only the profile's mapped topics are read.
  - A topic may back several roles (e.g. actual_vel and odom both on /odom); every
    role's extract runs.
  - Explicit resampling to a fixed output rate (default 10 Hz) with a declared
    aggregation per metric (front_min_range -> min, scalar_field -> last). Floor
    bucketing by integer index (float-robust), NOT last-write-wins on a rounded
    timestamp. Events keep their own timestamp (rounded to 1 ms).
  - metadata.resample records raw / valid / invalid / output counts + aggregation.

Extract kinds: front_min_range, scalar_field. Event kinds: json_string_event
(rosout_text / diagnostic_status: later, with a unified event schema). A missing
role degrades gracefully — its metric is absent, so the dependent signal is low.

Usage:
  python tools/extract_incident.py --bag in.bag \\
    --profile profiles/synthetic_demo.example.json --out out_dir
"""
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore

TS = get_typestore(Stores.ROS1_NOETIC)
DEFAULT_AGG = {"front_min_range": "min", "scalar_field": "last"}
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


def _aggregate(samples, how, center):
    """samples: [(t, v)] sorted by t, v not None. Aggregate per `how`."""
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
    """raw: {metric: [(t, v)]} -> (rows sorted by t, out_count).

    Floor bucketing by integer index (robust to 0.1 being inexact in float):
    idx = floor(t / bucket_s + eps). Correct for any output rate, not just 10 Hz.
    """
    buckets = {}   # idx -> {metric: [(t, v)]}
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


def run(bag_path, profile, out_dir):
    roles = profile["roles"]
    topic_roles = defaultdict(list)          # topic -> [role dict, ...]  (Bug 1 fix)
    for r in roles.values():
        topic_roles[r["topic"]].append(r)
    events_cfg = profile.get("events", {})
    ev_role = events_cfg.get("source_role")
    ev_topic = roles[ev_role]["topic"] if ev_role in roles else None
    rate_hz = float(profile.get("resample", {}).get("rate_hz", 10.0))
    bucket_s = 1.0 / rate_hz

    want = set(topic_roles) | ({ev_topic} if ev_topic else set())
    raw = defaultdict(list)      # output_metric -> [(t, v)] (valid samples only)
    raw_count = defaultdict(int)  # attempts (messages), incl. invalid
    agg_of = {}
    logs, warnings = [], []
    t0 = t1 = None

    with Reader(bag_path) as r:
        start = r.start_time
        conns = [c for c in r.connections if c.topic in want]   # connection filtering
        for c, ts, rawdata in r.messages(connections=conns):
            t = (ts - start) / 1e9
            t0 = t if t0 is None else min(t0, t)
            t1 = t if t1 is None else max(t1, t)
            topic = c.topic
            ex_roles = [rr for rr in topic_roles.get(topic, [])
                        if rr.get("extract") and rr["extract"].get("output_metric")]
            if ex_roles:
                try:
                    msg = TS.deserialize_ros1(rawdata, c.msgtype)
                except Exception:
                    msg = None
                    warnings.append("deserialize_failed")
                for rr in ex_roles:
                    ex = rr["extract"]
                    om = ex["output_metric"]
                    agg_of[om] = ex.get("aggregation", DEFAULT_AGG.get(ex["kind"], "last"))
                    raw_count[om] += 1
                    if msg is None:
                        continue
                    try:
                        if ex["kind"] == "front_min_range":
                            v = _front_min_range(msg.ranges, float(msg.angle_min),
                                                 float(msg.angle_increment), ex)
                        elif ex["kind"] == "scalar_field":
                            v = _get_field(msg, ex["field"])
                        else:
                            v = None
                            warnings.append("unsupported_kind:" + str(ex["kind"]))
                    except Exception:
                        v = None
                    if v is not None:
                        raw[om].append((t, v))
            if topic == ev_topic and events_cfg.get("kind") == "json_string_event":
                try:
                    payload = json.loads(TS.deserialize_ros1(rawdata, c.msgtype).data)
                    code = payload.get(events_cfg.get("code_field", "code"))
                    kind = payload.get(events_cfg.get("kind_field", "kind"))
                    level = (events_cfg.get("clear_level", "INFO") if kind == "clear"
                             else events_cfg.get("assert_level", "WARN"))
                    logs.append({"t": round(t, 3), "level": level, "node": "extractor",
                                 "code": code, "message": ""})
                except (ValueError, TypeError, AttributeError):
                    warnings.append("event_parse_failed")

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
    metadata = {"incident_id": timeline["incident_id"], "duration_s": timeline["t_end"],
                "synchronization": {"default_max_skew_s":
                                    profile.get("synchronization", {}).get("default_max_skew_s", 0.2)},
                "source": "extract_incident", "profile_id": profile.get("profile_id"),
                "resample": resample, "warnings": sorted(set(warnings))}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"metrics": len(metric_list), "logs": len(logs), "t_end": timeline["t_end"],
            "resample": resample}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True, type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    profile = json.loads(args.profile.read_text())
    s = run(args.bag, profile, args.out)
    print(f"[extract] out={args.out} metrics={s['metrics']} logs={s['logs']} "
          f"t_end={s['t_end']} resample={s['resample']['rate_hz']}Hz")


if __name__ == "__main__":
    main()
