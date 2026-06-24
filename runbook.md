# 现场 Runbook — Robot Incident Investigator

单日 hackathon 现场操作手册。目标:**稳定跑完 3 分钟 hero demo**,任何环节出问题都有降级话术。

---

## 0. 赛前一次性

- venv:`/home/shaoningyu/projects/rosbag_cc/claude-mcp-rosbags/venv`(已装 rosbags / PIL / numpy / httpx / starlette / uvicorn)。
- API key 放文件,不进对话/仓库:`umask 077; printf '%s' '<KEY>' > ~/.gemini_key`
- 资产已生成在 `incident/`(若缺,重跑流水线,见 §5)。

简写:`V=/home/shaoningyu/projects/rosbag_cc/claude-mcp-rosbags/venv/bin/python3`

---

## 1. 起服务

```
cd <project>
GEMINI_API_KEY="$(cat ~/.gemini_key)" PORT=8000 $V backend/app.py
```

浏览器开 **http://127.0.0.1:8000**(必须本机浏览器;远程则 `ssh -L 8000:127.0.0.1:8000 …`)。

## 2. 开演前自检(30 秒)

- `curl -s http://127.0.0.1:8000/health` → `integrity_ok:true` 且 `gemini:true`。
- 页面顶部徽标应显示 **Gemini: available**。
- 点 "Jump to stop" → LiDAR/曲线跳到 10.6s;点一条证据 → 时间轴定位。
- 跑一次 "为什么停?" 确认在线出答案(徽标变 ● Online Gemini)。

赛前若有网,跑三套验收留底:
```
$V tools/validate_incident.py          # 期望 20/20
$V backend/test_backend.py             # 期望 30/30(真实 uvicorn+HTTP)
GEMINI_API_KEY="$(cat ~/.gemini_key)" $V backend/online_check.py   # 期望 6/6
```

---

## 3. Demo 脚本(3 分钟核心闭环)

1. 开场一句:*"When a robot stops, engineers dig through gigabytes. Watch anyone interrogate the incident."* 强调数据是 **fully synthetic, inspired by common AMR failure patterns**(不提任何真实错误码、真实 bag、真实命名约定或内部系统细节)。
2. 播放时间轴 → 机器人在 ~10.6s 停住。
3. 输入 **"为什么停?"** → 实时进度(thinking → inspect_incident_window +5 images)→ 根因 + 证据时间戳 + `evidence_strength: high`。
4. **点答案里的时间戳**(10.4/10.5/10.6/11.3)→ 时间轴跳转 + 证据卡高亮。强调"一键跳到证据"。
5. 追问 **"恢复条件?"**(多轮带上下文)→ `recovery_readiness: blocked` + 条件清单。**口播:这是恢复条件检查,不是安全认证。**
6. 闭环到此。剩余时间再演 stretch(反事实 / 正常对比)。

一句 pitch 收尾:*"Stateful multimodal investigation with evidence-grounded, hallucination-checked diagnostics — all on the Gemini API."*

---

## 4. 故障预案(按概率排序)

- **Gemini 503 / 网络抖动**:重试已内置(429/500/503 退避 3 次);若仍失败,徽标自动翻 **● Offline deterministic fallback**。
  - 口播:*"Notice it degraded gracefully — same deterministic evidence rules, same root cause and recovery verdict, just without live Gemini narration. The diagnosis doesn't depend on the network."* 然后继续用离线答案讲(根因/证据/恢复都在)。
- **在线验收当天就过不了**(配额/区域/模型不可用):赛前若 `online_check.py` 不过,**直接以离线模式开演**,把上面那句"degrades gracefully"作为主线卖点之一,不要现场临时调 key。
- **页面打不开**:确认本机浏览器 + 端口;`curl /health` 验服务在;远程加 SSH 端口转发。
- **端口被占**:换 `PORT=8001 …` 重起,浏览器改端口。
- **请求卡住**:页面有 **Cancel** 按钮 + 45s 超时 + 断连 "retry";点 Cancel 或 retry,别狂点 Ask(已防并发)。
- **服务崩了**:Ctrl-C 重起 §1;`incident/` 资产是静态的,重起秒级恢复。
- **数据看着不对**:`$V tools/validate_incident.py` 应 20/20;不对就重跑流水线 §5。

---

## 5. 重建资产(万不得已)

```
$V tools/generate_synthetic_bag.py     # 合成 bag(确定性,SHA 可复现)
$V tools/export_incident_assets.py     # → incident/
$V tools/validate_incident.py          # 20/20
```

---

## 6. 冻结与备份(赛前务必)

- **冻结 commit/tag**:见 §7,锁一个 `demo-verified` tag,现场只从这个 tag 起服务。
- **备用录屏**:赛前用本机录屏工具(或浏览器录制)把 §3 脚本完整录一遍(在线 + 故意断网演示离线降级各一遍)。现场若彻底起不来,直接放录屏 + 口播,不空场。
- key 不进录屏/截图/仓库。

---

## 7. 一键起服务命令(贴墙)

```
V=/home/shaoningyu/projects/rosbag_cc/claude-mcp-rosbags/venv/bin/python3
cd /home/shaoningyu/projects/allmemory/Robot_Incident_Investigator
GEMINI_API_KEY="$(cat ~/.gemini_key)" PORT=8000 $V backend/app.py
# 浏览器: http://127.0.0.1:8000  · 自检: curl -s :8000/health
```

验证基线(都该绿):data 20/20 · backend 30/30 · online 6/6。
