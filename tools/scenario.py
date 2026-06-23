"""Hero 案例的合成场景定义(单一事实来源)。

仅描述全合成数据,绝不读取真实 bag。数值与 ../schema.md 的 annotations.json 对齐。
"""

# --- 标识 ---
INCIDENT_ID = "demo_obstacle_stop_01"

# --- 时间与采样 ---
DURATION_S = 25.0
HZ = 10
DT = 1.0 / HZ
BASE_EPOCH_S = 1735689600  # 写死基准 epoch,保证可复现(2025-01-01 UTC)

# --- 阈值与码 ---
FRONT_SAFETY_M = 1.2
CODE_STOP = "DEMO_OBSTACLE_STOP_01"
CODE_CLEAR = "DEMO_OBSTACLE_CLEAR_01"

# --- 关键时刻(秒,均落在 0.1 网格上)---
T_NAV = 0.0
T_OBST_APPEAR = 10.0
T_DROP_END = 10.4     # front_distance 到达 0.74(障碍簇此刻已 ~0.74m)
T_STOP = 10.6         # 安全停车事件 assert
T_HALT = 11.3         # 实际速度归零
T_OBST_REMOVE = 20.2  # 障碍移除,front_distance 开始回升
T_CLEAR = 20.4        # 安全事件 clear
T_RISE_END = 20.8     # front_distance 回到 2.6
T_RESUME_END = 21.1   # 实际速度恢复到名义值

# --- 证据锚点(annotations 据此生成,确保不漂移)---
EV_LIDAR_T = T_DROP_END   # 10.4:LiDAR 簇已达 0.74m
EV_DIST_T = 10.5          # front_distance 处于 0.74 平台
EV_STOP_T = T_STOP        # 10.6
EV_HALT_T = T_HALT        # 11.3

# --- 规则参数 ---
CORROB_MAX_SKEW_S = 0.2
TEMPORAL_DIST_STOP_MAX_S = 0.3
TEMPORAL_STOP_HALT_MAX_S = 1.0
RECOVERY_DUR_S = 1.0
HALT_SPEED_EPS = 0.01     # 实际速度 ≤ 此值视为已停;halt 检测与 metric_check 共用

# --- 名义值 ---
PLANNER_V = 0.80
ACTUAL_V = 0.79
FRONT_NOMINAL_M = 2.6
FRONT_OBSTACLE_M = 0.74

FRAMES = ["demo_map", "demo_odom", "demo_base_link"]


def _lerp(a, b, x0, x1, t):
    return a + (b - a) * (t - x0) / (x1 - x0)


def front_distance(t):
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
    return PLANNER_V  # 规划器全程想走


def applied_speed(t):
    # 安全控制器在停车段把命令钳为 0
    if T_STOP <= t < T_CLEAR:
        return 0.0
    return PLANNER_V


def actual_speed(t):
    if t < T_STOP:
        return ACTUAL_V
    if t < T_HALT:
        return _lerp(ACTUAL_V, 0.0, T_STOP, T_HALT, t)
    if t < T_CLEAR:
        return 0.0
    if t < T_RESUME_END:
        return _lerp(0.0, ACTUAL_V, T_CLEAR, T_RESUME_END, t)
    return ACTUAL_V


def safety_state(t):
    return "STOP" if T_STOP <= t < T_CLEAR else "OK"


def obstacle_present(t):
    # scan 前向扇区出现障碍簇的时间段(障碍物理存在直到移除)
    return T_OBST_APPEAR <= t < T_OBST_REMOVE


def samples():
    """生成 [0, DURATION] 上 10Hz 的时间点(规整到 0.1)。"""
    n = int(round(DURATION_S * HZ)) + 1
    return [round(i * DT, 3) for i in range(n)]
