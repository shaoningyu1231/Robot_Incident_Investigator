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
from rosbags.typesys import Stores, get_types_from_msg, get_typestore

TS = get_typestore(Stores.ROS1_NOETIC)
TS.register(get_types_from_msg("geometry_msgs/TransformStamped[] transforms",
                               "tf2_msgs/msg/TFMessage"))
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

# --- Boundary checks: neutral spec, source-side-only real codes ---
spec_txt = (ROOT / "specs" / "obstacle_stop.json").read_text()
check("spec has no DEMO_ codes (abstract EVENT_* only)", "DEMO_" not in spec_txt)
sp = json.loads((ROOT / "profiles" / "synthetic_demo.example.json").read_text())
check("synthetic profile: output_code is EVENT_*, DEMO_ only inside matcher",
      all(e["output_code"].startswith("EVENT_") for e in sp["events"].values())
      and all("DEMO_" in json.dumps(e["matcher"]) for e in sp["events"].values()))


def _header(i, t):
    return T["std_msgs/msg/Header"](seq=i, stamp=T["builtin_interfaces/msg/Time"](
        sec=int(t), nanosec=int((t % 1) * 1e9)), frame_id="")


def build_log_bag(path, rows, base=1000.0):
    Log = T["rosgraph_msgs/msg/Log"]
    with Writer(path) as w:
        conn = w.add_connection("/rosout", "rosgraph_msgs/msg/Log", typestore=TS)
        for i, (dt, lvl, name, msg) in enumerate(rows):
            t = base + dt
            m = Log(header=_header(i, t), level=lvl, name=name, msg=msg,
                    file="", function="", line=0, topics=[])
            w.write(conn, int(t * 1e9), TS.serialize_ros1(m, "rosgraph_msgs/msg/Log"))


def build_diag_bag(path, statuses, base=2000.0):
    DA = T["diagnostic_msgs/msg/DiagnosticArray"]; DS = T["diagnostic_msgs/msg/DiagnosticStatus"]
    with Writer(path) as w:
        conn = w.add_connection("/diagnostics", "diagnostic_msgs/msg/DiagnosticArray", typestore=TS)
        for i, (lvl, name) in enumerate(statuses):
            st = DS(level=lvl, name=name, message="", hardware_id="", values=[])
            m = DA(header=_header(i, base + i), status=[st])
            w.write(conn, int((base + i) * 1e9), TS.serialize_ros1(m, "diagnostic_msgs/msg/DiagnosticArray"))


def _logs(outdir):
    return [json.loads(x) for x in (outdir / "logs.jsonl").read_text().splitlines() if x.strip()]


# --- rosout_text matcher: match -> abstract code, edge-deduped, no text leak ---
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp); bag = tmp / "r.bag"
    build_log_bag(bag, [(0, 2, "nav", "Navigating normally"),
                        (1, 8, "safety", "OBSTACLE in safety zone"),
                        (2, 8, "safety", "OBSTACLE in safety zone"),
                        (3, 8, "safety", "OBSTACLE in safety zone")])  # republished
    prof = {"profile_id": "r", "roles": {"rosout": {"topic": "/rosout", "msgtype": "rosgraph_msgs/msg/Log"}},
            "events": {"obstacle_stop": {"source_role": "rosout",
                       "matcher": {"kind": "rosout_text", "op": "contains", "value": "OBSTACLE", "level_min": 8},
                       "transition": "assert", "output_code": "EVENT_OBSTACLE_STOP", "emit": "edge"}}}
    EX.run(bag, prof, tmp / "out")
    lg = _logs(tmp / "out")
    check("rosout match -> abstract stop, edge-deduped to 1", len(lg) == 1 and lg[0]["code"] == "EVENT_OBSTACLE_STOP")
    check("rosout: no source text / real code leaked",
          all(x["message"] == "" and x["code"].startswith("EVENT_") for x in lg))

# --- diagnostic_status matcher: match -> abstract; no-match -> nothing ---
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    build_diag_bag(tmp / "d.bag", [(0, "ok"), (2, "safety_controller"), (2, "safety_controller")])  # ERROR repeated
    prof = {"profile_id": "d", "roles": {"diag": {"topic": "/diagnostics", "msgtype": "diagnostic_msgs/msg/DiagnosticArray"}},
            "events": {"obstacle_stop": {"source_role": "diag",
                       "matcher": {"kind": "diagnostic_status", "field": "name", "op": "contains", "value": "safety", "level_min": 2},
                       "transition": "assert", "output_code": "EVENT_OBSTACLE_STOP", "emit": "edge"}}}
    EX.run(tmp / "d.bag", prof, tmp / "out")
    check("diagnostic match -> abstract stop, edge-deduped to 1",
          len([x for x in _logs(tmp / "out") if x["code"] == "EVENT_OBSTACLE_STOP"]) == 1)
    build_diag_bag(tmp / "d2.bag", [(0, "ok"), (1, "battery_low")])   # nothing at ERROR + "safety"
    EX.run(tmp / "d2.bag", prof, tmp / "out2")
    check("diagnostic no-match -> no events emitted", len(_logs(tmp / "out2")) == 0)


# --- tf_jump: consecutive-sample deltas, spike survives max bucketing, decoy child ignored ---
def build_tf_bag(path, samples, base=3000.0):
    import math as _m
    TFMsg = T["tf2_msgs/msg/TFMessage"]; TrS = T["geometry_msgs/msg/TransformStamped"]
    Tr = T["geometry_msgs/msg/Transform"]; V3 = T["geometry_msgs/msg/Vector3"]
    Quat = T["geometry_msgs/msg/Quaternion"]; Hdr = T["std_msgs/msg/Header"]
    Time = T["builtin_interfaces/msg/Time"]
    with Writer(path) as w:
        conn = w.add_connection("/tf", "tf2_msgs/msg/TFMessage", typestore=TS)
        for i, (dt, x, yaw) in enumerate(samples):
            t = base + dt
            hdr = Hdr(seq=i, stamp=Time(sec=int(t), nanosec=int((t % 1) * 1e9)), frame_id="map")
            good = TrS(header=hdr, child_frame_id="odom",
                       transform=Tr(translation=V3(x=float(x), y=0.0, z=0.0),
                                    rotation=Quat(x=0.0, y=0.0, z=_m.sin(yaw / 2), w=_m.cos(yaw / 2))))
            decoy = TrS(header=hdr, child_frame_id="base_link",
                        transform=Tr(translation=V3(x=100.0 * i, y=0.0, z=0.0),
                                     rotation=Quat(x=0.0, y=0.0, z=0.0, w=1.0)))
            m = TFMsg(transforms=[decoy, good])
            w.write(conn, int(t * 1e9), TS.serialize_ros1(m, "tf2_msgs/msg/TFMessage"))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    build_tf_bag(tmp / "tf.bag", [(0.0, 0.0, 0.0), (0.1, 0.05, 0.0), (0.2, 0.10, 0.0), (0.3, 1.15, 0.5)])
    prof = {"profile_id": "tf", "roles": {"tf": {"topic": "/tf", "msgtype": "tf2_msgs/msg/TFMessage",
            "extract": {"kind": "tf_jump", "parent_frame": "map", "child_frame": "odom",
                        "output_metric": "tf_jump_m", "yaw_output_metric": "tf_yaw_jump_rad"}}}}
    EX.run(tmp / "tf.bag", prof, tmp / "out")
    rows = {m["t"]: m for m in json.loads((tmp / "out" / "timeline.json").read_text())["tracks"]["metrics"]}
    check("tf_jump: per-sample deltas, decoy child ignored, jump spike preserved",
          abs(rows[0.1]["tf_jump_m"] - 0.05) < 1e-6 and abs(rows[0.2]["tf_jump_m"] - 0.05) < 1e-6
          and abs(rows[0.3]["tf_jump_m"] - 1.05) < 1e-6 and "tf_jump_m" not in rows.get(0.0, {}))
    check("tf_jump: yaw delta emitted alongside translation",
          abs(rows[0.3]["tf_yaw_jump_rad"] - 0.5) < 1e-3 and rows[0.2]["tf_yaw_jump_rad"] == 0.0)

print(f"--- {passed}/{passed + failed} extractor tests passed ---")
sys.exit(0 if failed == 0 else 1)
