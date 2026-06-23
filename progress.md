# Robot Incident Investigator — 当前进度

更新时间：2026-06-23

## 当前结论

Hero demo 的主链路已经跑通：

> 全合成 ROS1 bag → 固定演示资产 → 确定性证据规则 → Starlette 后端 → Gemini function calling + PNG 多模态输入 → 在线调查结果

真实 Gemini 在线端到端验收已经达到 `6/6 PASS`。当前项目已不是概念或静态计划，而是可运行、可离线兜底、带回归测试的 MVP。

## 已完成

### 数据与脱敏边界

- Hero 使用全合成障碍物触发安全停车场景。
- Hero 不使用真实错误码或内部故障名称，也不宣称复现任何真实事故或真实 bag。
- 真实 rosbag 不进入 `incident/`、Gemini 请求或发布资产。
- 无相机数据；多模态输入由 LiDAR 渲染图、运动/距离曲线和结构化日志组成。
- 脱敏与发布检查见 [non_sensitive.md](non_sensitive.md)。

### 合成数据流水线

`tools/scenario.py` 是场景数值的单一事实来源，定义：

- 25 秒、10Hz
- 前方安全阈值 1.2m
- LiDAR 证据 10.4s
- 距离证据 10.5s
- stop assert 10.6s
- 实际速度归零 11.3s
- clear 20.4s

已实现：

- `tools/generate_synthetic_bag.py`
- `tools/export_incident_assets.py`
- `tools/validate_incident.py`

当前产物：

- 合成 ROS1 bag：7 topics、251 个采样点、约 493KiB
- `incident/timeline.json`：251 条 metrics、126 个 LiDAR 引用、3 个 chart 引用
- `incident/lidar_frames/`：126 张 PNG
- `incident/charts/`：front distance、planner/applied/actual velocity、safety state
- `incident/logs.jsonl`：4 条结构化事件
- `incident/metadata.json`
- `incident/annotations.json`

数据与规则验证当前为 `20/20 PASS`，包括：

- 完整事故窗得到 `evidence_strength=high`
- halt 尚未发生时不能得到 high
- 正常时段不能得到 high
- 删除 stop 日志后不能得到 high
- 把 halt 速度篡改为 0.5m/s 后不能得到 high
- 停车阶段 recovery 为 `blocked`
- 障碍解除后 recovery 为 `conditions_met`

### 共享确定性规则

`tools/incident_rules.py` 是 validator 与后端共同使用的唯一规则实现，包含：

- `evidence_strength`
- `check_recovery_readiness`
- `search_logs`
- `inspect_incident_window`
- `asset_exists`
- `integrity_checks`

关键边界：

- 后端负责证据存在性、时间关系、指标阈值和恢复状态。
- Gemini 负责选择调查窗口、调用工具和组织说明。
- Gemini 不负责自报置信度、发安全许可或覆盖后端状态。

### 后端

后端位于 `backend/`，使用 Starlette + httpx，不依赖 FastAPI 或 Gemini SDK。

已实现端点：

- `GET /`
- `GET /health`
- `GET /incident`
- `GET /media/{path}`
- `POST /tools/inspect_incident_window`
- `POST /tools/check_recovery_readiness`
- `POST /tools/search_logs`
- `POST /investigate`

已实现保护：

- 启动时校验 `incident/` 完整性
- media 路径穿越防护
- 请求输入校验
- Gemini 超时处理
- 429 / 500 / 503 指数退避
- API key 通过 `x-goog-api-key` header 发送，不进入 URL
- 日志不打印 API key
- 无 key、网络或 API 失败时进入确定性离线回退
- 响应明确标记 `online` 或 `offline`

后端本地集成测试覆盖规则、HTTP 工具端点、media、防穿越、搜索和离线回退。

### Gemini 在线端到端验收

2026-06-23 使用真实 Gemini API、默认模型 `gemini-2.5-flash` 完成在线验收，结果 `6/6 PASS`。

Q1：`Why did the robot stop?`

- HTTP 200
- Gemini 调用 `inspect_incident_window`
- 参数：`start=9`、`end=12`、`conclusion_id=concl_obstacle_stop`
- 提交 5 个 PNG image parts：2 张 LiDAR + 3 张 chart
- 回答引用 stop@10.6、halt@11.3、front distance 阈值、速度和 safety state
- 返回 `evidence_strength: high`
- `mode=online`，无 fallback

Q2：恢复前置条件

- HTTP 200
- Gemini 调用 `inspect_incident_window` 和 `check_recovery_readiness`
- recovery 窗口为 `[12,13]`
- 返回 `blocked`
- 未满足条件：障碍仍在安全区、stop event 仍 active
- `mode=online`，无 fallback

这次验收确认了：

- REST 请求结构被 Gemini 接受
- 多轮 function call / function response 循环可终止
- `functionResponse` 与 `inlineData` PNG 可以共同提交
- Gemini 实际使用 LiDAR/chart 进行说明
- 在线模式、工具轨迹和图片数量可观测
- API key header、日志脱敏与重试路径工作正常

## 运行方式

```bash
V=/home/shaoningyu/projects/rosbag_cc/claude-mcp-rosbags/venv/bin/python3

# 重新生成数据
$V tools/generate_synthetic_bag.py
$V tools/export_incident_assets.py
$V tools/validate_incident.py

# 启动后端
$V backend/app.py

# 本地后端测试
$V backend/test_backend.py

# 在线 Gemini 验收
GEMINI_API_KEY="$(tr -d '\r\n' < ~/.gemini_key)" \
  $V backend/online_check.py
```

API key 文件应保持 `0600` 权限，不能提交到项目或写入日志。

## 当前剩余工作

### Demo 必需

- 打磨同步时间轴页面的视觉层级、证据点击跳转和演示文案。
- 把四项核心证据在 UI 中稳定呈现：LiDAR、front distance、stop event、velocity halt。
- 准备 3 分钟现场 runbook，并至少进行多次断网/限流演练。
- 现场前再跑一次数据验证、后端测试和在线验收。

### 建议补强

- 加强 `online_check.py` 自动断言：
  - 检查全部关键时间戳，而非任意一个
  - 检查 `evidence_strength=high`
  - 检查 Q2 明确返回 `blocked`
  - 检查回答包含“not a safety certification”语义
- 当前 Q1/Q2 是两个独立的 `investigate()` 请求，不共享对话历史。若要支持真正追问，需要增加 session 或由前端传回 Gemini conversation contents。
- 在线回答仍可能对图表持续时间做近似描述；最终 UI 应以确定性时间戳和后端状态为准。

### Stretch

- 正常运行对比
- 反事实安全阈值模拟
- 备用诊断急停或定位故障案例
- 语音输入

## 项目文档

- [plan.md](plan.md)：比赛策略、项目定位和 demo 方案
- [schema.md](schema.md)：数据、规则、工具和后端契约
- [data_pipeline.md](data_pipeline.md)：合成 bag 与资产导出流程
- [non_sensitive.md](non_sensitive.md)：全合成与脱敏策略
- `progress.md`：当前实现状态与剩余工作
