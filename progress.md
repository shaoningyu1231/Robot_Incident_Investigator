# Robot Incident Investigator — 当前进度

更新时间：2026-06-23

## 当前状态

项目已经完成端到端 MVP，并冻结了经过验证的公开版本：

```text
全合成 ROS1 bag
→ 固定演示资产
→ 共享确定性规则
→ Starlette 后端
→ Gemini 多模态 function calling
→ SSE 实时进度与多轮追问
→ 同步时间轴和可点击证据
```

当前推荐冻结点：

```text
commit: 6bb1f6f
tag: demo-verified-v2
```

`demo-verified-v2` 在原 `demo-verified` 基础上清理了公开文档中的真实标识，并将脱敏说明对齐当前无相机 hero。原 tag 保持不变。

## 验证基线

| 层 | 当前结果 |
|---|---:|
| 合成数据与确定性规则 | 20/20 PASS |
| 后端真实 uvicorn + HTTP/SSE | 30/30 PASS |
| Gemini 在线端到端基线 | 6/6 PASS |
| 公开文档敏感词与旧场景扫描 | 无残留 |

在线 Gemini 的 `6/6 PASS` 已确认：

- `gemini-2.5-flash` 接受 REST 请求和 function-calling 循环。
- Q1 调用 `inspect_incident_window(start=9,end=12)`。
- 提交 5 张 PNG：2 张 LiDAR + 3 张遥测 chart。
- 回答引用 stop@10.6、halt@11.3 和距离阈值，返回 `evidence_strength: high`。
- Q2 调用 `check_recovery_readiness`，返回 `blocked` 与未满足条件。
- 两轮均为 `mode=online`，无 fallback。

后续复测曾遇到 Gemini 503 / ReadTimeout 并得到 `4/6`，系统按设计进入离线回退。这属于外部服务瞬时可用性，不是 key 或代码回归；也证明现场降级路径有效。

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

### 数据流水线

`tools/scenario.py` 是所有场景数值的单一事实来源。

- `tools/generate_synthetic_bag.py`：生成 7-topic ROS1 合成 bag。
- `tools/export_incident_assets.py`：导出固定演示资产。
- `tools/validate_incident.py`：执行数据、证据和恢复规则测试。

当前资产：

- 251 条 metrics。
- 126 张 LiDAR PNG。
- 3 张遥测 chart。
- 4 条结构化日志。
- `metadata.json`、`timeline.json`、`annotations.json`。

验证包含两个篡改回归：

- 删除 stop 日志后不能得到 `evidence_strength=high`。
- 将 halt 时速度改为 0.5m/s 后不能得到 `high`。

### 共享规则

`tools/incident_rules.py` 是 validator 与后端共同使用的唯一规则实现：

- `inspect_incident_window`
- `evidence_strength`
- `check_recovery_readiness`
- `search_logs`
- `asset_exists`
- `integrity_checks`

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

### 多轮会话

Interactions API spike 因请求 schema 闸门未通过而按时间盒停止，且完全未接入主路由。

当前稳定实现使用客户端轻量 history：

- Q&A 纯文本历史，不重复传输图片字节。
- 普通和 SSE 端点均支持 history。
- 前端提供 `↺ New` 清空会话。
- 最多 8 轮；提交第 9 轮前即返回 400。
- history 必须是完整的 `user → model` 成对结构。
- 单条、总长度和 question 均有限制。
- 生产系统应改为 server-side session；客户端 model history 是 demo 级权衡。

### 前端

前端已经具备现场演示所需交互：

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
- 当前 tracked 文件共 152 个，均为合成资产、代码或公开文档。
- `data_pipeline.md` 不再包含真实 bag 文件名、内部 topic 或私有消息类型。
- `non_sensitive.md` 已对齐当前 LiDAR + telemetry + log hero。
- 公开文本扫描未发现真实标识或旧相机场景残留。

## 运行与验收

```bash
V=/home/shaoningyu/projects/rosbag_cc/claude-mcp-rosbags/venv/bin/python3
cd /home/shaoningyu/projects/allmemory/Robot_Incident_Investigator

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
- 使用 `demo-verified-v2` 至少完整彩排数次。
- 现场前重新运行 data 20/20、backend 30/30；网络可用时再跑 online 6/6。

### 发布与现场注意

- 现场必须从 `demo-verified-v2` 对应 commit 启动，不使用未验证工作区。
- Gemini 出现 503、429 或超时时，不现场调代码；让系统降级并按 runbook 口播。
- 最终结论以确定性证据状态为准，Gemini 负责调查和叙述。

### Stretch

- Interactions API 在获得可靠官方请求样例后重新启用独立 spike。
- 正常运行对比。
- 反事实安全阈值模拟。
- 备用诊断或定位故障案例。
- 语音输入。

## 文档入口

- [plan.md](plan.md)：比赛策略、项目定位和 demo 方案
- [schema.md](schema.md)：数据、规则、工具和后端契约
- [data_pipeline.md](data_pipeline.md)：合成 bag 与资产导出流程
- [non_sensitive.md](non_sensitive.md)：全合成与公开边界
- [runbook.md](runbook.md)：现场操作、演示脚本和故障预案
