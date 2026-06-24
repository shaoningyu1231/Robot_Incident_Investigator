"""Scenario parameters loaded from a JSON config (zero dependency).

Config = numbers (scenarios/*.json, enumerable for the evaluator). Logic = the
piecewise SHAPE of each signal, which stays here. Default config is
scenarios/obstacle_stop.json; override via the SCENARIO_CONFIG env var.

Only describes fully synthetic data; never reads any real bag.
"""
import json
import os
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[1] / "scenarios" / "obstacle_stop.json"
CONFIG_PATH = Path(os.environ.get("SCENARIO_CONFIG", _DEFAULT))
_C = json.loads(CONFIG_PATH.read_text())

# --- identity / sampling ---
INCIDENT_ID = _C["incident_id"]
DURATION_S = _C["duration_s"]
HZ = _C["hz"]
DT = 1.0 / HZ
BASE_EPOCH_S = _C["base_epoch_s"]          # fixed epoch → reproducible bag

# --- thresholds / codes ---
FRONT_SAFETY_M = _C["front_safety_m"]
CODE_STOP = _C["codes"]["stop"]
CODE_CLEAR = _C["codes"]["clear"]

# --- key moments (seconds, on the 0.1 grid) ---
_t = _C["times"]
T_OBST_APPEAR = _t["obst_appear"]
T_DROP_END = _t["drop_end"]
T_STOP = _t["stop"]
T_HALT = _t["halt"]
T_OBST_REMOVE = _t["obst_remove"]
T_CLEAR = _t["clear"]
T_RISE_END = _t["rise_end"]
T_RESUME_END = _t["resume_end"]

# --- evidence anchors (annotations generated from these) ---
_e = _C["evidence_t"]
EV_LIDAR_T = _e["lidar"]
EV_DIST_T = _e["dist"]
EV_STOP_T = _e["stop"]
EV_HALT_T = _e["halt"]

# --- rule parameters ---
_r = _C["rules"]
CORROB_MAX_SKEW_S = _r["corrob_max_skew_s"]
TEMPORAL_DIST_STOP_MAX_S = _r["temporal_dist_stop_max_s"]
TEMPORAL_STOP_HALT_MAX_S = _r["temporal_stop_halt_max_s"]
RECOVERY_DUR_S = _r["recovery_dur_s"]
HALT_SPEED_EPS = _r["halt_speed_eps"]

# --- nominal values ---
_v = _C["values"]
PLANNER_V = _v["planner_v"]
ACTUAL_V = _v["actual_v"]
FRONT_NOMINAL_M = _v["front_nominal_m"]
FRONT_OBSTACLE_M = _v["front_obstacle_m"]

FRAMES = _C["frames"]

# --- shape + events + doc (optional; absent → obstacle defaults, hero stays byte-identical) ---
SHAPE = _C.get("shape", "obstacle")             # "obstacle" | "planned"
EVENTS = _C.get("events", [                       # default = obstacle assert/clear pair
    {"t": T_STOP, "code": CODE_STOP, "kind": "assert"},
    {"t": T_CLEAR, "code": CODE_CLEAR, "kind": "clear"},
])
DOC = _C.get("doc", {})                           # export reads title/description/scenario_label/root_cause
LABELS = _C.get("labels", {"lidar": "obstacle", "dist": "obstacle"})  # semantic labels per evidence
_pd = _C.get("planner_decel", {})                 # planned-stop deceleration window
PLANNER_DECEL_START = _pd.get("start", T_OBST_APPEAR)
PLANNER_DECEL_END = _pd.get("end", T_DROP_END)


def _lerp(a, b, x0, x1, t):
    return a + (b - a) * (t - x0) / (x1 - x0)


def front_distance(t):
    if SHAPE == "planned":
        return FRONT_NOMINAL_M                    # planned stop: no obstacle, distance stays clear
    if t < T_OBST_APPEAR:
        return FRONT_NOMINAL_M
    if t < T_DROP_END:
        return _lerp(FRONT_NOMINAL_M, FRONT_OBSTACLE_M, T_OBST_APPEAR, T_DROP_END, t)
    if t < T_OBST_REMOVE:
        return FRONT_OBSTACLE_M
    if t < T_RISE_END:
        return _lerp(FRONT_OBSTACLE_M, FRONT_NOMINAL_M, T_OBST_REMOVE, T_RISE_END, t)
    return FRONT_NOMINAL_M


def planner_speed(t):
    if SHAPE == "planned":                        # planner actively ramps speed to 0
        if t < PLANNER_DECEL_START:
            return PLANNER_V
        if t < PLANNER_DECEL_END:
            return _lerp(PLANNER_V, 0.0, PLANNER_DECEL_START, PLANNER_DECEL_END, t)
        return 0.0
    return PLANNER_V  # obstacle: planner keeps requesting motion the whole time


def applied_speed(t):
    if SHAPE == "planned":
        return planner_speed(t)                   # no safety override; applied follows planner
    # obstacle: safety controller clamps the applied command to 0 during the stop
    if SHAPE == "obstacle" and T_STOP <= t < T_CLEAR:
        return 0.0
    return PLANNER_V                              # sensor_disagreement: keeps driving (no real stop)


def actual_speed(t):
    if SHAPE == "planned":
        return planner_speed(t)                   # follows the planned decel
    if SHAPE == "obstacle":
        if t < T_STOP:
            return ACTUAL_V
        if t < T_HALT:
            return _lerp(ACTUAL_V, 0.0, T_STOP, T_HALT, t)
        if t < T_CLEAR:
            return 0.0
        if t < T_RESUME_END:
            return _lerp(0.0, ACTUAL_V, T_CLEAR, T_RESUME_END, t)
        return ACTUAL_V
    return ACTUAL_V                               # sensor_disagreement: normal driving


def safety_state(t):
    if SHAPE == "obstacle":
        return "STOP" if T_STOP <= t < T_CLEAR else "OK"
    return "OK"                                   # planned / sensor_disagreement: safety never engages


def obstacle_present(t):
    # only the obstacle scenario carries a real return cluster in the scan front sector
    return SHAPE == "obstacle" and (T_OBST_APPEAR <= t < T_OBST_REMOVE)


def samples():
    """10 Hz sample times over [0, DURATION] (snapped to 0.1)."""
    n = int(round(DURATION_S * HZ)) + 1
    return [round(i * DT, 3) for i in range(n)]
