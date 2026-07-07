#!/usr/bin/env python3
"""从合成 bag 导出固定格式演示资产到 incident/。

读取 tools/generate_synthetic_bag.py 产出的合成 bag。绝不读取真实 bag。
产出: lidar_frames/  charts/  timeline.json  logs.jsonl  metadata.json  annotations.json
用法: <venv>/python3 tools/export_incident_assets.py [in.bag] [out_dir]
"""
import sys
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from rosbags.rosbag1 import Reader
from rosbags.typesys import Stores, get_typestore

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scenario as S

TS = get_typestore(Stores.ROS1_NOETIC)
ROOT = Path(__file__).resolve().parents[1]

FRONT_SECTOR_HALF_DEG = 15.0                         # front angular sector for front_min_range_m
FRONT_SECTOR_HALF_RAD = math.radians(FRONT_SECTOR_HALF_DEG)


def lidar_ref(t):
    return f"lidar_frames/{int(round(t * 100))}.png"


# ---------- 简易 PIL 绘图 ----------
def _plot(path, series, t0, t1, ylim, ylabel, hlines=None, step=False):
    W, H, ML, MB, MT, MR = 560, 260, 60, 36, 24, 16
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = ML, MT, W - MR, H - MB
    d.rectangle([x0, y0, x1, y1], outline="black")
    ymin, ymax = ylim

    def px(t): return x0 + (t - t0) / (t1 - t0) * (x1 - x0)
    def py(v): return y1 - (v - ymin) / (ymax - ymin) * (y1 - y0) * -1 if False else \
        y1 - (v - ymin) / (ymax - ymin) * (y1 - y0)

    for hv, col in (hlines or []):
        yy = py(hv)
        d.line([x0, yy, x1, yy], fill=col, width=1)
        d.text((x1 - 70, yy - 12), f"thr={hv}", fill=col)
    colors = [(0, 90, 200), (200, 60, 0), (20, 150, 60), (120, 120, 120)]
    for i, (label, pts) in enumerate(series):
        col = colors[i % len(colors)]
        if step:
            poly = []
            prev = None
            for (t, v) in pts:
                if prev is not None:
                    poly.append((px(t), py(prev)))
                poly.append((px(t), py(v)))
                prev = v
            if len(poly) > 1:
                d.line(poly, fill=col, width=2)
        else:
            poly = [(px(t), py(v)) for t, v in pts]
            if len(poly) > 1:
                d.line(poly, fill=col, width=2)
        d.text((x0 + 6, y0 + 4 + 14 * i), label, fill=col)
    d.text((4, MT - 18), ylabel, fill="black")
    d.text(((x0 + x1) // 2 - 20, y1 + 8), "t (s)", fill="black")
    img.save(path)


def render_lidar(path, ranges, angle_min, angle_inc, thr=1.2, rmax=4.0):
    sz = 320
    img = Image.new("RGB", (sz, sz), "white")
    d = ImageDraw.Draw(img)
    cx, cy = sz // 2, sz - 30  # 机器人在底部中央,前向朝上
    scale = (sz - 60) / rmax
    d.ellipse([cx - int(thr * scale), cy - int(thr * scale),
               cx + int(thr * scale), cy + int(thr * scale)], outline=(200, 60, 0))
    d.text((6, 6), f"safety ring {thr}m", fill=(200, 60, 0))
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill="black")
    for i, r in enumerate(ranges):
        if r <= 0 or r > rmax:
            continue
        ang = angle_min + i * angle_inc
        x = cx + r * math.sin(ang) * scale
        y = cy - r * math.cos(ang) * scale
        near = r < thr
        col = (200, 0, 0) if near else (60, 60, 60)
        rad = 3 if near else 1
        d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col)
    img.save(path)


def front_min_range(ranges, angle_min, angle_inc, rmax=10.0):
    """Min valid LaserScan range within the front sector (geometric, no ML).

    Derived scalar for the incident-metrics spec so LiDAR evidence can be found in
    data instead of hand-authored. Ignores <=0 / inf / nan / out-of-range points.
    """
    best = None
    for i, r in enumerate(ranges):
        r = float(r)
        if r <= 0 or math.isinf(r) or math.isnan(r) or r > rmax:
            continue
        if abs(angle_min + i * angle_inc) <= FRONT_SECTOR_HALF_RAD:
            if best is None or r < best:
                best = r
    return best


# ---------- 读 bag ----------
def read_bag(bag_path):
    series = {"front": {}, "planner": {}, "applied": {}, "actual": {}, "safety": {}}
    scans = {}
    events = []
    with Reader(bag_path) as r:
        start = r.start_time
        conns = {c.topic: c for c in r.connections}
        for c, mt, raw in r.messages():
            t = round((mt - start) / 1e9, 1)
            m = TS.deserialize_ros1(raw, c.msgtype)
            tp = c.topic
            if tp == "/demo/front_distance":
                series["front"][t] = float(m.data)
            elif tp == "/demo/planner_cmd_vel":
                series["planner"][t] = float(m.linear.x)
            elif tp == "/demo/applied_cmd_vel":
                series["applied"][t] = float(m.linear.x)
            elif tp == "/demo/odom":
                series["actual"][t] = float(m.twist.twist.linear.x)
            elif tp == "/demo/safety_state":
                series["safety"][t] = str(m.data)
            elif tp == "/demo/scan":
                scans[t] = (np.asarray(m.ranges, dtype=float),
                            float(m.angle_min), float(m.angle_increment))
            elif tp == "/demo/error_events":
                ev = json.loads(m.data)
                events.append((t, ev["code"], ev["kind"]))
    return series, scans, events


# ---------- 授权写死的 metadata / annotations(与场景对齐,见 schema.md)----------
def metadata():
    doc = S.DOC  # scenario-specific copy; absent → obstacle-hero defaults (keeps hero byte-identical)
    return {
        "incident_id": S.INCIDENT_ID,
        "title": doc.get("title", "Synthetic obstacle-triggered safety stop"),
        "description": doc.get("description", "Fully synthetic; does not reproduce any real incident or bag."),
        "scenario_label": doc.get("scenario_label",
                                  "Obstacle-triggered safety stop, inspired by common AMR failure patterns."),
        "synthetic": True,
        "modalities": ["lidar", "metrics", "log"],
        "robot": {"id": "demo_bot_01", "type": "demo_amr"},
        "frames": {"map": S.FRAMES[0], "odom": S.FRAMES[1], "base_link": S.FRAMES[2]},
        "duration_s": S.DURATION_S,
        "demo_thresholds": {"front_safety_m": S.FRONT_SAFETY_M,
                            "front_min_range_sector_deg": FRONT_SECTOR_HALF_DEG},
        "synchronization": {
            "default_max_skew_s": S.CORROB_MAX_SKEW_S,
            "relations": {
                "lidar_distance": {"max_skew_s": S.CORROB_MAX_SKEW_S},
                "distance_stop_log": {"min_delay_s": 0.0, "max_delay_s": S.TEMPORAL_DIST_STOP_MAX_S},
            },
        },
        "ground_truth": {
            "root_cause": doc.get("root_cause",
                                  "A synthetic obstacle entered the configured demo safety zone ahead; "
                                  "front distance crossed the demo threshold and the safety controller commanded a stop."),
            "primary_conclusion_id": "concl_obstacle_stop",
        },
        "media": {"charts": "charts/", "lidar_fps": 5},
    }


def annotations():
    # 数值全部从 scenario 计算,避免改场景后漂移
    return {
        "incident_id": S.INCIDENT_ID,
        "evidence": [
            {"id": "ev_obstacle_lidar", "modality": "lidar", "t": S.EV_LIDAR_T,
             "ref": lidar_ref(S.EV_LIDAR_T), "object_label": S.LABELS.get("lidar", "obstacle"),
             "expected_observation": f"A dense return cluster at ~{S.front_distance(S.EV_LIDAR_T):.2f} m "
                                     f"directly ahead of the robot."},
            {"id": "ev_front_distance", "modality": "metric", "t": S.EV_DIST_T,
             "ref": "charts/front_distance.png", "object_label": S.LABELS.get("dist", "obstacle"),
             "expected_observation": f"Front distance drops below the {S.FRONT_SAFETY_M} m demo threshold.",
             "metric": {"name": "front_distance_m", "value": round(S.front_distance(S.EV_DIST_T), 3)}},
            {"id": "ev_stop_event", "modality": "log", "t": S.EV_STOP_T,
             "ref": f"logs.jsonl#{S.CODE_STOP}", "code": S.CODE_STOP,
             "expected_observation": "Safety controller logged an obstacle stop event."},
            {"id": "ev_velocity_halt", "modality": "metric", "t": S.EV_HALT_T,
             "ref": "charts/velocity.png",
             "expected_observation": f"Planner still requests {S.PLANNER_V:.2f} m/s but applied command is "
                                     f"clamped to 0 and actual velocity decelerates to 0.",
             "metric": {"name": "actual_speed_mps", "value": round(S.actual_speed(S.EV_HALT_T), 3)}},
        ],
        "conclusions": [
            {"id": "concl_obstacle_stop",
             "statement": "The robot stopped because an obstacle entered the demo safety zone ahead.",
             "required_evidence": ["ev_obstacle_lidar", "ev_front_distance", "ev_stop_event", "ev_velocity_halt"],
             "corroboration_groups": [["ev_obstacle_lidar", "ev_front_distance"]],
             "temporal_checks": [
                 {"before": "ev_front_distance", "after": "ev_stop_event",
                  "min_delay_s": 0.0, "max_delay_s": S.TEMPORAL_DIST_STOP_MAX_S},
                 {"before": "ev_stop_event", "after": "ev_velocity_halt",
                  "min_delay_s": 0.0, "max_delay_s": S.TEMPORAL_STOP_HALT_MAX_S},
             ],
             "metric_checks": [
                 {"name": "front_distance_m", "op": "<", "threshold": S.FRONT_SAFETY_M,
                  "evidence_id": "ev_front_distance"},
                 {"name": "actual_speed_mps", "op": "<=", "threshold": S.HALT_SPEED_EPS,
                  "evidence_id": "ev_velocity_halt"},
             ]},
        ],
        "stateful_events": [
            {"code": S.CODE_STOP, "kind": "assert", "clears": S.CODE_CLEAR},
            {"code": S.CODE_CLEAR, "kind": "clear"},
        ],
        "recovery": {
            "conditions": [
                {"id": "rc_obstacle_cleared", "label": "Obstacle removed from safety zone",
                 "check": {"metric": "front_distance_m", "op": ">=", "threshold": S.FRONT_SAFETY_M,
                           "aggregation": "continuous_at_end", "duration_s": S.RECOVERY_DUR_S}},
                {"id": "rc_stop_cleared", "label": "Obstacle-stop event cleared",
                 "check": {"event_state": S.CODE_STOP, "must_be": "cleared"}},
            ],
        },
    }


def main():
    bag = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "synthetic_bag" / "demo_obstacle_stop_01.bag"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "incident"
    (out / "lidar_frames").mkdir(parents=True, exist_ok=True)
    (out / "charts").mkdir(parents=True, exist_ok=True)

    series, scans, events = read_bag(bag)
    times = sorted(series["front"].keys())

    # --- lidar 帧:每 0.2s 渲染一张 ---
    lidar_track = []
    rendered = 0
    for t in sorted(scans.keys()):
        if int(round(t * 10)) % 2 != 0:   # 0.2s 网格
            continue
        ranges, amin, ainc = scans[t]
        name = f"{int(round(t * 100))}.png"
        render_lidar(out / "lidar_frames" / name, ranges, amin, ainc, thr=S.FRONT_SAFETY_M)
        lidar_track.append({"t": t, "ref": f"lidar_frames/{name}"})
        rendered += 1

    # --- charts ---
    tmax = S.DURATION_S
    fr = [(t, series["front"][t]) for t in times]
    _plot(out / "charts" / "front_distance.png", [("front_distance", fr)],
          0.0, tmax, (0.0, round(S.FRONT_NOMINAL_M + 0.4, 1)), "front_distance (m)",
          hlines=[(S.FRONT_SAFETY_M, (200, 60, 0))])
    vmax = S.PLANNER_V + 0.2
    _plot(out / "charts" / "velocity.png",
          [("planner", [(t, series["planner"][t]) for t in times]),
           ("applied", [(t, series["applied"][t]) for t in times]),
           ("actual",  [(t, series["actual"][t]) for t in times])],
          0.0, tmax, (-0.05, vmax), "velocity (m/s)")
    smap = {"OK": 0, "STOP": 1}
    _plot(out / "charts" / "safety_state.png",
          [("safety_state", [(t, smap[series["safety"][t]]) for t in times])],
          0.0, tmax, (-0.1, 1.2), "OK=0 / STOP=1", step=True)

    # --- timeline.json ---
    metrics = []
    for t in times:
        m = {"t": t,
             "planner_speed_mps": round(series["planner"][t], 3),
             "applied_speed_mps": round(series["applied"][t], 3),
             "actual_speed_mps": round(series["actual"][t], 3),
             "front_distance_m": round(series["front"][t], 3),
             "safety_state": series["safety"][t]}
        sc = scans.get(t)
        if sc is not None:
            fmr = front_min_range(sc[0], sc[1], sc[2])
            m["front_min_range_m"] = round(fmr, 3) if fmr is not None else None
        metrics.append(m)
    timeline = {"incident_id": S.INCIDENT_ID, "t_start": 0.0, "t_end": S.DURATION_S,
                "tracks": {"lidar": lidar_track,
                           "charts": [
                               {"t": 0.0, "ref": "charts/front_distance.png", "kind": "front_distance_vs_threshold"},
                               {"t": 0.0, "ref": "charts/velocity.png", "kind": "cmd_vs_actual_velocity"},
                               {"t": 0.0, "ref": "charts/safety_state.png", "kind": "safety_state_steps"}],
                           "metrics": metrics}}
    (out / "timeline.json").write_text(json.dumps(timeline, indent=2))

    # --- logs.jsonl ---
    halt_t = next((t for t in times if t >= S.T_STOP and series["actual"][t] <= S.HALT_SPEED_EPS), S.T_HALT)
    logs = [{"t": 0.0, "level": "INFO", "node": "demo_safety_controller",
             "code": "DEMO_NAV_RUNNING", "message": f"Navigating at {S.PLANNER_V:.1f} m/s."}]
    for (t, code, kind) in events:
        if kind == "assert":
            logs.append({"t": t, "level": "WARN", "node": "demo_safety_controller", "code": code,
                         "message": "Synthetic obstacle entered the configured demo safety zone ahead "
                                    "(front distance below threshold)."})
        else:
            logs.append({"t": t, "level": "INFO", "node": "demo_safety_controller", "code": code,
                         "message": "Synthetic obstacle cleared from the configured demo safety zone."})
    logs.append({"t": round(halt_t, 1), "level": "INFO", "node": "demo_motion",
                 "code": "DEMO_MOTION_HALTED", "message": "Velocity reached 0."})
    logs.sort(key=lambda x: x["t"])
    (out / "logs.jsonl").write_text("\n".join(json.dumps(x) for x in logs) + "\n")

    # --- 授权写死的 json ---
    (out / "metadata.json").write_text(json.dumps(metadata(), indent=2))
    (out / "annotations.json").write_text(json.dumps(annotations(), indent=2))

    print(f"[export] out={out}")
    print(f"[export] lidar_frames={rendered}  charts=3  metrics={len(metrics)}  "
          f"logs={len(logs)}  events={len(events)}  halt_t={round(halt_t,1)}")


if __name__ == "__main__":
    main()
