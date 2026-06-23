# 合成 bag 生成 + 资产导出流程

赛前一次性把 hero(+ 备用)案例做成固定资产。**现场后端只读导出的资产,不碰 bag、不解析 rosbag。** 合成 bag 的作用是"数据源是真 ROS bag、可信"的叙事 + 资产来源,不进运行链路。

数据策略与边界见 [non_sensitive.md](non_sensitive.md);资产字段见 [schema.md](schema.md)。

---

## 真 bag 的用途(已完成的一步)

`sample_data/v63mp052_...err-105.bag` **仅供人工理解 topic 形态**,不进仓库/云端/Gemini。从它学到的、写进合成生成脚本的事实:

- 真机 50 Hz 发这些遥测:`/safety_status`、逐方向 `/front_lidar_distance` 等(Float32)、`/stop_distances`(Float32MultiArray)、`/scan`(LaserScan)、`/cmd_vel_*`、`/wheel_velocity`、`/diff_drive_controller/odom`、`/current_state`(String)、`/error_codes`、`/emergency_stop_reason`(JSON String)。
- 真实 err-105 = `diagnostic_emergency_stop`(诊断急停)+ 稳态,无事件弧 → 不适合 hero,故 hero 走**合成的障碍停车**,不叫 105。

合成 bag **仿照这些 topic 的形态**,但用 `demo_` 前缀 + 标准消息类型(避免拖进 `lexxauto_msgs` 自定义类型,也不复刻真实命名约定)。

---

## 合成 bag:topic 与消息类型

只放演示必需的 topic,全部标准类型(`rosbags` ROS1_NOETIC typestore 自带,无需自定义 msgdef):

```
/demo/scan              sensor_msgs/msg/LaserScan  10 Hz   前向 LiDAR 扫描
/demo/front_distance    std_msgs/msg/Float32       10 Hz   前方最近障碍距离 (m)
/demo/planner_cmd_vel   geometry_msgs/msg/Twist    10 Hz   规划器请求速度(停车段仍 0.80)
/demo/applied_cmd_vel   geometry_msgs/msg/Twist    10 Hz   安全控制器钳制后的速度(停车段 0)
/demo/odom              nav_msgs/msg/Odometry      10 Hz   实际速度 (twist.twist.linear.x)
/demo/safety_state      std_msgs/msg/String        10 Hz   "OK" / "STOP"
/demo/error_events      std_msgs/msg/String        事件触发  JSON: {"code","kind"}
```

- 速度拆 **planner → applied → actual** 三条,显式展示安全链路(planner request 0.8 → safety override → applied 0 → actual 0),比单一 cmd_vel 更有诊断价值,也避免"控制器失效"的误读。
- frame_id 用 `demo_base_link` / `demo_map`,不用真实 frame。
- `/demo/error_events` 的 payload:`{"code":"DEMO_OBSTACLE_STOP_01","kind":"assert"}` / `{"code":"DEMO_OBSTACLE_CLEAR_01","kind":"clear"}`。

---

## hero 时间线(约 25s,合成生成的目标曲线)

```
t (s)     front_dist  planner_v  applied_v  actual_v  safety  事件
0.0–10.0   ~2.6        0.80       0.80       ~0.79     OK      DEMO_NAV_RUNNING@0.0
10.0       开始下降                                            障碍出现(线性降 10.0→10.4)
~10.3      跨过 1.2                                            (阈值穿越)
10.4       0.74        0.80       0.80       0.79      OK       (簇已到 0.74,stop 尚未 assert)
10.6       0.74        0.80       0.00       0.79→     STOP    DEMO_OBSTACLE_STOP_01 (assert)
10.6–11.3  0.74        0.80       0.00       0.79→0.0  STOP     (规划仍请求/钳制为0/实际减速)
11.3       0.74        0.80       0.00       0.00      STOP    DEMO_MOTION_HALTED@11.3
11.3–20.2  0.74        0.80       0.00       0.00      STOP     (停车,障碍仍在)
20.2       回升                                                障碍移除(线性升 20.2→20.8)
20.4       回升中      0.80       0.80*      0.00→     OK      DEMO_OBSTACLE_CLEAR_01 (clear)
20.4–25.0  ~2.6        0.80       0.80       →0.79     OK       (可选:恢复行驶)
```

- **数值单一事实来源是 `tools/scenario.py`**;`metadata.json` / `annotations.json` 由 `export_incident_assets.py` 从 scenario 常量计算生成,改场景不会漂移。本表仅为人读概览。
- front 在 **10.4** 到 0.74(`T_DROP_END`),与 `ev_obstacle_lidar@10.4` 标注一致;阈值穿越约在 10.3。
- 关键对比:停车段 **planner 一直 0.80、applied 钳为 0、actual 0** —— 把"是安全链路主动钳停,不是控制器失效"讲清楚。`*` clear 后 applied 解钳恢复跟随 planner。
- 这些数值与 `scenario.py` / `annotations.json` 一致(`front_distance_m=0.74`、阈值 `1.2`、stop@10.6、halt@11.3、clear@20.4)。

---

## 生成脚本设计(用现有分析器 venv)

用 `claude-mcp-rosbags/venv`(已装 `rosbags`)写,不另装依赖。

- `rosbags.rosbag1.Writer` 打开 `demo_obstacle_stop_01.bag`。
- 为每个 topic `add_connection(topic, msgtype, typestore=ROS1_NOETIC)`。
- 按 10 Hz 循环 `t = 0.00, 0.10, … 25.00`,用上面时间线的分段函数算各值:
  - `front_distance`:分段 —— 平台 2.6 → 线性降 → 平台 0.74 → 线性升 → 平台 2.6。
  - `planner_cmd_vel`:全程 0.80(规划器一直想走)。
  - `applied_cmd_vel`:正常 0.80;`[10.6, 20.4]` 钳为 0;clear 后恢复 0.80。
  - `actual`(odom):正常 0.79 → 10.6 后衰减到 0 → 停 → clear 后(可选)恢复。
  - `safety_state`:10.6 翻 STOP,20.4 翻 OK。
  - `/demo/scan`:基线为远距离 ranges 数组,障碍段在正前方扇区注入 ~0.74m 的低值簇 + 少量噪声。
- 事件:在 10.6 / 20.4 写 `/demo/error_events`。
- 时间戳:bag 内用合成基准 epoch + t(脚本里 `Date.now()` 不可用的限制是工作流脚本的事,这里是普通 Python,可正常取时间;但为可复现建议**写死一个基准 epoch**)。

产物:一个几 MB 的小 bag,可重跑、可对拍。

---

## 资产导出(bag → 固定格式)

读合成 bag,导出 [schema.md](schema.md) 规定的资产到 `incident/` 目录:

- **`lidar_frames/`** —— 把 `/demo/scan` 每帧(或每 ~0.1s)渲染成极坐标 png,文件名按毫秒 `1040.png`(t=10.40s)。可直接复用分析器的 `extractors/lidar.py` / `visualization.py`。
- **`charts/`** —— 三张整段曲线图:
  - `front_distance.png`:front_distance 曲线 + 1.2m 阈值横线。
  - `velocity.png`:**planner / applied / actual 三条**速度曲线(展示钳停链路)。
  - `safety_state.png`:OK/STOP 阶梯图。
- **`timeline.json`** —— 10 Hz 的 `metrics` 轨(planner/applied/actual 速度、front_distance、safety_state)+ `lidar` 帧引用 + `charts` 引用。
- **`logs.jsonl`** —— 从 `/demo/error_events` 转出事件行,补 `DEMO_NAV_RUNNING@0` / `DEMO_MOTION_HALTED@11.3` 两条 INFO。
- **`annotations.json`** —— 由 `export_incident_assets.py` 从 `scenario.py` 常量**计算生成**(非手敲魔数):ground truth + 4 条 evidence(含 `ev_velocity_halt`)+ 1 个 conclusion + 两条 temporal_checks + 两条 metric_checks(`front_distance_m<1.2`、`actual_speed_mps<=0.01`)+ stateful_events + recovery(**只含规则,无窗口**)。这是规范性标注来源。
- **`metadata.json`** —— 手写顶层描述(含 "fully synthetic … inspired by common AMR failure patterns" 定性、`demo_thresholds.front_safety_m=1.2`、synchronization 块)。

---

## 导出后对拍(确认规则实现正确)

赛前用这几条断言验证后端规则与数据一致(任何一条不符就改数据或改阈值):

- `evidence_strength(concl_obstacle_stop, window=[9.5,11.8])` → `high`(四项核查全 true:**四**证据齐含 velocity_halt、lidar↔distance 偏差 ≤0.2s、两条 temporal_check 满足(distance→stop、stop→halt)、object_label 都是 obstacle、front_distance<1.2)。窗口要含到 11.3 的 halt。
- `evidence_strength(concl_obstacle_stop, window=[9.5,10.8])` → `low`(halt 还没发生 → `ev_velocity_halt` 缺 → `required_present=false`)。这正是加 velocity_halt 进必需证据要防的"还没真停就判 high"。
- `evidence_strength(concl_obstacle_stop, window=[0,10])` → `low`(正常段取不到任何事故证据)。
- **篡改测试**:删掉 stop 日志后,`[9.5,11.8]` 必须 **不再是 high**(`required_present=false`)。校验器按 modality 真核对资产存在(lidar 帧文件、metric 实际采样、log 条目),不只看时间窗;否则删证据仍判 high。
- `continuous_at_end` 还校验末段 `duration_s` 内**采样完整覆盖、无大空洞**,缺采样判 `unknown` 而非默认满足。
- `inspect_incident_window([0,10])` 取不到 stop 事件 → `required_present=false` → `low`(正常段没有事故证据)。
- `check_recovery_readiness(evaluation_window=[12,18])`(障碍仍在)→ `blocked`(front_distance 末段 1s 持续 0.74 < 1.2;stop 仍 active)。
- `check_recovery_readiness(evaluation_window=[19,25])`(障碍移除 + clear 事件)→ `conditions_met`。两点保证:① front_distance 用 `continuous_at_end`/`duration_s=1.0`,末段 `[24,25]` 持续 ≥1.2(障碍 20.2 已移除),窗口前半段还有障碍**不影响**;② event_state **从时间轴起点扫描、截止窗口末**,clear@20.4 被算进 → cleared。

对拍脚本和生成/导出脚本一起放 `tools/`(仓库内,因为只处理合成数据)。

当前实现结果:

- 完整流水线 `generate → export → validate` 已跑通。
- 合成 bag 为 25s / 10Hz / 251 个采样点 / 7 topics,固定随机种子可复现。
- 导出 126 张 LiDAR PNG、3 张 chart、251 条 metrics 和 4 条结构化日志。
- `validate_incident.py` 使用共享的 `tools/incident_rules.py`,当前 `20/20 PASS`。
- 篡改回归覆盖删除 stop 日志和把 halt 速度改成非零,两种情况下都不能继续得到 `high`。
- 后端也复用同一规则模块;不会出现 validator 与运行时各自维护一套判断逻辑。

后端与在线 Gemini 的当前状态见 [progress.md](progress.md)。

---

## 边界 checklist(生成/导出环节)

- bag 名、frame、topic、namespace、错误码全部 `demo_` / 虚构;无真实命名约定。
- 不含相机帧、地图轮廓、真实坐标序列、序列号、内部错误码、真实日志原文。
- 真 bag 不出现在 `incident/`、仓库、云端或 Gemini 请求里。
- `metadata.json` 标 `synthetic: true` + 定性描述;前端显示 "fully synthetic"。
- 全流程过一遍 [non_sensitive.md](non_sensitive.md) 的发布前 checklist。
