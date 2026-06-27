# Robot Incident Investigator — 当前进度

更新时间：2026-06-25

## 当前状态

项目已完成现场可用版本，并冻结在：

```text
commit: 3d2ddfc
tag: demo-verified-v6
```

v6 是当前推荐现场版本。旧 tag 均保留原位，未移动：

- `demo-verified`
- `demo-verified-v2`
- `demo-verified-v3`
- `demo-verified-v4`
- `demo-verified-v5`

端到端链路：

```text
全合成 ROS1 bag
→ 固定演示资产
→ 共享确定性规则
→ Starlette 后端
→ Gemini 多模态 function calling
→ SSE 实时进度与多轮追问
→ Material Design 3 深色前端
→ 可点击证据与同步时间轴
→ 离线确定性降级
```

## 验证基线

当前 v6 验证结果：

| 层 | 当前结果 |
|---|---:|
| 场景判别 eval | 3/3 PASS |
| Hero recovery 窗口 eval | 2/2 PASS |
| 合成数据与确定性规则 | 20/20 PASS |
| 后端真实 uvicorn + HTTP/SSE | 30/30 PASS |
| Gemini 在线稳定性 | v5 连跑 5/5 次均 6/6 PASS |
| 公开文档真实标识扫描 | 无已知残留 |

v6 没有改 Gemini 主链路；在线稳定性继承 v5 的修复：

- why/root-cause 问题只调用 `inspect_incident_window`。
- 禁止在 why/root-cause 问题中调用 `check_recovery_readiness` 或把答案漂到恢复状态。
- 只有用户明确问恢复 / resuming / can continue 时才调用 `check_recovery_readiness`。
- `generationConfig.temperature=0`，降低现场行为漂移。

## 当前能力边界

项目现在不只是单一 scripted incident，而是有多场景判别回归：

```text
obstacle_stop        → high/ok
planned_stop         → low/ok
sensor_disagreement  → low/conflicting
```

含义：

- `obstacle_stop`：确认障碍物触发安全停车，`evidence_strength.level=high`。
- `planned_stop`：规划器主动停车，无障碍、无 safety event、无 stop log，对“是否障碍停车”判 `low/ok`，证明不误报正常停车。
- `sensor_disagreement`：front-distance 指标显示近障碍，但 LiDAR label 为 clear，判 `low/conflicting`，映射为场景级 `insufficient_evidence`，防止模型硬编根因。

Hero 同一事故的恢复窗口也已进 eval：

- `[12,18] → blocked`
- `[19,25] → conditions_met`

这仍是恢复条件检查，不是安全认证。

## Hero 场景

Hero 是一个 25 秒、10Hz 的全合成障碍物安全停车事件，不使用相机。

核心证据：

- 10.4s：LiDAR 近距离障碍簇。
- 10.5s：front distance 为 0.74m，低于 1.2m 演示阈值。
- 10.6s：`DEMO_OBSTACLE_STOP_01` assert，applied command 被钳为零。
- 11.3s：实际速度降为零。
- 20.4s：`DEMO_OBSTACLE_CLEAR_01` clear。

对外统一描述：

> A fully synthetic obstacle-triggered safety-stop scenario inspired by common AMR failure patterns.

项目不使用真实错误码、内部故障名称、真实 topic、私有消息类型或真实数据片段。

## 已完成的实现

### 数据与 eval

- `scenarios/obstacle_stop.json`
- `scenarios/planned_stop.json`
- `scenarios/sensor_disagreement.json`
- `tools/scenario.py`：读取 JSON 配置；数字在 JSON，信号形状逻辑在 Python。
- `tools/generate_synthetic_bag.py`：生成 ROS1 合成 bag。
- `tools/export_incident_assets.py`：导出固定演示资产。
- `tools/validate_incident.py`：验证 hero 数据、证据规则、篡改回归和 recovery 两窗口。
- `tools/eval_scenarios.py`：枚举场景配置，执行多场景判别 eval，并验证 hero recovery 两窗口。

live demo 仍只读 `incident/`，不做 UI 场景切换。`eval_build/` 与 `eval_incidents/` 是确定性重建产物，已 gitignore，不入库。

### 共享规则

`tools/incident_rules.py` 是 validator、eval 与后端共同使用的唯一规则实现：

- `inspect_incident_window`
- `evidence_strength`
- `check_recovery_readiness`
- `search_logs`
- `asset_exists`
- `integrity_checks`

`evidence_strength.level` 仍只取：

- `high`
- `medium`
- `low`

Step 3 新增结构化字段：

- `verdict: ok | conflicting`
- `conflicts: [...]`

`conflicting` 只在同一 corroboration group 内至少两条 evidence 都在窗口内、资产存在、label 非空且 label 明确矛盾时触发。单纯缺证据不是 conflict，仍是 `level=low / verdict=ok`。

Gemini 不负责计算证据强度或恢复状态，也不允许签发安全许可。

### 后端

后端使用 Starlette + httpx，不依赖 FastAPI 或 Gemini SDK。

端点：

- `GET /`
- `GET /health`
- `GET /incident`
- `GET /media/{path}`
- `POST /tools/inspect_incident_window`
- `POST /tools/check_recovery_readiness`
- `POST /tools/search_logs`
- `POST /investigate`
- `POST /investigate/stream`

能力与保护：

- Gemini function calling 与 PNG `inlineData`。
- SSE 推送 `start → thinking → tool_call → result`。
- 429 / 500 / 503 指数退避。
- 超时或网络失败时确定性离线回退。
- API key 仅通过 `x-goog-api-key` header 发送。
- 日志脱敏、media 路径穿越防护和启动完整性检查。
- 在线/离线模式、工具轨迹与图片数量可观察。
- 多轮 history 输入有形状、角色交替、轮数、单条长度和总长度校验。

### 前端

前端已经改成 Material Design 3 风格深色 UI，并具备现场演示所需交互：

- 同步时间轴、LiDAR、遥测曲线和实时数值。
- 证据时刻标线、证据卡与一键跳转。
- 答案时间戳可点击并跳到对应时刻。
- SSE 实时显示 Gemini 的 thinking、工具和图片提交进度。
- Online Gemini / Offline deterministic fallback 徽标。
- 多轮上下文与轮次显示。
- BUSY 防重复提交和并发请求。
- 请求期间禁用 Ask、New 和示例按钮。
- Cancel 按钮与 45 秒超时。
- SSE 断开、超时、取消的明确提示和 retry。

### 安全与公开边界

- 真 bag、`*.bag`、key 和 Python 缓存均被 `.gitignore` 排除。
- `data_pipeline.md` 不包含真实 bag 文件名、内部 topic 或私有消息类型。
- `non_sensitive.md` 已对齐当前 LiDAR + telemetry + log hero。
- `runbook.md` 不再反向提及任何真实错误码。
- 公开口径：fully synthetic，不复现任何真实事故或真实 bag。

## 运行与验收

```bash
V=/home/shaoningyu/projects/rosbag_cc/claude-mcp-rosbags/venv/bin/python3
cd /home/shaoningyu/projects/allmemory/Robot_Incident_Investigator

# 多场景判别 + hero recovery eval
$V tools/eval_scenarios.py

# 数据验证
$V tools/validate_incident.py

# 后端真实 HTTP/SSE 验证
$V backend/test_backend.py

# 启动现场服务
GEMINI_API_KEY="$(tr -d '\r\n' < ~/.gemini_key)" \
  PORT=8000 $V backend/app.py

# 在线 Gemini 验收
GEMINI_API_KEY="$(tr -d '\r\n' < ~/.gemini_key)" \
  $V backend/online_check.py
```

API key 文件必须保持 `0600` 权限，不得提交或写入日志。

现场操作、3 分钟脚本和故障预案见 [runbook.md](runbook.md)。

## 当前剩余工作

### 必须手动完成

- 录制一段在线 hero demo。
- 录制一段故意断网或无 key 的离线降级 demo。
- 从 `demo-verified-v6` 至少完整彩排数次。
- 现场前重新运行 eval、data、backend；网络可用时再跑 online。

### 发布与现场注意

- 现场必须从 `demo-verified-v6` 对应 commit 启动，不使用未验证工作区。
- Gemini 出现 503、429 或超时时，不现场调代码；让系统降级并按 runbook 口播。
- 最终结论以确定性证据状态为准，Gemini 负责调查和叙述。

### Stretch

- Interactions API 在获得可靠官方请求样例后重新启用独立 spike。
- 正常运行对比的可视化扩展。
- 反事实安全阈值模拟。
- 备用诊断或定位故障案例。
- 语音输入。

## 文档入口

- [plan.md](plan.md)：比赛策略、项目定位和 demo 方案
- [schema.md](schema.md)：数据、规则、工具和后端契约
- [data_pipeline.md](data_pipeline.md)：合成 bag、资产导出与多场景 eval 流程
- [non_sensitive.md](non_sensitive.md)：全合成与公开边界
- [runbook.md](runbook.md)：现场操作、演示脚本和故障预案
