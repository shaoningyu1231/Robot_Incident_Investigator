# Demo 介绍优先级

这份文档只管一件事：现场向评委介绍时，按什么顺序讲项目亮点。不要把它讲成技术实现清单；先讲价值，再讲 Gemini，再讲可靠性。

---

## 0. 一句话定位

> Robot Incident Investigator lets anyone ask why an autonomous robot stopped, and get a grounded answer backed by LiDAR, telemetry, logs, and clickable evidence.

中文理解：

> 这个项目让非机器人专家也能直接问“机器人为什么停了”，系统会用 LiDAR、速度遥测和日志给出可追溯的事故解释。

强调边界：

- 数据是 fully synthetic。
- 场景 inspired by common AMR failure patterns。
- 不使用真实错误码、真实 bag、真实 topic、内部命名或真实日志。

---

## P0 — 最高优先级：把问题讲清楚

先讲痛点，不要先讲模型。

核心说法：

> When a robot stops, engineers usually dig through bags, logs, plots, and sensor frames. That workflow is slow and hard for non-experts. I built an agent that turns this into an interactive investigation.

要点：

- 真实机器人事故调查通常要查 rosbag、日志、LiDAR、速度曲线。
- 评委不需要懂 ROS，也能问自然语言问题。
- 目标不是“聊天机器人”，而是“可追溯的机器人事故调查员”。

不要说：

- “这是一个 chatbot。”
- “Gemini 判断安全不安全。”
- “它复现了某个真实事故。”

---

## P1 — 最高优先级：展示 hero demo 的因果链

现场主线只讲一个清晰故事：

```text
front_distance 下降
→ LiDAR 前方出现近距离回波
→ safety stop 事件 assert
→ applied command 被钳为 0
→ actual velocity 降为 0
```

推荐话术：

> I ask “Why did it stop?” Gemini chooses the incident window, calls the investigation tool, receives LiDAR images and telemetry charts as image parts, and explains the root cause with timestamps.

必须点出的证据时间：

- `10.4s`：LiDAR 近距离障碍簇。
- `10.5s`：front distance 跌破 1.2m 阈值。
- `10.6s`：obstacle stop event assert。
- `11.3s`：actual velocity 到 0。

亮点：

- 答案不是空口解释。
- 每个结论都能跳回证据。
- 评委点时间戳 / 证据卡可以看到对应帧和曲线。

---

## P2 — 最高优先级：说明 Gemini 用得深，不只是 API 调用

这一段直接对应 Google 技术利用度。

必须讲：

- Gemini 做自然语言调查规划。
- Gemini 自己决定调用哪个工具、调查哪个时间窗。
- `inspect_incident_window` 返回结构化事实。
- 后端把 LiDAR PNG 和 chart PNG 作为 `inlineData` image parts 交回 Gemini。
- Gemini 用多模态证据组织自然语言答案。
- 流式 UI 展示 thinking / tool call / image submission 进度。

推荐话术：

> This is not a single text prompt. Gemini runs a tool-using investigation loop: it selects the window, calls deterministic tools, receives structured evidence plus actual LiDAR and chart images, then explains the incident.

可以强调：

- function calling
- multimodal image parts
- SSE streaming progress
- multi-turn follow-up
- offline deterministic fallback

不要夸大：

- 不说 Gemini 自己“看懂了所有 ROS 数据”。
- 不说 Gemini 负责最终证据强度判定。
- 不说 Gemini 发安全许可。

---

## P3 — 高优先级：讲“确定性规则 + Gemini”的分工

这是防幻觉的核心。

推荐话术：

> Gemini investigates and explains. The deterministic backend verifies evidence strength and recovery readiness. That separation prevents the model from inventing confidence or issuing safety approvals.

分工：

| 部分 | 负责什么 |
|---|---|
| Gemini | 选择调查窗口、调用工具、组织解释 |
| 后端规则 | evidence_strength、verdict、recovery_readiness |
| 前端 | 展示时间轴、图片、曲线、证据跳转 |

必须讲清：

- `evidence_strength` 不是概率。
- 它表示证据完整性与一致性。
- `recovery_readiness` 是恢复条件检查，不是 safety certification。

推荐短句：

> The model narrates; the rules verify.

---

## P4 — 高优先级：展示恢复条件追问

第二问建议问：

> What needs to be true before it can resume?

或：

> 恢复运行前还需要满足哪些条件？

展示结果：

- `[12,18] → blocked`
  - 障碍仍在。
  - stop event 仍 active。
- `[19,25] → conditions_met`
  - front distance 末段持续大于阈值。
  - clear event 已发生。

口播边界：

> This is a recovery-condition check, not a safety certification.

为什么这重要：

- 机器人项目里“能不能继续走”是高风险问题。
- 系统不直接给安全许可，只列出恢复前置条件。
- 这比让 LLM 直接回答 “safe / unsafe” 更严谨。

---

## P5 — 高优先级：证明不是只会讲一个 scripted case

这部分可以放在 demo 后半段或 Q&A。

当前 eval 覆盖：

```text
obstacle_stop        → high/ok
planned_stop         → low/ok
sensor_disagreement  → low/conflicting
recovery [12,18]     → blocked
recovery [19,25]     → conditions_met
```

推荐话术：

> I also evaluated it across a confirmed obstacle stop, a planned stop, and conflicting sensor evidence. It does not simply narrate one scripted incident.

解释：

- `planned_stop`：规划器主动停车，系统不会误报成障碍事故。
- `sensor_disagreement`：距离指标说有障碍，但 LiDAR 是 clear，系统给 `conflicting → insufficient evidence`，不会硬编根因。
- recovery 两窗口证明同一个事故在不同时间能得到不同恢复状态。

这部分是 performance / robustness 亮点。

---

## P6 — 中高优先级：强调现场可靠性

现场 demo 最怕网络、模型或 API 波动。这里要说明系统能降级。

推荐话术：

> If Gemini or the network fails, the system degrades to the deterministic evidence engine. The narration becomes less flexible, but the same root cause and recovery checks remain available.

可讲点：

- Gemini 503 / timeout 时自动 fallback。
- 离线模式仍能返回根因、证据、恢复条件。
- 后端和 validator 共用同一套 `incident_rules.py`。
- 回归测试覆盖真 HTTP / SSE / 多轮 history / 输入校验。

不要花太久，除非评委问可靠性。

---

## P7 — 中优先级：讲前端体验

前端不是核心算法，但现场观感重要。

可讲：

- Material Design 3 深色界面。
- Ask 后不是空等，有流式进度。
- 可点击时间戳。
- 可点击证据卡。
- 时间轴、LiDAR、曲线同步。
- Cancel / retry / timeout 防现场卡死。

推荐话术：

> The UI is designed for live debugging: timeline, evidence, model reasoning progress, and recovery conditions stay synchronized.

---

## P8 — 中优先级：讲为什么用合成 bag

如果评委问数据真实性，再讲。

推荐话术：

> The demo data is fully synthetic by design. It preserves the structure of robot incident investigation — LiDAR, telemetry, and logs — without exposing customer, company, or robot data.

关键点：

- 合成 bag 仍是 ROS1 bag。
- 有标准 ROS topic、LiDAR scan、odom、cmd velocity、logs。
- 可以重复生成、重复验证。
- 不上传真实数据给云端或模型。

不要说：

- 不要提任何真实 bag 名。
- 不要提真实错误码。
- 不要说“从真实数据脱敏而来”。

---

## 推荐 3 分钟讲法

### 0:00–0:20 问题

> Robot stops are hard to debug because evidence is scattered across bags, logs, plots, and sensors.

### 0:20–0:40 产品

> This agent lets anyone ask why the robot stopped and get a grounded answer with clickable evidence.

### 0:40–1:40 主 demo

问：

> Why did it stop?

展示：

- Gemini 调 `inspect_incident_window`。
- UI 显示 tool progress。
- 答案引用 10.4 / 10.5 / 10.6 / 11.3。
- 点一个时间戳或证据卡。

### 1:40–2:15 恢复追问

问：

> What needs to be true before it can resume?

展示：

- `blocked`
- 条件清单
- “not a safety certification”

### 2:15–2:45 技术亮点

> Gemini plans and explains; deterministic tools verify. LiDAR and charts are sent as actual image parts, not just URLs.

### 2:45–3:00 可靠性与 eval

> It is evaluated across obstacle stop, planned stop, and sensor disagreement, and it degrades to deterministic fallback if the network fails.

---

## 最后 20 秒必须留下的印象

如果时间只够一句：

> This is a multimodal Gemini agent for robot incident investigation: it does not just answer, it investigates, calls tools, looks at evidence images, cites timestamps, and refuses to overclaim when evidence is missing or conflicting.

---

## 现场禁忌

- 不说真实错误码。
- 不说真实 bag。
- 不说内部 topic、内部消息类型或真实系统命名。
- 不说 Gemini 判断机器人“安全”。
- 不说 evidence_strength 是 root-cause 概率。
- 不临场解释太多 ROS 细节。
- 不把演示重点放在代码结构。

---

## Q&A 速答

**Q: Why synthetic data?**  
A: To preserve the structure of robot incident debugging without exposing real robot, company, or customer data.

**Q: Is Gemini making the safety decision?**  
A: No. Gemini investigates and explains. Deterministic rules compute evidence strength and recovery readiness. The system does not issue safety certification.

**Q: What makes this more than a chatbot?**  
A: It uses function calling, structured tools, real image parts for LiDAR/charts, evidence timestamps, and deterministic verification.

**Q: What if the model is wrong or the network fails?**  
A: The deterministic evidence engine still works offline. Gemini improves interaction and explanation, but the core evidence checks are reproducible.

**Q: How do you know it is not overfitting to one case?**  
A: The eval includes a confirmed obstacle stop, a planned stop true-negative, conflicting sensor evidence, and recovery windows before and after clear.
