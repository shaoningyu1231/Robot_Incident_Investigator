# Robot Incident Investigator — Gemini AI Hackathon 项目计划

> 一句话 pitch:When a robot stops, engineers inspect gigabytes of data. Our agent lets anyone interrogate the incident and jump directly to the evidence.

机器人停下来时,工程师要翻 GB 级的数据。我们的 agent 让任何人都能"审讯"这次事故,并一键跳到对应证据。

---

## 比赛要求(必须满足,否则没奖)

- **活动**:Gemini AI Hackathon @Google Japan(ハッカソン東京)
- **地点 / 形式**:Google Japan,涩谷 Shibuya Stream 5F;单日线下 hackathon。
- **当天节奏**:9:30 check-in → 10:00 开场 → 10:30 workshop → 11:30 组队 + 开始 hack → 16:30 停止编码 + live demo → 17:30 公布获胜 → 18:00 结束。
- **真正可编码时间**:约 11:30–16:30,**只有 5 小时**,要做到 idea → 能现场演示的 prototype。
- **强制技术栈**:必须接入 Google Cloud 产品。活动页面把 "Gemini API / AI Studio / Antigravity / Vertex AI" **并列**为合格方式 —— 即 AI Studio 的 Gemini API key 按主办方描述就算数。没接 Google 产品的项目直接失去评奖资格。**Vertex AI 作为兜底**,现场再向主办方口头确认一句。
- **核心工具**:Gemini 3.5 Flash、Managed Agents、AI Studio、Antigravity 2.0;现场发 Google Cloud credits。
- **评分三条**:
  - Google Cloud 产品利用程度(强制项)
  - 创新与创意(用上 I/O 2026 新特性有加分)
  - 完成度与 demo-readiness
- **参赛规则**:无需编程经验;solo 或最多 6 人组队都可以。

设计取舍:5 小时内,"能现场跑通、能互动"比技术深度更重要。所以全部围绕完成度和 demo 效果来做。

---

## 项目定位

把项目从"静态 rosbag 报告工具"升级成 **Robot Incident Investigator —— 和机器人事故现场对话的 Agent**。

不要只让 Gemini 回答"机器人为什么停了",而是让评委**亲自调查**:

- hero 案例:**一个全合成的障碍物触发安全停车场景**(*A fully synthetic obstacle-triggered safety-stop scenario inspired by common AMR failure patterns*)。一个备用案例兜底,不做第三个。详见 [non_sensitive.md](non_sensitive.md):全合成数据,真实 rosbag 不进仓库 / 云端 / 演示设备 / Gemini 请求。
  - **表述边界(必须守住)**:不使用任何真实错误码或内部故障名称;不宣称它复现了任何真实 bag;统一用上面那句英文定性。
  - **不用相机**:模态为 **LiDAR + 运动遥测 + 事件日志**。LiDAR 渲染图和速度/距离曲线本身就是 Gemini 的视觉输入,且贴合真实数据形态,系统边界更可靠(无需视觉模型识别物体)。
- 播放同步时间轴:LiDAR 渲染图、距离/速度曲线、安全状态、事件日志。
- 评委用自然语言提问,Gemini 自己决定调查哪个时间窗、调用工具提取多模态证据,最后给出根因 + 证据时间戳 + **`evidence_strength`(高/中/低,表证据完整性+一致性,非根因正确概率)** + 恢复前置条件。

示例提问:
- "为什么在这里停下?"
- "给我看停止前 3 秒。"
- "前方距离是什么时候掉到阈值以下的?"
- "恢复运行前还需要满足哪些条件?"(输出 `conditions_met / blocked / insufficient_evidence`,是**恢复条件检查结果,不是安全认证**)

为什么这个方向赢面大:
- **专家构建的高拟真合成数据** —— 领域专家按真实 AMR 故障模式造的合成事故,topic 形态仿照真机(`scan` / 逐方向距离 / `safety_status` / `cmd_vel` vs 实际速度),跨模态因果一致、ground truth 干净;现场别人是临时编的。
- **多模态用满** —— LiDAR 图 + 距离/运动曲线 + 事件日志一起喂 Gemini。
- **是真 agent** —— function calling 操作界面,不只是聊天。
- **故事性强** —— "让任何人都能审讯一台自主机器人"。

---

## 核心理念:一次真正的多模态事故调查,而不是四个页面控制工具

重点不在"Gemini 帮我点页面按钮",而在"Gemini 自己决定调查哪个时间窗、要哪些证据、综合出根因"。`seek/show/render` 这类只是 UI 控制,体现不出 agent 的分析能力。真正的核心是下面这个工具。

### 核心工具:`inspect_incident_window(start, end)`

统一返回这个时间窗内的全部证据,让 Gemini 做调查决策:

- 该窗内的 LiDAR 渲染图、距离/速度曲线图(或其引用)
- 该窗内的结构化日志条目
- 关键指标(命令速度 vs 实际速度、前方最近障碍距离、安全状态等)
- **`evidence_strength`**(见下),作为事实返回

Gemini 负责:决定调查哪个窗口 → 调 `inspect_incident_window` 取证 → 必要时缩小窗口或调 `search_logs` → 综合表述。它综合,但不能编造证据状态。

### `evidence_strength`(替代 Gemini 自报置信度)

**不要**用 Gemini 自报的"置信度"(没校准、没意义)。也**不要**让普通后端去"看图判断 LiDAR 里有没有障碍" —— 没有独立视觉模型根本算不了。事实来源是合成数据的**预置标注 [annotations.json](schema.md)**:每条证据带 `expected_observation`,后端只做确定性核查:

- **必需证据是否齐**(该结论需要的 LiDAR / 距离指标 / safety 事件 / 日志条目都在?)
- **时间是否对齐**(同步证据 ≤ `max_skew_s`;因果事件落在允许延迟内)
- **预置标签是否互相印证**(LiDAR 近距离回波 ↔ front_distance 指标 ↔ safety 事件指向同一障碍?)
- **指标是否越过演示阈值**(前方最近障碍距离 < 演示安全阈值?)

据此给出 `evidence_strength: 高 / 中 / 低`,**表证据完整性 + 一致性,不表"根因正确的概率"**。这几项都是对 annotations.json 的简单核查,既便宜又防幻觉。

### 证据可追溯

每个结论都附带可点击证据,点击后时间轴直接定位:

> 机器人停止的主要原因是前方障碍物进入安全区。
> 证据:10.4s LiDAR 近距离回波、10.5s front_distance 跌破阈值、安全事件 `DEMO_OBSTACLE_STOP_01`。
> evidence_strength:必需证据齐 ✓,时间对齐 ✓,预置标签互相印证 ✓,指标越阈值 ✓ → 高。

### 恢复前置条件(替代"继续走安全吗")

不让 AI 发安全许可。问法改成"恢复运行前还需要满足哪些条件",输出 `recovery_readiness` 三态 —— 这是**恢复条件检查结果,不是机器人安全认证**:

- `conditions_met`:恢复所需的前置条件都满足
- `blocked`:存在未满足的前置条件(证据指向明确阻塞项)
- `insufficient_evidence`:数据不足以判断 —— 这个状态本身就是机器人圈认可的专业回答

### 支撑工具

- `search_logs("obstacle_stop")` —— 结构化日志检索,是 `inspect_incident_window` 之外的第二根支柱。
- `seek_to_timestamp(t)` —— 把 Gemini 的调查动作映射到时间轴跳转(配合可点击证据)。
- LiDAR 图 / 曲线图 同步展示组件 —— 注意:这是 `inspect_incident_window` 输出能被看见的**前置展示面**,不是纯靠后的功能,要和核心工具一起留出时间。

### Stretch(只在主闭环稳定后才碰)

- **反事实调查**(单独的 stretch demo,**不进必演脚本**):"安全距离从 1.2m 调到 0.8m 会怎样?" —— 按距离曲线和规则生成预测,**明确标记为 simulation**。
- **正常运行对比**:用同路线正常案例比命令/实际速度 / 前方距离 / safety 事件 / 日志差异。
- **语音输入**:最后做,高风险低边际收益。
- **未来扩展(口头带过即可)**:同一套调查工作流也支持一般诊断急停和定位丢失 —— *The same investigation workflow also supports diagnostic emergency stops and localization failures.* hero 只演障碍停车这一条最直观的链。

---

## 功能优先级(从核心调查能力往外排)

- `inspect_incident_window` —— 核心,先做这个
- `search_logs`
- 可点击证据 + `seek_to_timestamp`
- LiDAR 图 / 曲线图 同步展示(部分是 `inspect_incident_window` 的前置展示面)
- 反事实(stretch)
- 正常案例对比(stretch)
- 语音输入(最后)

---

## 五小时可完成的 MVP

**不要现场直接解析任意 rosbag。** 赛前把 **1 个 hero 案例 + 1 个备用案例**(不做第三个)转换成固定格式,现场只做交互层。2–3 个完整案例会把时间耗在数据整理上。

预处理后的案例目录结构:

```
incident/
├── metadata.json      # 案例描述、机器人型号、结论(ground truth)
├── timeline.json      # 同步时间轴:每个时刻指向哪帧 lidar 图 / 指标 / 日志
├── annotations.json   # 规范性标注来源:声明 expected_observation、证据关系与核查规则
├── lidar_frames/      # 渲染好的 LiDAR scan 图 (png)
├── charts/            # 速度(命令vs实际)、前方距离vs阈值、safety_state 曲线 (png)
└── logs.jsonl         # 结构化日志:每行 {t, level, node, code, message}
```

从合成 bag 导出这些资产的流程见 [data_pipeline.md](data_pipeline.md)。**不含相机帧** —— 模态是 LiDAR + 运动遥测 + 事件日志。

字段定义见 [schema.md](schema.md)。日志用 `logs.jsonl` 而不是纯文本,否则搜索和证据定位不稳;`annotations.json` 是 `evidence_strength` / `recovery_readiness` 计算的**规范性标注来源**(声明"应观察到什么、谁该印证谁"),实际指标 / 日志 / 资产存在性仍来自 `timeline.json` / `logs.jsonl` / 媒体文件,没有它"后端确定性计算"就没有规范依据。

当前 MVP 已实现:

- 同步时间轴网页、LiDAR 帧、三张遥测曲线和文本提问入口
- Gemini 在线多模态调查与确定性离线回退
- 核心 `inspect_incident_window`、`check_recovery_readiness`、`search_logs`
- 带时间戳、`evidence_strength` 和恢复三态的诊断结果
- 在线 / 离线模式、工具调用轨迹和提交图片数量的界面标识

技术链路保持简单:

```
浏览器 → Python / Starlette → Gemini REST API (AI Studio key;Vertex AI 兜底)
                            ↓
       inspect_incident_window / check_recovery_readiness / search_logs
                            ↓
              incident/ 固定资产 + 共享确定性规则模块
```

实现状态和复现命令见 [progress.md](progress.md)。

---

## hero 案例:全合成障碍物触发安全停车

> A fully synthetic obstacle-triggered safety-stop scenario inspired by common AMR failure patterns.

核心证据链(约 25s 合成时间线):

```
LiDAR 近距离回波 → front_distance 跌破演示阈值 → safety 事件 asserted (DEMO_OBSTACLE_STOP_01)
→ 命令速度保持 / 实际速度降为零 → 障碍移除 → front_distance 回升过阈值
→ safety 事件 cleared (DEMO_OBSTACLE_CLEAR_01) → 恢复条件满足
```

首选这个,因为:

- 因果链直观,评委几秒就懂。
- LiDAR、距离指标、安全状态、运动数据、日志能形成完整证据链。
- 完整事件弧:正常 → 障碍触发 → 减速停车 → 障碍解除 → 恢复条件满足。
- 不需要相机 / 视觉模型,系统边界更可靠;明确 ground truth,降低幻觉。

另准备 1 个备用案例(定位丢失或诊断急停),demo 翻车时切换用,不做第三个。

---

## 现场 demo 脚本(3 分钟核心闭环)

正式脚本只演稳定能力,**停在"根因 → 四项可追溯证据 → 恢复前置条件"**。这一闭环跑稳就已经是完整产品。

- 打开 hero 案例,播放同步时间轴,展示机器人在某点突然停住。
- 评委问"为什么停?" → Gemini 自己定位时间窗、调 `inspect_incident_window` 取 LiDAR 图+距离/速度曲线+日志+指标 → 给出根因 + 证据时间戳 + `evidence_strength`(高/中/低)。
- 点击证据 → 时间轴跳转,证据可追溯。
- 追问"恢复运行前还需满足哪些条件?" → 输出 `recovery_readiness`(`conditions_met / blocked / insufficient_evidence`)+ 条件清单。
- **闭环到此为止。** 若时间充裕,再演 stretch:反事实"安全距离调到 0.8m 会怎样?"(标 simulation),或正常运行对比。

---

## 待办 / 风险

- **hero 数据与规则层已完成**:合成 bag、固定资产、共享规则模块和篡改回归均已跑通。数据流水线当前为 `20/20 PASS`。
- **后端与 Gemini 在线链路已完成**:真实 Gemini `gemini-2.5-flash` 在线验收为 `6/6 PASS`;Q1 调用事故窗口调查并提交 5 张 PNG,Q2 调用恢复条件检查,全程 HTTP 200、无 fallback。
- **仍待完成**:前端演示体验打磨、真正连续的多轮会话、在线验收断言加固、现场 runbook 与多次彩排。备用案例仍是 stretch,不阻塞 hero demo。
- **表述边界(阻塞项)**:hero 不使用真实错误码或内部故障名称、不宣称复现真实 bag,统一用 "fully synthetic obstacle-triggered safety-stop ... inspired by common AMR failure patterns"。
- **全合成、脱敏(阻塞项)**:真实 rosbag / 地图 / 人员影像 / 序列号 / 内部错误码 / 真实 topic 命名约定**不进仓库、云端、演示设备、Gemini 请求**;真 bag 仅供赛前人工理解 topic 形态。按 [non_sensitive.md](non_sensitive.md) 的 checklist 过一遍。
- **网络 / API 失败预案**:后端已实现超时、429/500/503 退避和确定性离线回退;正式 demo 仍应至少保留一次真实 Gemini 调用,并在界面明确显示 `Online Gemini` 或 `Offline deterministic fallback`。
- **Google Cloud 接入**:AI Studio 的 Gemini API key 起步最快(主办方描述里算合格接入);**Vertex AI 兜底**;现场领 credits,并口头跟主办方确认一句资格。
- **创新加分**:现场留意 I/O 2026 的新多模态 / agent 特性,demo 里点一下。
- **幻觉风险**:每个结论强制绑证据时间戳;`evidence_strength` / `recovery_readiness` 由后端对 `annotations.json` 确定性核查、不问 Gemini;选有明确 ground truth 的案例兜底。
