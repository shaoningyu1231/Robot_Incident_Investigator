#!/usr/bin/env python3
"""Profile-driven incident extractor (Phase 2; synthetic gate first).

Reads a rosbag + a topic-mapping profile and produces the NEUTRAL intermediate
representation the spec compiler consumes: timeline.json (metrics only) +
logs.jsonl + a minimal metadata.json. It renders NO visualization — no LiDAR
frames, no charts, no spatial images. Verification runs on scalars + events, so
nothing about a real environment's layout is emitted.

    bag + profile -> extract_incident -> timeline.json / logs.jsonl / metadata.json
                     incident_spec.py (compile) -> derived annotations
                     incident_rules.py (verify, unchanged)

Extract kinds: front_min_range, scalar_field. Event kinds: json_string_event
(rosout_text / diagnostic_status are declared in the profile schema but
implemented in the real-bag branch). A missing role degrades gracefully — its
metric is simply absent, so the dependent signal drops to low.

Usage:
  python tools/extract_incident.py --bag in.bag \\
    --profile profiles/synthetic_demo.example.json --out out_dir
"""
import argparse
import json
import math
from pathlib import Path

from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore

TS = get_typestore(Stores.ROS1_NOETIC)


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


def run(bag_path, profile, out_dir):
    roles = profile["roles"]
    topic_role = {r["topic"]: (name, r) for name, r in roles.items()}
    events_cfg = profile.get("events", {})
    ev_role = events_cfg.get("source_role")
    ev_topic = roles[ev_role]["topic"] if ev_role in roles else None

    metrics = {}   # t -> {"t": t, <output_metric>: value}
    logs = []
    t0 = t1 = None

    with Reader(bag_path) as r:
        start = r.start_time
        for conn, ts, raw in r.messages():
            t = round((ts - start) / 1e9, 1)
            t0 = t if t0 is None else min(t0, t)
            t1 = t if t1 is None else max(t1, t)
            topic = conn.topic

            if topic in topic_role:
                _, role = topic_role[topic]
                ex = role.get("extract")
                if ex:
                    msg = TS.deserialize_ros1(raw, conn.msgtype)
                    if ex["kind"] == "front_min_range":
                        v = _front_min_range(msg.ranges, float(msg.angle_min),
                                             float(msg.angle_increment), ex)
                    elif ex["kind"] == "scalar_field":
                        v = _get_field(msg, ex["field"])
                    else:
                        v = None
                    if ex.get("output_metric"):
                        row = metrics.setdefault(t, {"t": t})
                        row[ex["output_metric"]] = None if v is None else round(v, 3)

            if topic == ev_topic and events_cfg.get("kind") == "json_string_event":
                msg = TS.deserialize_ros1(raw, conn.msgtype)
                try:
                    payload = json.loads(msg.data)
                except (ValueError, TypeError, AttributeError):
                    continue
                code = payload.get(events_cfg.get("code_field", "code"))
                kind = payload.get(events_cfg.get("kind_field", "kind"))
                level = (events_cfg.get("clear_level", "INFO") if kind == "clear"
                         else events_cfg.get("assert_level", "WARN"))
                logs.append({"t": t, "level": level, "node": "extractor",
                             "code": code, "message": ""})

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    metric_list = [metrics[t] for t in sorted(metrics)]
    timeline = {"incident_id": profile.get("profile_id", out.name),
                "t_start": t0 or 0.0, "t_end": t1 or 0.0,
                "tracks": {"lidar": [], "charts": [], "metrics": metric_list}}
    (out / "timeline.json").write_text(json.dumps(timeline, indent=2))
    logs.sort(key=lambda x: x["t"])
    (out / "logs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in logs))
    metadata = {"incident_id": timeline["incident_id"], "duration_s": t1 or 0.0,
                "synchronization": {"default_max_skew_s":
                                    profile.get("synchronization", {}).get("default_max_skew_s", 0.2)},
                "source": "extract_incident", "profile_id": profile.get("profile_id")}
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))
    return {"metrics": len(metric_list), "logs": len(logs), "t_end": t1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True, type=Path)
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    profile = json.loads(args.profile.read_text())
    s = run(args.bag, profile, args.out)
    print(f"[extract] out={args.out} metrics={s['metrics']} logs={s['logs']} t_end={s['t_end']}")


if __name__ == "__main__":
    main()
