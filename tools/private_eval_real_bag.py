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


MAPPING_SLOTS = ["front_scan", "cmd_vel", "actual_vel", "odom", "rosout", "diag", "tf"]


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

    # --- topic_mapping.local.example.json (template to copy -> .local.json and fill) ---
    example = {"_about": "Copy to topic_mapping.local.json (git-ignored) and fill each slot "
                         "with a REAL topic name from topic_candidates.local.txt. Keep it local; "
                         "share only sanitized labels with the assistant.",
               **{slot: "" for slot in MAPPING_SLOTS}}
    (out / "topic_mapping.local.example.json").write_text(json.dumps(example, indent=2))

    print(f"[private-eval] out={out}")
    print(f"[private-eval] size={size_mb}MB duration={duration_s}s "
          f"topics={len(topics)} messages={manifest['message_count']}")
    print("[private-eval] wrote manifest.redacted.json (safe) + "
          "topic_candidates.local.txt + topic_mapping.local.example.json (LOCAL ONLY)")


if __name__ == "__main__":
    main()
