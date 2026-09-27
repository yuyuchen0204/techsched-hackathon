# V3 演示指导（三端闭环）

面向：项目负责人现场演示。V2 的逐块说明仍在 `docs/demo-guide-zh.md`（其中"Inject Event / Reset 在顶栏"的描述已被本版取代：两者都移到了 **Demo controls** 抽屉里）。改动清单见 `docs/v3-change-summary.md`，Agent 工具见 `docs/v3-agent-tools.md`。

演示用三个浏览器窗口（或标签）：
- **调度工作台** `http://127.0.0.1:5174/`（桌面宽度）
- **客户 App** `http://127.0.0.1:5174/customer`（建议用浏览器"设备模拟"调到 400px 宽，页面本身是手机版式）
- **技师 App** `http://127.0.0.1:5174/technician`（同样 400px）

三个窗口共用同一个模拟时钟（工作台顶栏的时间）。
- **数据流动画** `http://127.0.0.1:5174/flow`（讲原理用，不依赖后端）：一个工单令牌沿真实模块（Agents / Tools / Apps）流动，同一时刻只亮"正在发生"的节点，工具节点（虚线）只在被调用时亮起，每一步列出具体输出字段；三个脚本：P3 自动派单、P0 请假人工审批、无解 → Agent 运行时 → 转人工。空格暂停、←/→ 单步、R 重播；最后一步停留更久后循环。所有短信 / 支付 / 拨号 / GPS 都是模拟的，界面上都有标注。

---

## 0. 准备（30 秒）

1. `scripts/dev.sh` 启动后端 :8100 与前端 :5174（`.env` 里 `LLM_MODE=real`、`ROUTE_MODE=osrm`、`GEOCODE_MODE=auto` 已配置）。
2. 工作台右上角 **Reset demo**（红色按钮，确认后执行）：一键把整个演示初始化——场景 main、时钟 08:30、重建 8 名技师和 20 张工单、清空所有订单/方案/人工事项/Agent 任务/聊天会话，并写入三位"老客户"的历史（alice / bob / carol）；同一浏览器里打开的客户 App 和技师 App 会自动收到重置通知（客户端换新会话、技师端回到自动模式）。要换负载场景时用 **Demo controls** → 选场景 → **Load**。
3. 关闭抽屉。此时 KPI 只有四个：**Pending orders / Orders at risk / Pending human / Available technicians**；"more counters" 才展开工程计数。

三种负载场景（同一抽屉里切换，每个都有一句说明和"负载摘要"利用率条）：`main` 忙碌基线；`relaxed` 12 单，便于演示协商和休息；`scarce` 两名技师请假 + 32 单，seed 时就有 7 单无法分配，用来演示 Agent 升级到人工。

---

## 1. 客户端：老客户下单（2 分钟）

窗口：客户 App。

1. 右上角下拉 **I am…** 选 **Alice Tan (4 past)**。助手回复"Welcome back…"：她有保存的电话和默认地址（125 Tampines St 11 #05-123）、4 条历史。历史卡片下方会提示"我们记得你上次的低评价，会尽量安排其他技师"（她给 tech_02 打过 2★）。
2. 点 **Use saved phone & default address**。
3. 输入 `aircon not cold`（或中文 `空调不冷`）→ 助手匹配到 **问题库**里的条目（只会给出 CSV 里存在的问题，另附 2 个备选按钮），并显示"similar repair record"提示（同一客户 90 天内的空调维修记录，措辞不说"同一台机器又坏了"）。
4. 地址确认后助手先问 **"Do you need this expedited?"**（中文会话为"这次需要加急吗？"；两个按钮：**Yes, expedite** / **No, a free slot is fine**；如果客户在描述里已经写了"urgent / 尽快 / 急"，这一步自动跳过、按加急处理）。然后给时间窗按钮，每个都写了技师和最早可到时间：
   - **不加急**：只给 **🕒 Free · no disturbance** 窗口（零打扰试插，例如 `09:30–11:00 · Bala Krishnan from 10:33 · Free · no disturbance`），最多 4 个。
   - **加急**：额外做一轮 P1 试排（允许把任意数量未出发的普通订单往后挪或改派，各自仍在窗口内），最多 2 个 **💳 Paid expedite** 窗口和 2 个空闲窗口合并后按最早可到时间排序；💳 按钮写明 `Paid expedite · moves N normal order(s)`，分数 ≤70 时再加 `needs dispatcher confirmation`。**加急并不比空闲更早时，💳 项不会出现**，助手会说明 "Expediting would not be earlier than the free windows, so only free windows are listed."
   这里仍会出现 §8.3 的说明：可行窗口只有 Bala（她曾打低分）→ 可以继续，也可以点 **Do not send Bala Krishnan this time**（仅本单排除，再算一遍窗口）。
5. 选一个窗口。**只有加急的客户**接着被问是否付费（**Confirm payment (simulated)** / **No payment**），文案如实说明付费的作用：选了 💳 窗口 → "Paying raises the order to P1: it is scheduled first and, if needed, a few normal orders are moved slightly later; afterwards it cannot be bumped by other normal orders. Payment in this demo is simulated"；选了空闲窗口 → "You already have a free window, so paying will not make it earlier — it only raises the priority so the slot cannot be bumped later"。选 💳 窗口又**不付费** → 该时段作废，重新只给空闲窗口。之后填联系方式 → 确认卡（问题 / 地址 + 单元号 / 时间窗 / 联系方式 / **Urgent: yes/no · Paid: yes/no (P1, may move N normal order(s))** / 优先级）→ **Confirm & submit** → "Order wo_… created. Scheduled: … plans to start at …"；付费单的 dispatch 分数 ≤70 进审批时会告诉客户 "A dispatcher is confirming the technician."（中文会话："调度员确认中"）。
6. 切到 **My orders** 标签 → 点这张单进入**订单页**：状态、时间窗与计划开始、地址（含单元号）、技师；按钮：**💳 Expedite (simulated payment)**、Cancel、Ask to reschedule（提交时间窗 → 生成 CUSTOMER_REQUEST 人工事项，调度员审批后生效）、Talk to a human、投诉输入框。完成后会出现评分表；技师出发后出现**实时跟踪地图**（位置由路线几何 + 模拟时钟推算，页面注明）。
7. **已下单后加急 = 付费并自动改到最早时间**（只针对已创建、未出发的订单；下单前的"是否加急 / 是否付费"流程不变）。按钮旁有一行灰字预告，来自只读试算：`Earliest possible start after expedite: 10:40 (moves 1 other appointment)`，算不出更早时间时是 `Expedite raises priority; no earlier slot is available today.`。点击后一次原子操作：记录模拟付费 → 基础优先级 P1 → 以"最早服务开始时间"为目标重排（先零打扰试插，再用 P1 权限"紧急前插"：让**能最早赶到的技师立刻出发**，把他后面放不下的普通订单改派给别人，不限数量、各自仍在窗口内）→ 只有比当前计划开始更早才采用，并把预约时间窗改成以新开始时间为起点、长度不变的窗口：
   - 分数 > 70 且在权限内 → 自动生效（新的排班版本），横幅："Your order is expedited (simulated payment). New appointment: 10:30–12:00, technician Devi Nair, planned start 10:40 (was 14:00). Tap ‘Keep original time’ if the earlier slot does not suit you."；被挪动的客户收到 "Your appointment was adjusted to 11:00–12:30 to make room for an urgent job; your original window … is still respected."；技师 App 顶部出现 "Schedule updated: wo_… moved to 10:40 (expedited)."
   - 分数 ≤ 70 或按规则需人工 → 进工作台 **Review queue**（调度员通知 "Expedited order wo_… needs approval for an earlier slot (moves 2 P3 orders)."），审批前原时段保留，客户看到 "Your order is expedited. A dispatcher is confirming an earlier slot (planned start 10:40); your original appointment stays until then."；审批时方案和新时间窗一起生效，拒绝则时间不变、P1 保留。
   - 找不到更早方案 → 时间不变，只保留 P1："Your order is expedited: priority raised, so it will not be displaced by regular orders. No earlier technician is available today — your appointment time is unchanged."
   - 订单页信息行：`Expedited (P1) · moved from 14:00 to 10:40` / `Expedited (P1) · time unchanged`。改早后出现 **↩ Keep original time**（未出发前可用）：时间窗改回原来的、重新按 P1 排回原时段（零打扰优先），优先级仍是 P1、不退款（模拟）；重复点击幂等。重复点 Expedite 也幂等："Already expedited — no second charge."；已出发/完成/取消的订单："Expedite is only possible before the technician departs."
   - 聊天里对已有订单说 `please hurry my order` / `make it faster` / `expedite`：助手先用一句话说明后果（含只读试算的最早时间和会挪动几张单），再给 **Expedite now (simulated payment)** / **Keep as is**。

新客户路径（可选）：**New session** → 输入 `toilet is leaking at Blk 123 Tampines St 11` → 助手识别问题并给出 **📍 Enter address & unit**：弹出地址窗口（搜索 / 邮编 / 地图落针，OneMap 结果会预填），**单元号必填**（或勾选"no unit"），可选"保存为默认地址"。没有单元号系统不会建单。

---

## 2. 安全事件与转人工（1 分钟）

窗口：客户 App（任一会话）。

1. 输入 `I smell gas near the water heater` → 助手不再排班，回复安全指引，给出 **Contact emergency services** 面板：SCDF 995 / Police 999 / City Energy 1800 752 1800（均来自官方页面，`config/policy.yaml` 里记录来源），按钮只是 `tel:` 链接 + "I have contacted them"记录，**不会自动拨号或上报**。同时生成 SafetyIncident 和一条 **critical** 人工事项。
2. 反例：输入 `no gas smell, the stove just won't light` → 不触发（否定词保护）；`last week there was a small fire` → 不触发（过去式保护）。
3. 任意时刻点 **🙋 Talk to a human**：助手暂停（顶部黄色横幅），之后客户输入都进入人工事项，**不需要重复描述**——工作台的事项里带着对话摘要与已收集的信息。

窗口：工作台 → **Human queue** 标签：三种来源用颜色区分（customer request / policy required / agent escalation）。点 **open** → **Take** → 输入回复 → **Reply**：客户 App 横幅与订单页立刻显示这条回复。**Resolve** 后助手恢复工作（客户再发消息会正常接单）。

---

## 3. 技师端：手动执行、请假、休息、服务报告（2 分钟）

窗口：技师 App。

1. 右上角选技师（如 **Aaron Lim**）。看到：模式条（Auto：模拟器按计划推进；Manual：只有本 App 能改变状态，**单一驱动**）、当日时间条、**Rest** 卡（自上次休息以来的工作分钟、等级 none / pre_evaluate / evaluate / escalate、已计划的休息）、**当前/下一单**卡（问题、计划离开/到达/开始、地址 + **单元号**、客户电话、备注、历史提示）、路线图、当日全部工单、请假表。
2. 点 **Take manual control** → 下一步按钮亮起（Depart now / Arrived / Start work / Complete）。按实际顺序点；工作台的时间轴、地图位置（状态颜色：绿 available、靛 en route、紫 arrived/busy、青 break、红 unavailable）和客户订单页同步变化。工作台点 **▶ Run** 后，地图上的技师沿真实道路几何**连续滑动**（每 1.5 秒取一次位置并平滑过渡，不再跳变）；客户订单页的跟踪地图同样连续。若比计划早/晚、比窗口早/晚，事件记录里会带 anomaly 标记，**不会被改写成合规**。
3. **Complete** 后出现 **📝 Service report**：实际问题、处理结果、被打断分钟数、异常标签、备注。实际服务时长 = start → complete（不含通勤和提前到达的等待），进入时长评估数据。
4. **休息**：点 **☕ Take a 30-min break now** 记录一次休息事实（随后的风险扫描检查后续工单）；自动路径是：连续工作 180 分钟（提前 60 分钟预评估）后，系统在 90 分钟内寻找"零打扰"的休息位，以版本校验提交；240 分钟仍无休息则升级为高优先级人工事项。演示自动路径：工作台把时钟推到 12:00 之后点 **Scan now** → **Agent activity** 里出现 `break` 任务及其工具轨迹。
5. **请假**：Leave / unavailable → report → **Report unavailable from now**（可填 until 时间）。未出发的工单立即释放并进入恢复；正在执行的工单变为人工事项（锁不自动释放）。重复提交同一请假是幂等的。

---

## 4. 主线：技师请假 → P0 审批（工作台 + 技师 App，2 分钟）

与 V2 主线相同，只是事件从技师 App 发出：
1. 客户 App 用新会话下一单"door lock damaged, Punggol, between 10:30 and 10:40"（会分给 Farah tech_06）。
2. 工作台用 `+15m/+5m/+1m` 推到 Farah 计划出发前 1 分钟。
3. 技师 App 选 **Farah Osman** → 请假。距离截止 <30 分钟 → 该单 P0；工作台 **Review queue** 出现需要审批的方案（P0 且影响他人 → 必须人工），**Pending human** KPI 变 1（policy required 事项）。
4. 审批 → 排班新版本；人工事项自动关闭。

---

## 5. Agent 运行时与开发者视图（1 分钟）

1. **Demo controls** → 载入 **scarce**。工作台 **Work orders** 里有多张 `UNASSIGNED`。对其中一张点开详情 → **Re-run dispatch**（或直接调用 `POST /api/scheduling/dispatch`）→ 快路径 `unresolved` → 创建 **AgentTask**。
2. **Agent activity** 标签：任务状态（pending / running / waiting_customer / waiting_human / succeeded…）、工具预算用量（12 次调用 / 3 次搜索）、点 **trace** 看逐步轨迹：`planned → called → returned/validated/submitted`，每步带状态、reason codes、耗时、`decided_by: model|mock`。真实模型（DeepSeek）驱动时，典型轨迹是：读取上下文 → 零打扰试插失败（`NO_ZERO_DISTURBANCE_SLOT`）→ 提出备选时间窗 → 带证据转人工（Human queue 里 `agent escalation`，附 attempted actions 与建议下一步）。
3. **⚙ Dev** 抽屉：模型 / 路线 / 地理编码 / 策略版本；**时长评估（shadow）**：点 Rebuild report 看 CSV 基线 MAE 与影子模型 MAE、样本质量分布（carol 的 12 条历史里有一条 240 分钟的离群值会被标记 outlier）；最近的工具轨迹原始列表。影子预测**不会**改变排班时长。

---

## 6. 自动化验证

- 后端：`scripts/run_tests.sh`（103 个测试；`tests/test_expedite.py` 8 个：已下单后加急 P1 生效且计划开始变早（affected ≤ 2）、无更早方案时间不变并说明、已出发拒绝、分数 ≤ 70 进审批且原时段保留 → 审批后方案与时间窗一起生效、拒绝保留原时段、Keep original time 恢复原窗口且仍为 P1、重复加急/撤回幂等、客户/被挪动客户/技师/调度员通知；`tests/test_v3.py` 31 个：加急 → 时间窗 → 付费的槽位顺序、`propose_windows(allow_moves=True)` 每条 ≤2 张受影响且 paid_required、拒绝付费后只剩空闲窗口、不加急永不问付费、实际服务时长 ≠ 出发时刻、影子预测不改排班、请假/投诉/休息幂等、休息提交版本冲突与打扰拒绝、Agent 成功即停 / 失败换搜索 / 预算耗尽转人工、工具角色与参数拒绝、越权方案只能进审批、安全触发的否定/过去式保护、转人工暂停助手、历史提示不匹配共享区域、低评分只是软偏好）。
- 浏览器：`scripts/e2e/run.sh`（24 项检查，含订单页 Expedite → 审批 → 改早 → Keep original time 出现，三端同时驱动，客户端与技师端为 400px 宽；截图在 `data/evaluation/screenshots/`）。
