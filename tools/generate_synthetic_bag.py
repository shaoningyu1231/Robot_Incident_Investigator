#!/usr/bin/env python3
"""生成 hero 案例的合成 ROS1 bag。

只写全合成数据。绝不读取任何真实 bag。
用法: <venv>/python3 tools/generate_synthetic_bag.py [out.bag]
"""
import sys
import json
import math
from pathlib import Path

import numpy as np
from rosbags.rosbag1 import Writer
from rosbags.typesys import Stores, get_types_from_msg, get_typestore

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenario as S

TS = get_typestore(Stores.ROS1_NOETIC)
TS.register(get_types_from_msg("geometry_msgs/TransformStamped[] transforms",
                               "tf2_msgs/msg/TFMessage"))
T = TS.types
Header = T["std_msgs/msg/Header"]
Time = T["builtin_interfaces/msg/Time"]
LaserScan = T["sensor_msgs/msg/LaserScan"]
Twist = T["geometry_msgs/msg/Twist"]
Vector3 = T["geometry_msgs/msg/Vector3"]
Point = T["geometry_msgs/msg/Point"]
Quaternion = T["geometry_msgs/msg/Quaternion"]
Pose = T["geometry_msgs/msg/Pose"]
PoseWithCov = T["geometry_msgs/msg/PoseWithCovariance"]
TwistWithCov = T["geometry_msgs/msg/TwistWithCovariance"]
Odometry = T["nav_msgs/msg/Odometry"]
Float32 = T["std_msgs/msg/Float32"]
String = T["std_msgs/msg/String"]
TransformStamped = T["geometry_msgs/msg/TransformStamped"]
Transform = T["geometry_msgs/msg/Transform"]
TFMessage = T["tf2_msgs/msg/TFMessage"]

RNG = np.random.default_rng(42)  # 固定种子,可复现

N_RAYS = 180
ANGLE_MIN = -math.pi / 2
ANGLE_MAX = math.pi / 2
ANGLE_INC = (ANGLE_MAX - ANGLE_MIN) / (N_RAYS - 1)
FRONT_SECTOR = 0.15  # ±rad,正前方扇区


def ns(t):
    return int(round((S.BASE_EPOCH_S + t) * 1e9))


def header(t, seq, frame="demo_base_link"):
    return Header(seq=seq, stamp=Time(sec=int(S.BASE_EPOCH_S + t),
                                      nanosec=int((t % 1.0) * 1e9)),
                  frame_id=frame)


def make_scan(t, seq):
    base = 3.0 + RNG.normal(0, 0.01, N_RAYS)
    ranges = base.astype(np.float32)
    if S.obstacle_present(t):
        d = S.front_distance(t)
        for i in range(N_RAYS):
            ang = ANGLE_MIN + i * ANGLE_INC
            if abs(ang) <= FRONT_SECTOR:
                ranges[i] = np.float32(d + RNG.normal(0, 0.005))
    return LaserScan(header=header(t, seq, "demo_base_link"),
                     angle_min=ANGLE_MIN, angle_max=ANGLE_MAX, angle_increment=ANGLE_INC,
                     time_increment=0.0, scan_time=S.DT, range_min=0.05, range_max=10.0,
                     ranges=ranges, intensities=np.zeros(0, dtype=np.float32))


def make_twist(v):
    return Twist(linear=Vector3(x=float(v), y=0.0, z=0.0),
                 angular=Vector3(x=0.0, y=0.0, z=0.0))


def make_tf(t, seq):
    x, y, yaw = S.tf_pose(t)
    return TFMessage(transforms=[TransformStamped(
        header=header(t, seq, S.FRAMES[0]), child_frame_id=S.FRAMES[1],
        transform=Transform(translation=Vector3(x=float(x), y=float(y), z=0.0),
                            rotation=Quaternion(x=0.0, y=0.0, z=math.sin(yaw / 2.0),
                                                w=math.cos(yaw / 2.0))))])


def make_odom(t, seq, v):
    cov = np.zeros(36, dtype=np.float64)
    pose = PoseWithCov(pose=Pose(position=Point(x=0.0, y=0.0, z=0.0),
                                 orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)),
                       covariance=cov.copy())
    twc = TwistWithCov(twist=make_twist(v), covariance=cov.copy())
    return Odometry(header=header(t, seq, "demo_odom"),
                    child_frame_id="demo_base_link", pose=pose, twist=twc)


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).resolve().parents[1] / "synthetic_bag" / "demo_obstacle_stop_01.bag"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    topics = {
        "/demo/scan": "sensor_msgs/msg/LaserScan",
        "/demo/front_distance": "std_msgs/msg/Float32",
        "/demo/planner_cmd_vel": "geometry_msgs/msg/Twist",
        "/demo/applied_cmd_vel": "geometry_msgs/msg/Twist",
        "/demo/odom": "nav_msgs/msg/Odometry",
        "/demo/safety_state": "std_msgs/msg/String",
        "/demo/error_events": "std_msgs/msg/String",
    }
    if S.TF_CFG:                       # tf only when the scenario declares it
        topics["/demo/tf"] = "tf2_msgs/msg/TFMessage"

    with Writer(out) as w:
        conn = {tp: w.add_connection(tp, mt, typestore=TS) for tp, mt in topics.items()}
        for seq, t in enumerate(S.samples()):
            w.write(conn["/demo/scan"], ns(t), TS.serialize_ros1(make_scan(t, seq), topics["/demo/scan"]))
            w.write(conn["/demo/front_distance"], ns(t),
                    TS.serialize_ros1(Float32(data=float(S.front_distance(t))), topics["/demo/front_distance"]))
            w.write(conn["/demo/planner_cmd_vel"], ns(t),
                    TS.serialize_ros1(make_twist(S.planner_speed(t)), topics["/demo/planner_cmd_vel"]))
            w.write(conn["/demo/applied_cmd_vel"], ns(t),
                    TS.serialize_ros1(make_twist(S.applied_speed(t)), topics["/demo/applied_cmd_vel"]))
            w.write(conn["/demo/odom"], ns(t),
                    TS.serialize_ros1(make_odom(t, seq, S.actual_speed(t)), topics["/demo/odom"]))
            w.write(conn["/demo/safety_state"], ns(t),
                    TS.serialize_ros1(String(data=S.safety_state(t)), topics["/demo/safety_state"]))
            if S.TF_CFG:
                w.write(conn["/demo/tf"], ns(t),
                        TS.serialize_ros1(make_tf(t, seq), topics["/demo/tf"]))
        # events from scenario config (obstacle: assert/clear pair; planned: none)
        for ev in S.EVENTS:
            payload = json.dumps({"code": ev["code"], "kind": ev["kind"]})
            w.write(conn["/demo/error_events"], ns(ev["t"]),
                    TS.serialize_ros1(String(data=payload), topics["/demo/error_events"]))

    size = out.stat().st_size
    print(f"[generate] wrote {out}  ({size/1024:.0f} KiB)  topics={len(topics)}  "
          f"samples={len(S.samples())}  duration={S.DURATION_S}s")


if __name__ == "__main__":
    main()
