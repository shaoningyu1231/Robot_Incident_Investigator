#!/usr/bin/env python3
"""Extractor hardening tests (no real bag; builds a tiny synthetic bag in a tmp dir).

Covers the two correctness fixes:
  - a topic backing multiple roles still runs the extract-bearing role
  - resample bucketing is correct at non-10 Hz and aggregates per rule (min/last)
and the raw / valid / output counts.

Run: <venv>/python3 tools/test_extractor.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import extract_incident as EX
from rosbags.rosbag1 import Writer
from rosbags.typesys import Stores, get_typestore

TS = get_typestore(Stores.ROS1_NOETIC)
T = TS.types
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  [PASS] {name}")
    else:
        failed += 1; print(f"  [FAIL] {name}")


# --- Test 1: 20 Hz buckets must not merge (Bug 2) ---
rows, _ = EX._bucketize({"x": [(0.00, 1.0), (0.05, 2.0), (0.10, 3.0), (0.15, 4.0)]}, {"x": "last"}, 0.05)
check("20Hz: 0.05 buckets stay distinct (4 rows)", len(rows) == 4 and sorted(r["t"] for r in rows) == [0.0, 0.05, 0.1, 0.15])

# --- Test 2: same bucket -> range=min, velocity=last ---
rows2, _ = EX._bucketize({"rng": [(10.50, 0.9), (10.53, 0.4), (10.57, 0.7)],
                          "vel": [(10.50, 0.20), (10.57, 0.05)]}, {"rng": "min", "vel": "last"}, 0.1)
check("same bucket: range=min, velocity=last", len(rows2) == 1 and rows2[0]["rng"] == 0.4 and rows2[0]["vel"] == 0.05)

# --- Test 3: two roles share one topic -> extract-bearing role still produces its metric (Bug 1) ---
def build_odom_bag(path, n=5, v=0.5, base=1000.0):
    Header = T["std_msgs/msg/Header"]; Time = T["builtin_interfaces/msg/Time"]
    Odom = T["nav_msgs/msg/Odometry"]; Pose = T["geometry_msgs/msg/Pose"]
    Point = T["geometry_msgs/msg/Point"]; Quat = T["geometry_msgs/msg/Quaternion"]
    PWC = T["geometry_msgs/msg/PoseWithCovariance"]; Tw = T["geometry_msgs/msg/Twist"]
    V3 = T["geometry_msgs/msg/Vector3"]; TWC = T["geometry_msgs/msg/TwistWithCovariance"]
    cov = np.zeros(36, dtype=np.float64)
    with Writer(path) as w:
        conn = w.add_connection("/odom", "nav_msgs/msg/Odometry", typestore=TS)
        for i in range(n):
            t = base + i * 0.1
            msg = Odom(header=Header(seq=i, stamp=Time(sec=int(t), nanosec=int((t % 1) * 1e9)), frame_id="odom"),
                       child_frame_id="base",
                       pose=PWC(pose=Pose(position=Point(x=0.0, y=0.0, z=0.0),
                                          orientation=Quat(x=0.0, y=0.0, z=0.0, w=1.0)), covariance=cov.copy()),
                       twist=TWC(twist=Tw(linear=V3(x=v, y=0.0, z=0.0), angular=V3(x=0.0, y=0.0, z=0.0)),
                                 covariance=cov.copy()))
            w.write(conn, int(t * 1e9), TS.serialize_ros1(msg, "nav_msgs/msg/Odometry"))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp); bag = tmp / "t.bag"; build_odom_bag(bag, n=5, v=0.5)
    prof = {"profile_id": "t", "roles": {
        "actual_vel": {"topic": "/odom", "msgtype": "nav_msgs/msg/Odometry",
                       "extract": {"kind": "scalar_field", "field": "twist.twist.linear.x",
                                   "output_metric": "actual_speed_mps"}},
        "odom": {"topic": "/odom", "msgtype": "nav_msgs/msg/Odometry"}}}
    EX.run(bag, prof, tmp / "out")
    ms = json.loads((tmp / "out" / "timeline.json").read_text())["tracks"]["metrics"]
    vals = [m.get("actual_speed_mps") for m in ms if m.get("actual_speed_mps") is not None]
    check("shared topic: actual_vel still extracted (not overwritten by odom role)",
          len(vals) == 5 and all(abs(v - 0.5) < 1e-6 for v in vals))
    rc = json.loads((tmp / "out" / "metadata.json").read_text())["resample"]["metrics"]["actual_speed_mps"]
    check("counts: raw=valid=output=5, invalid=0",
          rc["raw"] == 5 and rc["valid"] == 5 and rc["invalid"] == 0 and rc["output"] == 5)

print(f"--- {passed}/{passed + failed} extractor tests passed ---")
sys.exit(0 if failed == 0 else 1)
