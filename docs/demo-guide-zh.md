# 系统功能与演示指导（中文）

> **V3 更新（2026-09-16）**：系统已升级为三端闭环（客户 App `/customer`、技师 App `/technician`、调度工作台 `/`）。本文的 V2 说明仍然有效，但以下位置有变化：顶栏新增一键 **Reset demo**（初始化全部：场景、时钟、订单、事项、会话，并通知客户/技师标签页）；**Inject Event** 与场景切换移到了 **Demo controls** 抽屉（含三种负载场景与负载摘要）；KPI 条精简为 4 项（其余在 more counters）；右栏新增 **Human queue**（人工事项）和 **Agent activity**（工具轨迹）；地图上的技师位置按状态着色并随模拟时钟移动；固定午休取消，改为动态休息。三端演示流程见 `docs/v3-demo-guide.md`。

面向：项目负责人自己走一遍全部功能。配套英文文档：`docs/demo.md`（简版脚本）、`docs/architecture.md`、`docs/scoring.md`、`docs/data-contracts.md`。

---

## 0. 启动与当前配置

```bash
scripts/dev.sh        # 后端 http://127.0.0.1:8100（/docs 是 Swagger）· 前端 http://127.0.0.1:5174
```

当前 `.env` 的三个"真实"开关都已打开，工作台顶部徽章会显示：
- `LLM real · openai_compat · deepseek-ai/DeepSeek-V4.1-Flash` —— 客户对话理解走 DeepSeek（每轮 5–20 秒）；
- `route osrm (router.project-osrm.org, ×1.25+3m)` —— 通勤时间来自真实路网（OSRM 公共服务器，自由流时间 ×1.25 + 3 分钟停车）；
- `catalog 46 items` —— 维修问题库来自你的 CSV；`/health` 里还能看到 `onemap_configured: true`（地址解析用 OneMap）。

演示前一定先点右上角 **Reset**：把模拟时钟拨回 **08:30**（2026-09-15，新加坡时间），重建 8 名技师、20 张基础工单和基线排班。时钟默认**暂停**，只有你点 `+1m/+5m/+15m` 或 `Run` 时间才会走。

---

## 1. 系统全景

| 组成 | 作用 |
|---|---|
| 客户 Chatbot（`/customer`） | 客户用自然语言（中/英）描述问题 → 系统对照 CSV 问题库、查地址、收集时间窗和联系方式 → 确认卡 → 建单派单；之后查进度、模拟付费加急、投诉、取消 |
| 调度工作台（`/`） | 调度员看时间轴/地图/工单/风险，推进模拟时钟，注入异常事件（技师请假、投诉、取消、付费），审批需要人工的方案，查看 Agent 执行日志和版本历史 |
| 后端 Agent | UnderstandingAgent（理解+查表+地址解析）、SchedulingAgent（求解+校验+评分+解释）、RiskMonitoringAgent（每分钟扫描风险）、Orchestrator（确定性流程）、PolicyEngine（自动/人工/禁止的唯一裁决者）；LLM 只做语义理解和投诉分类，**不决定**时长、优先级、分数、审批 |

---

## 2. 调度工作台逐块说明

### 2.1 顶栏
- **时钟** 08:30 + `PAUSED/RUNNING`：全系统唯一的业务时间（排班、风险、取消、过期都用它）。
- `+1m / +5m / +15m`：按分钟推进，每一分钟都会依次执行"出发→到达→开始→完成"的自动执行事件，再做一轮风险扫描。
- `▶ Run / ⏸ Pause`：后台每 1 秒真实时间推进 1 分钟模拟时间。
- `Scan now`：手动触发一轮风险扫描（平时暂停状态下每 60 秒真实时间自动扫一次）。
- `Generate Schedule`：对所有"OPEN 且未分配"的工单跑一次初排（批量里只要有一张分数 ≤70 整批进审批）。
- `Inject Event`：注入五类结构化事件（见 2.7）。
- `Reset`：显式重置场景（scenario generation +1，旧的异步结果不会写回）。

### 2.2 KPI 条
Open orders / Assigned / Unassigned / Urgent (P0/P1) / Active risks / Pending review / Manual queue（非排班投诉、执行中断等人工队列）/ Total travel（生效排班的总通勤分钟）/ Completed / Cancelled；最右边显示当前排班版本号和路线提供方（`osrm`，若外部服务失败会显示 `DEGRADED`）。

### 2.3 技师时间轴（Technician timeline）
每行一名技师，横轴 07:00–19:00，红色虚线是"现在"。
- 灰色块 = 通勤（travel），浅灰 = 到达后等待窗口开始（wait），彩色块 = 维修服务（颜色 = 优先级：红 P0、橙 P1、黄 P2、蓝 P3），斜纹 = 午休，粉底 = 技师不可用；🔒 = 已出发/已到达/维修中，**锁定，任何等级都不能改派**。
- 点某个块 → 右侧打开该工单详情；点技师名字 → 地图上画出他整条路线。
- 观察点：出发时间是"刚好赶上"倒推的（不会一大早就到客户家等三小时）；通勤/服务不会穿过午休。

### 2.4 地图（Map）
OpenStreetMap 底图，整行宽，左下角有图例。圆点 = 待服务工单（颜色 = 优先级，点击打开详情）；黑色圆形首字母徽章 = 技师当前位置（红 = 不可用，紫 = 执行中），悬停显示技能，**点击打开技师详情**并画出他的**真实道路折线**（OSRM 几何，紫色 = 已出发的锁定段；折线悬停显示计划分钟 / 自由流分钟 / 公里）。右上角可切换 SVG 示意图（瓦片失败时自动降级）。审批卡上悬停时，受影响工单在地图上高亮成紫框。

### 2.5 工单表（Work orders）
整行宽，在地图下方（左栏可上下滚动）。搜索框（ID / 客户 / 问题 / 地点）+ 优先级 / 状态过滤。列：Order（💳 = 付费加急）· Pri · Problem（含工种、复杂度 L#、固定时长）· Location（📍 = 地图落针或地址解析的精确点）· Window · Technician · start · Status（🔒 = 已出发）。状态含义：
- 生命周期：`OPEN → EN_ROUTE → ARRIVED → IN_PROGRESS → COMPLETED`，或 `CANCELLED`；
- 调度状态：`UNASSIGNED`（无有效分配）、`PENDING_REVIEW`（候选方案等审批）、`ASSIGNED`、`UNRESOLVED`（权限内找不到可行方案，待恢复）。

### 2.6 工单详情抽屉（点任何工单）
- **Customer request**：原话、客户、地点、预约窗口。
- **Catalog entry (fixed parameters)**：目录条目、复杂度（技师等级必须 ≥ 它）、固定时长（来自 CSV，LLM 无权改）。
- **Priority reasons**：base / risk / effective 三个优先级和每条原因（付费保底、技师取消剩余分钟、已超截止等）；过期会显示 *Deadline breached at …*（恢复后也保留），恢复单显示 *Recovery start*。
- **Assignment & execution**：技师、计划出发/到达/等待/服务时间、匹配分及五个分项；实际时间戳；**技师面板按钮**（depart / arrive / start / complete，模拟技师手机上报）、`Cancel order`（未出发才有）、`Re-run dispatch`。
- **P2 standby**：P2 且有有效安排时，列出最多 3 位备用技师和各自最早可开始时间，明确写着"不是资源预订"；没有可行备用时如实显示为空。
- **Active risks / Recent candidate plans**：该单的风险和历史候选。

### 2.6b 技师详情抽屉
入口：点时间轴上的技师名字、地图上的技师徽章，或右栏 **Technicians** 标签里的一行。内容：姓名 / ID / 状态；**技能表**（工种 → 等级，5 格条；工单复杂度 ≤ 技师等级才有资格接单）；班次、午休、不可用时段、驻地、"下一个可出发的地点与时间"（锚点）；今日负荷（计划任务数、通勤、服务、利用率）；**按顺序的路线表**（出发 / 开始–结束 / 通勤 + 等待，点单号跳到工单详情）；`Report unavailable…` 直接打开预设好该技师的事件注入。

### 2.7 事件注入（Inject Event）
| 事件 | 含义 | 系统反应 |
|---|---|---|
| technician_unavailable | 技师临时不可用（从当前时刻起） | 立即更新技师事实；未出发任务失效、按"距截止剩余分钟"分级（<30 → P0，30–120 → P1，>120 → P2）并逐单恢复；执行中的任务 → `EXECUTION_INTERRUPTED` 转人工，不自动改派 |
| paid_expedite_order | 模拟付费加急 | base 变 P1，重新派单（可移动任意数量未出发 P3，各自仍在窗口内） |
| lateness_complaint | 迟到投诉 | 按时钟/窗口/是否已开始核实：确实超截止且未开始 → P0；否则只记录 |
| non_scheduling_complaint | 态度/质量投诉 | 进人工服务队列，**优先级不变，不进求解器** |
| customer_cancel | 调度员代客户取消 | 同客户端规则：已出发/到达/维修中拒绝 |

### 2.8 右栏
- **Active risks**：当前活动风险（类型、等级、目标单、原因）。
- **Technicians**：全部技师一览（状态、班次、任务数、技能条、下一空闲点）；点一行打开技师详情。
- **Review queue**：待审批候选卡（见第 5 节）；下方 *Recent plans* 列出自动提交/被取代/过期的方案。
- **Agent activity**：每次派单的执行轨迹：load_snapshot → classify_priority → solve → validate_score_policy → commit/pending_review/unresolved，每步的工具调用和事实（候选数、耗时、影响单、分数、政策原因）。这里展示的是**工具与事实**，不是模型的隐藏思维链。
- **Notifications**：应用内模拟通知（客户 ETA、技师任务、调度员告警），全部标 `simulated`，不发真实短信。
- **Versions**：排班版本链（父版本、原因、变更单数、政策版本、路线快照 ID），用于前后对比。

---

## 3. 客户 Chatbot 逐块说明

- **对话区**：发送后客户气泡立即出现，随后出现 "thinking… (real model, may take 10–20 s)"，回复到达后替换。每轮系统只问**一个**缺失项（问题 → 地点 → 时间窗 → 联系方式），已收集的信息不会再问。
- **选项按钮**：目录候选（识别不确定时）、`🗺 Pick exact location on map`（地图落针）、地址候选（地址有多个匹配时）、区域按钮、时间段、联系表单、`Confirm & submit`、`💳 Simulate expedite payment (→ P1)`。
- **确认卡**：问题（含复杂度和固定时长）、地点、时间窗、联系方式、优先级（P3 或 P1 付费）。
- **My orders**：本会话自己的订单（其他会话看不到，也改不了）：状态、ETA、技师；`Cancel`（未出发）、`Pay expedite`、`Complain`（文本会被分类为迟到/态度/质量）。
- **Repair catalog**：浏览/搜索 CSV 问题库（支持中文关键词），点一条直接选定。
- `New request` 清空草稿；`New customer` 换一个模拟客户身份。

---

## 4. 核心概念速查

| 概念 | 含义 |
|---|---|
| 目录固定映射 | 复杂度、维修时长只来自 CSV 快照；工单创建时把目录参数复制到工单上，目录更新不影响已提交的单 |
| 优先级 | base：普通 P3，模拟付费 P1；risk：由风险规则算出；effective = 更紧急者。付费单风险消失也不会低于 P1；说"很急"不会升级 |
| 权限（authority） | P3/P2 不能改动任何其他单；P1 可移动任意数量**未出发的 P3**（无上限，2026-09-18 起）；P0 最多移动 5 张**未出发的 P2/P3**；任何等级不能动 EN_ROUTE/ARRIVED/IN_PROGRESS |
| affected（影响数） | 除目标外，技师或计划开始时间发生变化的已有工单数量（只变通勤不计数，但会显示） |
| 匹配分 | 技能 30 / 通勤 25 / 响应 20 / 负荷 10 / 稳定 15，0–100；决策分 = 本次新增或改变的分配里的**最低分** |
| 自动/人工 | 严格 >70 才可能自动；P0 只要影响 ≥1 张就必须人工；超权限的方案只做解释（OVER_LIMIT），没有批准按钮 |
| 候选与提交 | 候选只是预览；`commit_plan` 是唯一写入生效排班的入口。批准时在事务里重校验（目标未取消/未出发、版本未变、硬约束仍满足），过期返回 409 并自动重算 |
| 锁定 | 出发后任务锁定；技师完成锁定任务后从完成地点和预计结束时间继续参与后续安排 |
| P2 备用 | 有有效安排时不换人，只准备最多 3 位备用；无有效安排时零扰动补派，找不到就"待恢复" |

---

## 5. 主线演示（约 3–4 分钟，已在当前配置下用浏览器自动化验证）

> 两个浏览器标签：A = 工作台 `/`，B = Chatbot `/customer`。

**步骤 0 · Reset（A）**
看点：8 名技师、20 张单、排班 v2；`wo_001` 🔒 EN_ROUTE、`wo_002` 🔒 IN_PROGRESS；总通勤约 297 分钟（真实路网）。

**步骤 1 · 普通 P3 建单（B）**
输入：`My fridge is making a loud noise, I am at Hougang, please come around 2pm`
看点：一句话识别出 *Refrigerator – Unusual noise (complexity 2, 35 min)*、地点 Hougang、时间窗 14:00–15:30；系统只追问联系方式 → 填名字电话 → 确认卡 → Confirm。回复 "Scheduled: Bala Krishnan plans to start at 14:00"。
回到 A：Bala 行多了一个蓝块，**其他所有块位置不变**（P3 零扰动）；Agent activity 里这次运行的每一步。

**步骤 2 · 付费 P1 有限重排（B，New customer）**
输入：`Aircon not cooling at all in Paya Lebar, need someone between 9:30 and 9:50, my name is Priya, phone 91110002`
点 `💳 Simulate expedite payment` → 卡片显示 *P1 (paid expedite, simulated)* → Confirm。
看点：A 里该单是橙色 P1；打开详情 → Recent candidate plans：COMMITTED 的方案 *affected N / ∞*（移动了 N 张未出发 P3，且它们仍在各自窗口内；P1 不设上限，被挪的只能是 P3）。

**步骤 3 · 为 P0 埋一张单（B，New customer）**
输入：`My door lock is damaged and needs replacement, Punggol, between 10:30 and 10:40, my name is Lee Ann phone 91110004` → Confirm。
看点：派给 Farah Osman (tech_06)，她的时间轴显示约 10:17 出发。

**步骤 4 · 技师请假 → P0 → 必须人工（A）**
点 `+15m` 七次到 10:15，再 `+1m` 到 10:16（Farah 还没出发）。`Inject Event` → technician_unavailable → tech_06 → Send。
看点：
- Farah 整行变粉色（不可用）；Active risks 出现 `TECHNICIAN_CANCELLED … P0`（距截止 24 分钟 <30 → P0）；
- 同一事件里 `wo_015`/`wo_019`（剩余 >120 分钟 → P2）已**零扰动自动补派**（Recent plans 里 COMMITTED · auto）；
- Review queue 出现 P0 卡：唯一方案是 Devi 10:36 接单，但要把 `wo_012` 换给另一位技师 → *Affected 1 / 5* → `policy: manual`（P0 有影响就必须人工，即使分数 >70）；差分表里 `wo_012` 那行打 ✓ 计数，其他行是 travel only 不计数；悬停卡片，地图上 `wo_012` 高亮。

**步骤 5 · 审批（A）**
点 `Approve & commit`。看点：Versions 多一条 "approved plan … by dispatcher"；换锁单变成 Devi 的红块 10:36（窗口 10:30–10:40 内）；`wo_012` 到了 Hana 行，仍在 11:00–12:30 内；Notifications 里两位客户的 ETA 通知。

**步骤 6 · 取消规则（A）**
点一张未出发的蓝块（如 `wo_020`）→ `Cancel order` → 变 CANCELLED，技师释放，后继任务的通勤从真实前驱重算。再点一张 🔒 的单 → 没有取消按钮，写着 "cancel not allowed: technician already in progress"。

**步骤 7 · 新单利用空档（B，New customer）**
输入和被取消单相同的工种/地点/时间窗 → Confirm → 落进刚释放的空档。

**步骤 8 · 刷新（A）**
F5 后一切还在（SQLite 持久化），版本号不变。

---

## 6. 补充场景（各 30 秒–1 分钟）

**6.1 地址解析（OneMap，已用你的账号联调）**
- `aircon not cold, my name is Test phone 90009999, 3pm` → 追问地点 → 回复 `B210A clementi ave 6` → "Location: 210A Clementi Avenue 6 … S121210 (found via onemap)"。
- `冰箱不制冷，邮编 520123，下午4点，我叫小李，电话 90002222` → 邮编直接命中 123 Simei Street 1。
- `toilet clogged, Tampines Street 11, 2pm, …` → 多个候选按钮让客户选。
- 查不到的地址 → 提示选区域或地图落针，不会乱猜坐标。

**6.2 地图落针**：地点提问时点 `🗺 Pick exact location on map` → 点地图 → `Use this location` → 精确坐标下单；A 的地图上该单标 "(map pin)"，通勤按真实路网算。

**6.3 P2 备用（不换人）**：Reset 后在 B 输入 `aircon remote not working, Tampines, between 9:00 and 9:45, my name is Kim phone 90002222` → Confirm（Bala 09:35 开始）。A 推进到 09:15 → 风险 `APPROACHING_DEADLINE P2`，技师不变；详情里 *P2 standby* 面板（本例如实为空：没人能在 09:45 前赶到且不影响他人）。

**6.4 投诉**：B 的订单卡 `Complain` → 输入 "the technician was rude" → 分类为 attitude → 进人工队列、优先级不变（A 的 Manual queue +1）；输入 "technician is late" 而截止未到 → 只记录不升级；截止已过且未开始 → 升 P0。

**6.5 执行面板**：A 打开一张 OPEN 单 → `Technician panel: depart` → EN_ROUTE 🔒 → `arrive` → `start` → `complete`；每一步客户收到模拟通知；出发后这张单在 B 里不能取消。

**6.6 Run 时钟**：点 `▶ Run` 看时间轴自动推进、任务自动出发/完成、风险自动扫描；`⏸ Pause` 停。

**6.7 Generate Schedule**：Swagger `/docs` → `POST /api/orders` 里加 `"dispatch": false` 建一两张未派的单 → A 点 `Generate Schedule` → 初排结果（含低分时整批进审批）。

**6.8 版本对比**：右栏 Versions 看每次提交的原因、变更单数、父版本；`GET /api/schedules/{version}` 可取任一版本的完整快照。

**6.9 执行中断边界**：对正在 IN_PROGRESS 的技师注入 technician_unavailable → 风险 `EXECUTION_INTERRUPTED`（manual），锁定不释放、不伪造完成。

---

## 6b. 两个常问的等级场景（逐步操作，已在当前配置下验证）

### 6b.1 P1 —— 技师取消，距预约截止还有 30～120 分钟
1. 工作台 **Reset**（08:30）。看 Bala Krishnan 行：`wo_003`（Not draining，窗口 09:00–10:30）09:00 开始，他还没出发。
2. 点 `+15m` 一次 → 08:45。此时距 wo_003 的 window_end（10:30）还有 **105 分钟**，落在 30–120 区间。
3. `Inject Event` → `technician_unavailable` → 技师选 **Bala Krishnan (tech_02)** → "until" 留空 → Send。
4. 看点：
   - 返回结果里 `released` 有两张：`wo_003 · 105 min · P1`，`wo_014 · 345 min · P2`（同一技师的两张单按各自剩余时间分级，不是一刀切）；
   - Agent activity：wo_003 这次运行 `classify_priority` 步骤显示 effective P1；`solve` 找到方案 → `policy: auto`（P1 权限内：移动 1 张未出发 P3 `wo_008`，分数 82.9 > 70）→ `commit`；
   - 时间轴：wo_003 跑到 Gopal 行 09:05，`wo_008` 顺延；wo_014 零扰动补派；
   - 打开 wo_003 详情：Recent candidate plans 里 COMMITTED 方案标 target P1、affected 1；Priority reasons 现在又是 P3——因为技师取消风险已经解决，等级会回落（付费单才有 P1 保底）。
5. 想看 P1 **进审批**而不是自动：换一张分数会 ≤70 的单（远地点、技能刚好够），或者先让另一张单在同一事件里已经移动过别人（系统规定同一事件里第二个要移动他人的方案必须人工）。

### 6b.2 P0 —— 已过预约截止时间，技师还没到
思路：先让一张单**没人能接**，让时钟走过它的截止时间。用 Ethan Tan（唯一会修 Gas Stove 的技师）和他的 `wo_010`（Won't ignite，Punggol，窗口 10:30–12:00）。
1. **Reset** → `+15m` 四次 → 09:30（Ethan 还没出发去 wo_010）。
2. `Inject Event` → `technician_unavailable` → **Ethan Tan (tech_05)** → **Unavailable until 填 `12:30`** → Send。
   看点：`wo_010` 剩余 150 分钟 → P2 → 零扰动补派找不到人（没有第二个 Gas Stove 技师）→ 状态 `UNRESOLVED`，风险 `TECHNICIAN_CANCELLED P2`、`UNASSIGNED ETA UNKNOWN`，客户端 ETA 显示 unknown。
3. `+15m` 十次 → 12:00，再 `+1m` → **12:01**（刚过 window_end）。
   看点：风险变成 **`OVERDUE_NOT_STARTED P0`**；工单详情 Priority reasons 显示 P0，*Deadline breached at 12:01*（违约记录永久保留）；因为 Ethan 12:30 回来，系统立刻算出恢复方案（零影响、分数 >70 → 自动）：*Recovery start 13:10*，时间轴 Ethan 行出现红色 P0 块。
4. 继续 `+15m` 两次到 12:31 之后：不会再反复告警（已有恢复安排且没有更早的可能 → no_action）。
5. 变体：
   - 想看 P0 **人工审批**：用主线步骤 3–5 的换锁单（技师取消时距截止 <30 分钟，方案要移动一张 P3）；
   - 想看"迟到投诉核实升 P0"：在第 3 步之前（截止未到）从客户端投诉 "technician is late" → 只记录不升级；12:01 之后再投诉 → 核实 → P0；
   - 如果 "until" 留空（Ethan 整天不可用）→ 12:01 变 P0 后依然无人可接 → 保持 `UNRESOLVED`，调度员收到 unresolved 告警，这就是"无资源转人工"。

## 7. 后台与文档

- Swagger：`http://127.0.0.1:8100/docs`；健康与模式：`/health`（不返回密钥）。
- 评估：`POST /api/evaluations`（10 个 seed，基线"最近可行"vs 本系统），结果在 `data/evaluation/`，解读见 `docs/evaluation.md`。
- 测试：`scripts/run_tests.sh`（64 个 pytest + ruff + mypy + tsc + build）；浏览器主线：`scripts/e2e/run.sh`。
- 文档：`docs/architecture.md`（组件与 Agent 边界）、`docs/scoring.md`（评分公式）、`docs/data-contracts.md`（API/枚举/版本竞争）、`docs/decisions.md`（已确认规则 vs 工程默认值）、`docs/progress.md`（验证记录与未验证项）。

## 8. 常见问题

- **对话慢**：DeepSeek 推理模型每轮 5–20 秒；演示嫌慢可把 `.env` 的 `LLM_MODE=mock`（规则匹配，毫秒级）后重启后端。
- **审批显示 "Plan was stale … recomputed"**：说明批准前排班已变（例如同事件里其他单已自动提交），系统拒绝提交旧方案并自动重算，点新卡再批准即可。
- **路线徽章 DEGRADED**：OSRM 公共服务器不可用，整轮退化为估算；自建 OSRM 见 README "Routing"。
- **想回到离线可复现模式**：`ROUTE_MODE=fixture`、`LLM_MODE=mock`、`GEOCODE_MODE=none`。
