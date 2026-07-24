#!/usr/bin/env python3
"""PRIVATE, LOCAL-ONLY real-bag smoke test (Phase 2 skeleton).

Reads a real robot rosbag LOCALLY and writes a REDACTED manifest plus local-only
helper files to <out>/, to check pipeline plumbing (can we open it, what is
inside), NOT verdict correctness. A real bag has no ground truth; verdict
correctness is validated only on synthetic scenarios (tools/eval_extractor.py).

DATA BOUNDARY — do not violate:
  - Run locally only. Output goes under private_eval/ (git-ignored).
  - Never commit outputs, upload them, send them to Gemini, paste them into an
    assistant/chat, or route the bag through any cloud/MCP tool.
  - Files ending .redacted.* are safe to glance at; files ending .local.* contain
    real names and must never leave the machine.

Redaction policy (default, and REQUIRED for any future version that reads message
content, which this one does NOT yet do):
  - topic names -> hashed in the redacted manifest
  - non-standard / vendor / internal msgtype package names -> hashed
  - frame_id / child_frame_id -> hash or omit
  - /rosout log message text -> never emit raw text; only level / count / whether
    it matched a pattern
  - diagnostic_msgs name/hardware_id -> hash
  - never write real error codes, node names, serials, or map/location strings to
    the redacted manifest

Usage:
  python tools/private_eval_real_bag.py --bag /path/to/real.bag --out private_eval/case_001
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path


def _redact(name):
    return "topic_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


# Public ROS message packages are safe to show in the redacted manifest; anything
# else (vendor/internal) reveals proprietary package naming and is hashed instead.
STANDARD_ROS_PKGS = {
    "std_msgs", "std_srvs", "geometry_msgs", "sensor_msgs", "nav_msgs",
    "shape_msgs", "stereo_msgs", "trajectory_msgs", "visualization_msgs",
    "actionlib_msgs", "actionlib", "diagnostic_msgs", "tf2_msgs", "tf",
    "rosgraph_msgs", "control_msgs", "map_msgs", "move_base_msgs",
    "dynamic_reconfigure", "builtin_interfaces", "unique_identifier_msgs",
}


def _redact_msgtype(mt):
    if mt.split("/", 1)[0] in STANDARD_ROS_PKGS:
        return mt
    return "custom_msgtype_" + hashlib.sha1(mt.encode("utf-8")).hexdigest()[:8]


# Heuristic role suggestions: which standard msgtype backs which neutral role,
# and the extract block each role usually wants. Suggestions are PROVISIONAL —
# the skeleton marks them so and the user must review every pick.
ROLE_MSGTYPES = {
    "front_scan": "sensor_msgs/msg/LaserScan",
    "cmd_vel": "geometry_msgs/msg/Twist",
    "actual_vel": "nav_msgs/msg/Odometry",
    "rosout": "rosgraph_msgs/msg/Log",
    "diag": "diagnostic_msgs/msg/DiagnosticArray",
    "tf": "tf2_msgs/msg/TFMessage",
}
ROLE_EXTRACTS = {
    "front_scan": {"kind": "front_min_range", "front_sector_deg": 15.0, "front_axis_rad": 0.0,
                   "range_min_m": 0.05, "range_max_m": 10.0, "output_metric": "front_min_range_m"},
    "cmd_vel": {"kind": "scalar_field", "field": "linear.x", "output_metric": "planner_speed_mps"},
    "actual_vel": {"kind": "scalar_field", "field": "twist.twist.linear.x",
                   "output_metric": "actual_speed_mps"},
    "tf": {"kind": "tf_jump", "parent_frame": "<FILL: localization parent frame>",
           "child_frame": "<FILL: localization child frame>",
           "output_metric": "tf_jump_m", "yaw_output_metric": "tf_yaw_jump_rad"},
}


def _norm_msgtype(mt):
    parts = mt.strip("/").split("/")
    return f"{parts[0]}/msg/{parts[-1]}" if len(parts) >= 2 else mt


def suggest_profile(topics, case_id):
    """Build a schema-valid roles/events profile skeleton from the bag's topic
    index ({real_topic: {msgtype, count}}). Pure msgtype heuristics: per role,
    pick the highest-count topic of the expected type and list the rest under
    `_alternatives`. Contains REAL topic names -> output is LOCAL ONLY."""
    by_type = defaultdict(list)
    for name, info in topics.items():
        by_type[_norm_msgtype(info["msgtype"])].append((info["count"], name))
    roles, unmapped = {}, []
    for role, mt in ROLE_MSGTYPES.items():
        cands = sorted(by_type.get(mt, []), reverse=True)
        if not cands:
            unmapped.append(role)
            continue
        role_def = {"topic": cands[0][1], "msgtype": mt, "_status": "provisional"}
        if len(cands) > 1:
            role_def["_alternatives"] = [n for _, n in cands[1:]]
        if role in ROLE_EXTRACTS:
            role_def["extract"] = dict(ROLE_EXTRACTS[role])
        roles[role] = role_def
    if "actual_vel" in roles:   # odom role rides the same Odometry topic, no extract of its own
        roles["odom"] = {"topic": roles["actual_vel"]["topic"],
                         "msgtype": "nav_msgs/msg/Odometry", "_status": "provisional"}
    else:
        unmapped.append("odom")

    events = {}
    ev_role = "rosout" if "rosout" in roles else ("diag" if "diag" in roles else None)
    if ev_role == "rosout":
        stop_matcher = {"kind": "rosout_text", "op": "contains",
                        "value": "<FILL: substring of the real stop log line>", "level_min": 8}
        clear_matcher = {"kind": "rosout_text", "op": "contains",
                         "value": "<FILL: substring of the real clear log line>"}
    elif ev_role == "diag":
        stop_matcher = {"kind": "diagnostic_status", "field": "name", "op": "contains",
                        "value": "<FILL: substring of the real status name>", "level_min": 2}
        clear_matcher = {"kind": "diagnostic_status", "field": "name", "op": "contains",
                         "value": "<FILL: substring of the real status name>"}
    if ev_role:
        events = {
            "stop_event": {"source_role": ev_role, "matcher": stop_matcher,
                           "transition": "assert", "output_code": "EVENT_OBSTACLE_STOP",
                           "_status": "provisional"},
            "clear_event": {"source_role": ev_role, "matcher": clear_matcher,
                            "transition": "clear", "output_code": "EVENT_OBSTACLE_CLEAR",
                            "_status": "provisional"},
        }
    return {
        "_about": "Auto-suggested SKELETON from msgtype heuristics — every mapping is provisional; "
                  "review each pick against topic_candidates.local.txt. Copy to "
                  "topic_mapping.local.json, fix wrong picks, fill every <FILL: ...>, delete "
                  "roles/events you don't have. LOCAL ONLY: contains real topic names — never "
                  "commit, upload, or share. Field reference: docs/topic_mapping.md",
        "_unmapped_roles": unmapped,
        "profile_version": "0.1",
        "profile_id": case_id,
        "robot_family": "<FILL: your robot family>",
        "privacy": {"contains_private_topics": True, "commit_safe": False},
        "time": {"source": "bag_message_time"},
        "synchronization": {"default_max_skew_s": 0.2},
        "roles": roles,
        "events": events,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bag", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path,
                    help="output dir under private_eval/ (git-ignored)")
    args = ap.parse_args()

    if not args.bag.exists():
        sys.exit(f"bag not found: {args.bag}")
    if args.bag.suffix != ".bag":
        sys.exit("this skeleton handles ROS1 .bag only (ROS2 db3 / MCAP: later)")

    from rosbags.rosbag1 import Reader  # dev-only dependency

    size_mb = round(args.bag.stat().st_size / 1e6, 1)
    topics = {}  # real name -> {msgtype, count}
    with Reader(args.bag) as r:
        duration_s = round((r.end_time - r.start_time) / 1e9, 1)
        for c in r.connections:
            info = topics.setdefault(c.topic, {"msgtype": c.msgtype, "count": 0})
            info["count"] += getattr(c, "msgcount", 0)  # from the index; no full read

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    # --- manifest.redacted.json (topic names hashed; safe to glance at) ---
    manifest = {
        "case": out.name,
        "bag_size_mb": size_mb,
        "duration_s": duration_s,
        "topic_count": len(topics),
        "message_count": sum(t["count"] for t in topics.values()),
        "topics_redacted": sorted(
            ({"topic": _redact(name), "msgtype": _redact_msgtype(info["msgtype"]), "count": info["count"]}
             for name, info in topics.items()),
            key=lambda x: (-x["count"], x["topic"])),
        "_note": "Redacted. Real topic names hashed. LOCAL ONLY — do not commit, upload, "
                 "or send to any external service (Gemini/MCP/cloud/assistant).",
    }
    (out / "manifest.redacted.json").write_text(json.dumps(manifest, indent=2))

    # --- topic_candidates.local.txt (REAL names; local only, never share) ---
    lines = ["# LOCAL ONLY — real topic names. Do NOT paste into chat / commit / upload.",
             "# Use this to fill topic_mapping.local.json, then tell the assistant only",
             "# sanitized labels (e.g. front_scan=selected).", ""]
    lines += [f"{info['count']:>9}  {info['msgtype']:46}  {name}"
              for name, info in sorted(topics.items(), key=lambda kv: (kv[1]["msgtype"], -kv[1]["count"]))]
    (out / "topic_candidates.local.txt").write_text("\n".join(lines) + "\n")

    # --- topic_mapping.skeleton.local.json (schema-valid, heuristically pre-filled;
    #     copy -> topic_mapping.local.json, review every pick, fill <FILL: ...>) ---
    skeleton = suggest_profile(topics, out.name)
    (out / "topic_mapping.skeleton.local.json").write_text(json.dumps(skeleton, indent=2))

    print(f"[private-eval] out={out}")
    print(f"[private-eval] size={size_mb}MB duration={duration_s}s "
          f"topics={len(topics)} messages={manifest['message_count']}")
    mapped = [r for r in skeleton["roles"]]
    print(f"[private-eval] skeleton roles suggested={mapped} "
          f"unmapped={skeleton['_unmapped_roles']} (heuristic — review every pick)")
    print("[private-eval] wrote manifest.redacted.json (safe) + "
          "topic_candidates.local.txt + topic_mapping.skeleton.local.json (LOCAL ONLY)")


if __name__ == "__main__":
    main()
