#!/usr/bin/env python3
"""Export the synthetic incident to a Rerun .rrd recording (v0.2 spike).

Reuses export_incident_assets.read_bag() so the .rrd shares the exact same
synthetic source as timeline.json / the incident assets and cannot drift.
Reads the synthetic bag only; never real bags.

Dev-only tool: rerun-sdk is a dev dependency (requirements-dev.txt) and is NOT
part of the Cloud Run runtime. This does not touch the backend, frontend, or
Gemini path — it only writes a .rrd into the git-ignored rerun_build/ dir.

Usage: <venv>/python3 tools/export_to_rerun.py [in.bag] [out.rrd]
"""
import sys
import math
from pathlib import Path

import numpy as np
import rerun as rr
import rerun.blueprint as rrb

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_incident_assets as E  # noqa: E402  (reused: read_bag / annotations)
import scenario as S  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = "incident_time"


def _lidar_xy(ranges, angle_min, angle_inc, rmax=4.0):
    """Top-down 2D scatter in meters: forward = +Y, left/right = X."""
    thr = S.FRONT_SAFETY_M
    xs, cols, radii = [], [], []
    for i, r in enumerate(ranges):
        if r <= 0 or r > rmax:
            continue
        ang = angle_min + i * angle_inc
        xs.append((r * math.sin(ang), r * math.cos(ang)))
        near = r < thr
        cols.append((200, 0, 0) if near else (120, 120, 120))
        radii.append(0.035 if near else 0.02)
    pos = np.asarray(xs, dtype=float) if xs else np.zeros((0, 2))
    return pos, cols, radii


def _safety_ring(thr, n=64):
    return [(thr * math.sin(2 * math.pi * k / n), thr * math.cos(2 * math.pi * k / n))
            for k in range(n + 1)]


def _blueprint():
    """Default GIF-ready layout: LiDAR left, metrics center, logs/evidence right."""
    return rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(origin="/lidar", name="LiDAR (top-down)"),
            rrb.Vertical(
                rrb.TimeSeriesView(
                    name="Front distance vs threshold",
                    contents=["/metrics/front_distance", "/metrics/front_distance_threshold"],
                ),
                rrb.TimeSeriesView(
                    name="Velocity: planner / applied / actual",
                    contents="/metrics/velocity/**",
                ),
                rrb.TimeSeriesView(
                    name="Safety state (0=OK, 1=STOP)",
                    contents="/metrics/safety_state",
                ),
            ),
            rrb.Vertical(
                rrb.TextLogView(origin="/logs", name="Event log"),
                rrb.TextLogView(origin="/evidence", name="Evidence"),
            ),
            column_shares=[0.5, 0.3, 0.2],
        ),
        collapse_panels=True,
    )


def main():
    bag = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        ROOT / "synthetic_bag" / "demo_obstacle_stop_01.bag"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else \
        ROOT / "rerun_build" / "demo_obstacle_stop_01.rrd"
    out.parent.mkdir(parents=True, exist_ok=True)

    series, scans, events = E.read_bag(bag)
    ann = E.annotations()

    rr.init("robot_incident_investigator", spawn=False)
    rr.save(str(out), default_blueprint=_blueprint())

    # --- static geometry: safety ring + robot origin (front = +Y) ---
    rr.log("/lidar/safety_ring",
           rr.LineStrips2D([_safety_ring(S.FRONT_SAFETY_M)], colors=[(200, 60, 0)]),
           static=True)
    rr.log("/lidar/robot",
           rr.Points2D([[0.0, 0.0]], colors=[(0, 0, 0)], radii=[0.05]),
           static=True)

    # --- LiDAR frames along the incident timeline ---
    n_scans = 0
    for t in sorted(scans.keys()):
        ranges, amin, ainc = scans[t]
        pos, cols, radii = _lidar_xy(ranges, amin, ainc)
        rr.set_time(TIMELINE, duration=t)
        rr.log("/lidar/scan", rr.Points2D(pos, colors=cols, radii=radii))
        n_scans += 1

    # --- scalar timeseries (same source as timeline.json metrics) ---
    times = sorted(series["front"].keys())
    smap = {"OK": 0.0, "STOP": 1.0}
    for t in times:
        rr.set_time(TIMELINE, duration=t)
        rr.log("/metrics/front_distance", rr.Scalars(series["front"][t]))
        rr.log("/metrics/front_distance_threshold", rr.Scalars(S.FRONT_SAFETY_M))
        rr.log("/metrics/velocity/planner", rr.Scalars(series["planner"][t]))
        rr.log("/metrics/velocity/applied", rr.Scalars(series["applied"][t]))
        rr.log("/metrics/velocity/actual", rr.Scalars(series["actual"][t]))
        rr.log("/metrics/safety_state", rr.Scalars(smap[series["safety"][t]]))

    # --- event logs -> TextLog stream ---
    rr.set_time(TIMELINE, duration=0.0)
    rr.log("/logs", rr.TextLog(f"Navigating at {S.PLANNER_V:.1f} m/s.", level="INFO"))
    for (t, code, kind) in events:
        rr.set_time(TIMELINE, duration=t)
        if kind == "assert":
            rr.log("/logs", rr.TextLog(
                f"{code}: synthetic obstacle entered the demo safety zone "
                f"(front distance below threshold).", level="WARN"))
        else:
            rr.log("/logs", rr.TextLog(
                f"{code}: synthetic obstacle cleared from the demo safety zone.",
                level="INFO"))

    # --- evidence markers -> one entity per annotation evidence id ---
    n_ev = 0
    for ev in ann["evidence"]:
        rr.set_time(TIMELINE, duration=ev["t"])
        rr.log(f"/evidence/{ev['id']}",
               rr.TextLog(f"[{ev['modality']}] {ev['expected_observation']}", level="INFO"))
        n_ev += 1

    print(f"[rerun] out={out}")
    print(f"[rerun] scans={n_scans}  metric_steps={len(times)}  "
          f"events={len(events)}  evidence={n_ev}")


if __name__ == "__main__":
    main()
