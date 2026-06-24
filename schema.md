# 数据与工具 Schema

本文件定义所有演示资产格式与后端工具契约。所有示例均为**全合成数据**(见 [non_sensitive.md](non_sensitive.md)),命名一律 `demo_` 前缀、虚构错误码。

核心原则:**`annotations.json` 是后端确定性计算的"规范性标注来源"** —— 它声明"应当观察到什么、哪些证据应互相印证、结论依赖什么"。它不是唯一事实来源:**资产是否存在**来自媒体文件,**实际指标值**来自 `timeline.json`,**实际日志**来自 `logs.jsonl`。后端把 annotations 的规范 × 这些实际数据做规则核查,绝不让普通后端去"看图判断",也不问 Gemini。

---

## metadata.json

案例顶层描述与 ground truth。

```json
{
  "incident_id": "demo_obstacle_stop_01",
  "title": "Synthetic obstacle-triggered safety stop",
  "description": "Fully synthetic; does not reproduce any real incident or bag.",
  "scenario_label": "Obstacle-triggered safety stop, inspired by common AMR failure patterns.",
  "synthetic": true,
  "modalities": ["lidar", "metrics", "log"],
  "robot": { "id": "demo_bot_01", "type": "demo_amr" },
  "frames": { "map": "demo_map", "odom": "demo_odom", "base_link": "demo_base_link" },
  "duration_s": 25.0,
  "demo_thresholds": { "front_safety_m": 1.2 },
  "synchronization": {
    "default_max_skew_s": 0.2,
    "relations": {
      "lidar_distance": { "max_skew_s": 0.2 },
      "distance_stop_log": { "min_delay_s": 0.0, "max_delay_s": 0.3 }
    }
  },
  "ground_truth": {
    "root_cause": "A synthetic obstacle entered the configured demo safety zone ahead; front distance crossed the demo threshold and the safety controller commanded a stop.",
    "primary_conclusion_id": "concl_obstacle_stop"
  },
  "media": { "charts": "charts/", "lidar_fps": 10 }
}
```

- `synthetic: true` + `description` 必填,前端据此显示 "fully synthetic" 标注。公开 `description` **只写不复现真实事故/bag**,不出现任何真实错误码或内部故障名称。场景措辞放 `scenario_label`,且只使用通用 AMR 术语。
- `modalities` 固定为 `lidar / metrics / log`(无 camera)。
- `demo_thresholds` 是**演示专用**阈值,不代表真实设备参数。
- `primary_conclusion_id` 指向 `annotations.json` 里的主结论,demo 默认调查它。
- `synchronization` 区分两类时间约束(**不要**用单一全局容差):
  - **同步证据**(应同时发生,如 LiDAR 近距离回波 / front_distance 指标看同一障碍)→ 用 `max_skew_s` 判最大时间偏差。`default_max_skew_s` 是未声明关系时的回退值。
  - **因果事件**(应有先后 + 合理延迟,如 front_distance 跌破阈值 → 安全停车日志)→ 用 `min_delay_s` / `max_delay_s`,在 `annotations.json` 的 `temporal_checks` 里逐对声明。
  - 一律写成 `max_skew_s: 0.2` 这种**单侧上界**,不写 `±0.2s`(避免被理解成 0.4s 总跨度)。
  - 0.2 对 10 Hz 合成数据够用,但**最终值应按你实际生成数据的时间误差设定**。

---

## timeline.json

同步时间轴:把任一时刻映射到各模态的资产引用。后端据此实现 `seek` 与同步展示。

```json
{
  "incident_id": "demo_obstacle_stop_01",
  "t_start": 0.0,
  "t_end": 25.0,
  "tracks": {
    "lidar": [
      { "t": 10.4, "ref": "lidar_frames/1040.png" }
    ],
    "charts": [
      { "t": 0.0, "ref": "charts/front_distance.png", "kind": "front_distance_vs_threshold" },
      { "t": 0.0, "ref": "charts/velocity.png", "kind": "cmd_vs_actual_velocity" },
      { "t": 0.0, "ref": "charts/safety_state.png", "kind": "safety_state_steps" }
    ],
    "metrics": [
      { "t": 11.5, "planner_speed_mps": 0.80, "applied_speed_mps": 0.0, "actual_speed_mps": 0.0, "front_distance_m": 0.74, "safety_state": "STOP" }
    ]
  }
}
```

- 每条都带 `t`(相对秒),前端按 `t` 对齐多轨。
- 速度拆三条以显式展示安全链路:`planner_speed_mps`(规划器请求,停车段仍 0.80)→ `applied_speed_mps`(安全控制器钳制后,停车段 0)→ `actual_speed_mps`(实际,减速到 0)。单一 cmd_vel 会被误读成"控制器失效"。
- `lidar` 是逐帧渲染图(~10fps);`charts` 是整段曲线图(`t:0` 表示覆盖全程,前端在图上画游标);`metrics` 是逐采样数值轨,`inspect_incident_window` 从这里取窗口指标。
- 缺某模态某时刻的数据 → 该 track 直接没有那条,后端据"缺失"判 `insufficient_evidence`。

---

## logs.jsonl

每行一条结构化日志(非纯文本,保证搜索与证据定位稳定)。

```json
{"t":0.0,"level":"INFO","node":"demo_safety_controller","code":"DEMO_NAV_RUNNING","message":"Navigating at 0.8 m/s."}
{"t":10.6,"level":"WARN","node":"demo_safety_controller","code":"DEMO_OBSTACLE_STOP_01","message":"Synthetic obstacle entered the configured demo safety zone ahead (front distance below threshold)."}
{"t":11.3,"level":"INFO","node":"demo_motion","code":"DEMO_MOTION_HALTED","message":"Velocity reached 0."}
{"t":20.4,"level":"INFO","node":"demo_safety_controller","code":"DEMO_OBSTACLE_CLEAR_01","message":"Synthetic obstacle cleared from the configured demo safety zone."}
```

- 字段:`t`(相对秒)、`level`、`node`、`code`、`message`。
- `search_logs` 对 `code` / `message` / `node` 做子串或精确匹配。

---

## annotations.json(事实来源)

预置标注 + 结论定义。后端所有确定性判断都基于此。

```json
{
  "incident_id": "demo_obstacle_stop_01",
  "evidence": [
    {
      "id": "ev_obstacle_lidar",
      "modality": "lidar",
      "t": 10.4,
      "ref": "lidar_frames/1040.png",
      "object_label": "obstacle",
      "expected_observation": "A dense return cluster at ~0.74 m directly ahead of the robot."
    },
    {
      "id": "ev_front_distance",
      "modality": "metric",
      "t": 10.5,
      "ref": "charts/front_distance.png",
      "object_label": "obstacle",
      "expected_observation": "Front distance drops below the 1.2 m demo threshold.",
      "metric": { "name": "front_distance_m", "value": 0.74 }
    },
    {
      "id": "ev_stop_event",
      "modality": "log",
      "t": 10.6,
      "ref": "logs.jsonl#DEMO_OBSTACLE_STOP_01",
      "code": "DEMO_OBSTACLE_STOP_01",
      "expected_observation": "Safety controller logged an obstacle stop event."
    },
    {
      "id": "ev_velocity_halt",
      "modality": "metric",
      "t": 11.3,
      "ref": "charts/velocity.png",
      "expected_observation": "Planner still requests 0.80 m/s but applied command is clamped to 0 and actual velocity decelerates to 0.",
      "metric": { "name": "actual_speed_mps", "value": 0.0 }
    }
  ],
  "conclusions": [
    {
      "id": "concl_obstacle_stop",
      "statement": "The robot stopped because an obstacle entered the demo safety zone ahead.",
      "required_evidence": ["ev_obstacle_lidar", "ev_front_distance", "ev_stop_event", "ev_velocity_halt"],
      "corroboration_groups": [["ev_obstacle_lidar", "ev_front_distance"]],
      "temporal_checks": [
        { "before": "ev_front_distance", "after": "ev_stop_event",
          "min_delay_s": 0.0, "max_delay_s": 0.3 },
        { "before": "ev_stop_event", "after": "ev_velocity_halt",
          "min_delay_s": 0.0, "max_delay_s": 1.0 }
      ],
      "metric_checks": [
        { "name": "front_distance_m", "op": "<", "threshold": 1.2, "evidence_id": "ev_front_distance" },
        { "name": "actual_speed_mps", "op": "<=", "threshold": 0.01, "evidence_id": "ev_velocity_halt" }
      ]
    }
  ],
  "stateful_events": [
    { "code": "DEMO_OBSTACLE_STOP_01", "kind": "assert", "clears": "DEMO_OBSTACLE_CLEAR_01" },
    { "code": "DEMO_OBSTACLE_CLEAR_01", "kind": "clear" }
  ],
  "recovery": {
    "conditions": [
      { "id": "rc_obstacle_cleared", "label": "Obstacle removed from safety zone",
        "check": { "metric": "front_distance_m", "op": ">=", "threshold": 1.2,
                   "aggregation": "continuous_at_end", "duration_s": 1.0 } },
      { "id": "rc_stop_cleared", "label": "Obstacle-stop event cleared",
        "check": { "event_state": "DEMO_OBSTACLE_STOP_01", "must_be": "cleared" } }
    ]
  }
}
```

- evidence 横跨 **LiDAR(图)/ metric(距离、速度,对应 charts)/ log(事件)** 三模态,无相机。
- `required_evidence` 含 `ev_velocity_halt` —— 否则机器人还没真正停住也可能判 `high`。`stop_event → velocity_halt` 的因果延迟检查保证"先有 stop 事件、后有速度归零";另有 `actual_speed_mps <= 0.01` 的 metric_check 保证 halt 时速度**确实为零**(只检查"有采样"还不够)。
- 每条 evidence 带 `expected_observation` —— 合成时就写死的"应当观察到什么",后端拿它当事实,**不需要视觉模型**。
- `object_label` 跨模态相同(`obstacle`)→ LiDAR 近距离回波与 front_distance 指标指向同一障碍,用于 corroboration。
- **`recovery.conditions` 只保留规则,不含评估窗口** —— 评估窗口由 `check_recovery_readiness` 请求的 `evaluation_window` 决定(见下),不通过改 annotations 切换状态。
- `corroboration_groups`:声明哪几条应当互相印证(同 `object_label`)→ 走 `max_skew_s`(同步类)。
- `temporal_checks`:声明**因果先后**的 evidence 对(`before` → `after`)及允许延迟 `min_delay_s` / `max_delay_s` → 走因果类核查。未列入 corroboration 也未列入 temporal_checks 的证据对**不做时间比较**。
- `metric_checks`:结论依赖的数值阈值判断,指向某条 evidence 的 `metric`(实际值取自 `timeline.json`)。
- `stateful_events`:声明 assert/clear 配对。`absence_of_code` 只能证明"窗口内无新增报错",**不能证明已解除**;故恢复判断改用显式 clear 事件 / active-cleared 状态。
- `recovery.conditions`:恢复前置条件,每条只声明可机器核查的 `check`(指标阈值含聚合方式,或 `event_state`)。**评估窗口不在此声明**,由 `check_recovery_readiness` 请求的 `evaluation_window` 传入。

---

## 工具:inspect_incident_window

Gemini 的核心调查工具。返回窗口内事实 + 对落在窗口内的结论的 `evidence_strength`。

### 请求

```json
{
  "incident_id": "demo_obstacle_stop_01",
  "start": 9.5,
  "end": 11.5,
  "modalities": ["lidar", "metrics", "log"],
  "conclusion_id": "concl_obstacle_stop",
  "reason": "Investigating why the robot stopped around t=10.5s."
}
```

- `start` / `end` 必填,窗口边界(秒)。
- `modalities` 可选,缺省返回全部。
- `conclusion_id` 可选;给了就在响应里附带该结论的 `evidence_strength`。
- `reason` 可选,记录 Gemini 的调查意图(用于 demo 可解释性,不影响计算)。

### 响应

```json
{
  "window": { "start": 9.5, "end": 11.5 },
  "lidar":  [{ "t": 10.4, "ref": "lidar_frames/1040.png", "uri": "/media/..." }],
  "charts": [{ "ref": "charts/front_distance.png", "kind": "front_distance_vs_threshold", "uri": "/media/..." }],
  "logs":   [{ "t": 10.6, "level": "WARN", "node": "demo_safety_controller",
               "code": "DEMO_OBSTACLE_STOP_01", "message": "..." }],
  "metrics": { "planner_speed_mps": { "min": 0.8, "max": 0.8, "at_end": 0.8 },
               "applied_speed_mps": { "min": 0.0, "max": 0.8, "at_end": 0.0 },
               "actual_speed_mps": { "min": 0.0, "max": 0.79, "at_end": 0.0 },
               "front_distance_m": { "min": 0.74, "max": 2.6 },
               "safety_state": { "values": ["OK", "STOP"], "at_end": "STOP" } },
  "present_modalities": ["lidar", "metrics", "log"],
  "missing_modalities": [],
  "evidence_strength": {
    "conclusion_id": "concl_obstacle_stop",
    "level": "high",
    "verdict": "ok",
    "conflicts": [],
    "checks": {
      "required_present": true,
      "time_aligned": true,
      "labels_corroborate": true,
      "metrics_crossed": true
    },
    "missing": [],
    "note": "Reflects evidence completeness and consistency, NOT probability the root cause is correct. verdict='conflicting' means sensors explicitly contradict (maps to insufficient_evidence)."
  }
}
```

- `uri` 是前端可直接加载的媒体地址;`ref` 是稳定标识(用于可点击证据)。
- `evidence_strength.note` 字段固定写死,防止把它当置信度读。
- `evidence_strength.level` 是兼容字段,仍只取 `high / medium / low`;`verdict` 是结构化补充,用于区分普通缺证据和明确证据冲突。
- **关键:让 Gemini 真正"看到"图。** 工具返回 `uri` 只是给前端展示;要让 Gemini 做多模态推理,后端必须把对应 LiDAR/chart 的 **PNG 字节作为 image part 再次提交**到下一轮 Gemini 请求(inline data / Files API),光给本地 `uri` Gemini 看不到图。`metadata.json` 的 `modalities` 含 `lidar` 但调查时只回了 `uri` 没回图 part = 等于没用多模态。

---

## evidence_strength 计算规则

对某 `conclusion_id` 在给定窗口内做四项确定性核查(全部基于 `annotations.json`,**不看图、不问 Gemini**):

- **required_present** —— 该结论 `required_evidence` 里的每条,其 `t` 是否落在窗口内且资产存在(资产存在性查媒体文件,不查 annotations)。
- **time_aligned** —— **分两类核查,不用单一全局容差**:
  - 同步类(`corroboration_groups` 组内):组内各 evidence 时间偏差 ≤ 对应 `max_skew_s`(查 `synchronization.relations`,缺则用 `default_max_skew_s`)。
  - 因果类(`temporal_checks` 每对):`after.t - before.t` 落在 `[min_delay_s, max_delay_s]` 内(顺序对 + 延迟合理)。
  - 既不在 corroboration 也不在 temporal_checks 的证据对 → **不比较**,不影响判定。
- **labels_corroborate** —— 每个 `corroboration_groups` 组内,各 evidence 的 `object_label` 是否一致。
- **metrics_crossed** —— 所有 `metric_checks` 是否成立(`op` 比 `threshold`,实际值取自 `timeline.json`)。

定级:

- `high` —— 四项全 true。
- `medium` —— `required_present == true`,但 `time_aligned` / `labels_corroborate` / `metrics_crossed` 中**恰有一项**为 false。
- `low` —— `required_present == false`,或上述三项中 **≥2 项**为 false。

`missing` 列出导致降级的具体项(如缺哪条 evidence、哪条没对齐),供 demo 的"速度数据缺失 ✗"这类可追溯展示。

`verdict` 是独立于 `level` 的结构化判别:

- `ok` —— 没发现显式证据冲突。
- `conflicting` —— 某个 `corroboration_groups` 组内,至少两条 evidence 同时满足:
  - `t` 落在调查窗口内。
  - 对应资产 / 采样存在。
  - `object_label` 非空。
  - 组内出现两个或以上不同 `object_label`。

`conflicts` 列出冲突组和组内 label,例如:

```json
{
  "verdict": "conflicting",
  "conflicts": [
    { "group": ["ev_obstacle_lidar", "ev_front_distance"],
      "labels": ["clear", "obstacle"] }
  ]
}
```

注意:单纯缺证据**不是** conflict。缺图、缺日志、窗口没覆盖到某条 required evidence 仍通过 `required_present=false` 进入 `level=low` / `verdict=ok`;只有明确互相矛盾的已存在证据才进入 `verdict=conflicting`。场景级评估可把 `verdict=conflicting` 映射为 `insufficient_evidence`,表示"有冲突,不能确认该根因"。

`level` 表**证据完整性 + 一致性**,不表"根因正确的概率";`verdict=conflicting` 表**显式传感器矛盾**,也不是概率 —— 命名与文案都要守住这一点。

---

## 工具:check_recovery_readiness + 三态规则

回答"恢复运行前还需满足哪些条件" —— **恢复条件检查结果,不是机器人安全认证**。

**评估窗口由请求决定,不写死在 annotations。** `recovery.conditions` 只声明规则;`evaluation_window` 由调用方(Gemini / demo)传入,所以同一份 annotations 在不同窗口能得到不同状态,无需改数据。

请求:

```json
{
  "incident_id": "demo_obstacle_stop_01",
  "evaluation_window": [19.0, 25.0]
}
```

逐条评估 `recovery.conditions`,每条在 `evaluation_window` 内核查 `check`:

- `check.metric` + `op` + `threshold` + `aggregation` —— 在窗口内取该指标(来自 `timeline.json`),按 `aggregation` 聚合后判断。**必须显式声明聚合方式**,否则"取末值/任意值/全窗口值"无法确定:
  - `continuous_at_end` + `duration_s` —— 指标在 `evaluation_window` 的**最后 `duration_s`** 内**每个采样**都满足 `op`/`threshold`(末段持续达标)。这是恢复类条件的默认:窗口前半段障碍未清不影响,只要末段持续清空即满足。
  - 其它可选:`all`(全窗口每采样都满足)、`any`(任一采样满足)、`at_end`(仅末值)。恢复判断**不要**用 `any`(瞬时达标不代表已恢复)。
  - 末段 `duration_s` 内采样不足(数据缺失)→ 该条件 `unknown`,不算满足。
- `check.event_state` + `must_be` —— 按 `stateful_events` 的 assert/clear 配对,**从事故时间轴起点**扫描到 `evaluation_window` 的结束时刻,以最后一次相关事件确定状态:
  - 最后一次相关事件为 assert → `active`。
  - 最后一次相关事件为 clear → `cleared`。
  - 扫描范围内从未出现相关 assert 或 clear → `unknown`。
- `evaluation_window` 限定评估时段和截止时刻,但**不能**作为 stateful event 的扫描起点;否则会丢失窗口之前已进入 active 的状态。
- **不要**用"窗口内没出现报错"推断已解除——那只能证明没有新增报错。
- 若指标数据缺失,或 stateful event 扫描结果为 `unknown`,该条件记为 `unknown`。

汇总成三态:

- `conditions_met` —— 所有条件都可核查且**全部满足**。
- `blocked` —— **至少一条**可核查且**未满足**(存在明确阻塞项)。
- `insufficient_evidence` —— 没有任何 `blocked`,但**至少一条**为 `unknown`(数据不足以判断)。

响应:

```json
{
  "incident_id": "demo_obstacle_stop_01",
  "recovery_readiness": "blocked",
  "conditions": [
    { "id": "rc_obstacle_cleared", "label": "Obstacle removed from safety zone",
      "status": "unmet", "observed": { "front_distance_m": 0.74 } },
    { "id": "rc_stop_cleared", "label": "Obstacle-stop event cleared",
      "status": "unmet", "observed": { "event_state": "active" } }
  ],
  "note": "Recovery-condition check only. NOT a safety certification."
}
```

- 单条 `status`:`met` / `unmet` / `unknown`。
- `note` 字段固定写死。

---

## 后端职责小结(防幻觉的边界)

- **后端确定性产出**:`inspect_incident_window` 的事实、`evidence_strength`、`recovery_readiness` —— 全部来自 `annotations.json` / `timeline.json` / `logs.jsonl` 的规则核查。
- **Gemini 负责**:决定调查哪个窗口、组织自然语言叙述、把后端返回的状态串成给评委听得懂的根因说明。
- **Gemini 不负责**:判定 evidence_strength / recovery_readiness、宣称图里有什么、发安全许可。

---

## 后端运行契约与在线验收

后端实现位于 `backend/`,使用 Starlette + httpx,规则实现只来自 `tools/incident_rules.py`:

- `GET /health` —— 数据完整性与 Gemini key 可用状态。
- `GET /incident` —— 前端所需 metadata / timeline / annotations。
- `GET /media/{path}` —— 仅允许读取 `incident/lidar_frames` 与 `incident/charts` 下的 PNG。
- `POST /tools/inspect_incident_window`
- `POST /tools/check_recovery_readiness`
- `POST /tools/search_logs`
- `POST /investigate` —— Gemini function-calling 主入口。

在线调用使用 Gemini REST API:

- API key 只通过 `x-goog-api-key` 请求头发送,不进入 URL。
- 模型默认 `gemini-2.5-flash`,可通过 `GEMINI_MODEL` 覆盖。
- 模型返回 `functionCall` 后,后端执行确定性工具并提交 `functionResponse`。
- `inspect_incident_window` 选中的 LiDAR / chart PNG 以 `inlineData` image part 一起提交。
- 图片按 ref 去重;当前 hero 在线验收一次调查提交 2 张 LiDAR + 3 张 chart,共 5 张。
- 429 / 500 / 503 使用有限次数指数退避;网络、超时或协议失败时进入确定性离线回退。

`/investigate` 的关键状态字段:

```json
{
  "mode": "online",
  "model": "gemini-2.5-flash",
  "used_gemini": true,
  "tool_calls": ["inspect_incident_window"],
  "images_submitted": 5,
  "fallback_reason": null
}
```

真实在线验收结果为 `6/6 PASS`:

- Q1 调用 `inspect_incident_window(start=9,end=12,conclusion_id=concl_obstacle_stop)`。
- Q1 提交 5 张 PNG,引用 stop@10.6、halt@11.3、距离阈值和 `evidence_strength: high`。
- Q2 调用 `check_recovery_readiness`,返回 `blocked` 及两项未满足条件。
- 两轮均 HTTP 200、`mode=online`,未进入 fallback。

注意:当前 `online_check.py` 的 Q1/Q2 是两个独立请求,不是共享历史的会话。真正连续的多轮聊天需要在后续增加 session 或由客户端传回 conversation contents。
