# 演示数据脱敏与全合成策略

本项目公开演示的数据全部从零生成，只参考 AMR 行业常见故障模式，不复制、裁剪、改名或重新包装任何真实 rosbag。

对外统一描述：

> This demo uses a fully synthetic obstacle-triggered safety-stop scenario inspired by common AMR failure patterns. It contains no production, customer, employee, facility, or real robot data.

## 当前 Hero 场景

Hero 是一个 25 秒的全合成障碍物安全停车事件，不使用相机：

- 模态：LiDAR 渲染图、距离与运动遥测图、结构化事件日志。
- 虚构机器人：`demo_bot_01`。
- 演示安全阈值：`1.2 m`。
- 近距离障碍：`0.74 m`。
- 规划速度保持 `0.80 m/s`，安全控制器将 applied command 钳制为零，实际速度随后降为零。
- 虚构事件：`DEMO_OBSTACLE_STOP_01` 与 `DEMO_OBSTACLE_CLEAR_01`。

事件弧：

```text
t=0.0–10.0s   正常行驶
t≈10.3s       front distance 跌破演示阈值
t=10.4s       LiDAR 近距离障碍证据
t=10.6s       safety stop assert，applied command 被钳为 0
t=11.3s       实际速度降为 0
t=20.2s       障碍开始移除
t=20.4s       safety stop clear
t=21.1s       实际速度恢复至名义值
```

这些数值都是项目专用的演示参数，不代表任何真实设备配置。

## 基本原则

- 真实 rosbag 不进入项目仓库、Git 历史、云端、Gemini 请求或演示设备。
- 生成脚本不得读取真实 bag；合成数据必须由 `tools/scenario.py` 从零生成。
- 不通过改名、裁剪、平移、旋转、模糊或加噪来“脱敏”真实数据。
- 只保留抽象因果模式，不保留来源系统的标识、拓扑、参数组合或时间序列。
- 所有公开名称、坐标、事件码、时间戳和配置均为虚构值。
- 无法确认是否敏感的信息默认不使用。

## 可以参考的内容

- ROS 标准消息类型和公开接口概念。
- LiDAR、运动遥测、安全状态和事件日志之间的一般因果关系。
- 常见 AMR 故障类别和合理的物理变化趋势。
- 公开标准与开源项目中已有的通用字段。

参考只允许停留在抽象设计层，不能复制真实字段值、topic 名称、帧名称、错误码或参数组合。

## 禁止进入公开资产的内容

- 真实 rosbag、数据片段、扫描帧、地图、轨迹或坐标序列。
- 真实相机帧、录音、人员影像、标签、屏幕或现场照片。
- 客户名称、公司内部名称、项目代号和设施布局。
- 真实机器人序列号、UUID、MAC、IP、主机名或设备 ID。
- 真实 ROS topic、namespace、节点、frame、启动参数和私有消息类型。
- 内部错误码、日志原文、软件版本、容器名、源码路径或部署路径。
- 真实标定、传感器外参、控制器参数和安全阈值。
- 能通过时间、位置、轨迹或多项参数组合反推出来源的信息。

## 各类合成数据策略

### LiDAR

- 用程序生成标准 `LaserScan`。
- 正常阶段生成远距离背景回波；障碍阶段在正前方扇区生成虚构近距离簇。
- 不使用、裁剪或变换任何真实扫描。
- 导出的 LiDAR PNG 仅来自合成 scan。

### 运动遥测

- 从零生成 planner、applied 和 actual 三条速度信号。
- planner 表示规划请求；applied 表示安全控制后的命令；actual 表示机器人实际速度。
- 坐标、速度、减速时间和恢复时间均由演示场景定义。

### 安全状态与日志

- 使用项目专用的 `demo_` 节点、topic 和事件码。
- 日志全部重新编写，不包含真实路径、版本、账号、网络地址或内部术语。
- assert/clear 必须显式配对，支持确定性的 event-state 计算。

### rosbag 元数据

- 使用固定的合成基准时间以保证可复现。
- bag 名、topic、frame 和 namespace 全部使用演示命名。
- 不写入录制主机、真实日期、用户路径、构建环境或来源标识。

## 生成与发布流程

- 在 `tools/scenario.py` 定义完整合成时间线和阈值。
- 使用 `tools/generate_synthetic_bag.py` 生成 ROS1 bag。
- 使用 `tools/export_incident_assets.py` 导出 LiDAR PNG、charts、timeline 和日志。
- 使用 `tools/validate_incident.py` 检查数据、证据和恢复规则。
- 后端和 validator 共同使用 `tools/incident_rules.py`。
- 仅发布代码、文档和由合成 bag 导出的固定资产。

## 发布前检查清单

- [ ] 仓库与 Git 历史中不存在真实 rosbag 或真实数据片段。
- [ ] 文档中不存在真实 bag 文件名、内部 topic、私有消息类型或真实错误码。
- [ ] 所有 LiDAR、曲线、日志和时间线均由合成脚本生成。
- [ ] topic、节点、frame、namespace 和事件码均为 `demo_` 命名。
- [ ] 不存在真实用户名、路径、IP、主机名、版本或内部术语。
- [ ] 文件名、图片元数据、bag metadata 和时间戳不包含来源信息。
- [ ] Gemini 请求只包含已批准的合成数据。
- [ ] `metadata.json` 明确标注 `synthetic: true`。
- [ ] 数据验证、后端验证和在线验收结果符合冻结基线。
- [ ] 至少由另一人独立检查一次公开资产。

## 发现问题时

如发现公开资产可能包含真实信息，应立即停止上传和展示，移除相关内容并重新生成。若内容已进入 Git 历史、云端或外部 API，应按所属组织的安全流程处理历史清理、凭据撤销和事件报告。
