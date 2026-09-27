# Technician Scheduling — Code Agent 完整开发 Prompt


版本：V2 · 2026-09-14  
状态：业务规则已确认；工程默认值另行标注。  
用途：交给 Claude Code、Codex 或其他具有文件编辑、终端运行和测试能力的 Code Agent，开发完整可运行系统。


## 0. 使用方式、依据与规则优先级


将本文件放在项目 docs/project-brief.md，将真实维修问题库放在 data/reference/repair_object_problem_database.csv。通过 REPAIR_CATALOG_PATH 可以修改问题库位置。


向 Code Agent 发送：


> 请完整阅读 这个md文件，在这个新创建的项目中执行，按本文逐阶段实现系统，持续更新 docs/progress.md。请实际编写、运行、验证代码，不要只输出方案或静态页面。业务规则按本文执行，普通工程细节自行决策并记录。


本文件综合两次会议及后续逐项确认。冲突时，本文最终规则优先于早期会议和旧 Prompt。特别注意：
- 最终 P1 权限：最多影响 2 张尚未出发的 P3。
- 最终 P0 权限：最多影响 5 张尚未出发的 P2/P3。
- 曾讨论的“P1 最多影响 3 张 P2/P3”已撤回，不得实现。
- 付费 P1 可因风险升 P0；预约窗口约束服务开始；P2 允许缺少有效分配时零扰动补派；客户未出发可取消，已出发不可取消。
- 复杂度与维修时长由问题库固定映射，不能继续使用旧版 LLM 自由估时设计。


本轮未能直接核验附件 CSV 的实际表头、编码和全部数据行。本文规定语义与导入合同，不声称已验证具体列名或条目数量。实施时必须读取用户放入项目的真实文件，建立明确列映射，不根据本文猜测源表头或重造目录。


本文是开发指令，不是已完成软件、已验证性能或真实企业运营制度。评分权重、技术栈、演示数据量等“工程默认值”可在不改变已确认业务政策的前提下调整，并记录原因。


## 1. 任务、角色与工作方式


你负责从已有仓库或空仓库交付一个面向 SME 上门维修业务的智能服务与技术人员调度系统。


这是约三周规模的黑客松原型。重点是：
1. 客户描述到标准工单。
2. 技能、时间、通勤约束下的可执行排班。
3. P0–P3 的不同处理权限。
4. 紧急事件下的受限重排。
5. 自动执行与人工审批。
6. 可复现演示和真实计算的前后比较。


先读取 README、适用的 AGENTS.md 和已有实现。已有代码时增量开发，不覆盖用户修改，不重建已经可用的模块。


不要只交付文档、伪代码、Chatbot 外壳或硬编码演示。核心操作必须有后端计算和持久化。分阶段推进，每阶段形成可运行增量，不为普通工程选择反复要求用户确认。


将实际选择写入 docs/decisions.md，将进度、验证结果和具体阻塞写入 docs/progress.md。外部密钥缺失时继续完成 mock 全闭环；未真实调用的接口不能声称已验证。


## 2. 产品组成与范围


### 2.1 两个主界面


| 界面 | 使用者 | 功能 |
|---|---|---|
| 客户 Chatbot | 客户 | 描述问题、必要追问、核对工单、模拟付费加急、查看安排、投诉、取消 |
| 调度工作台 | 调度员 | 基础排班、普通插单、风险、候选方案、审批、时间轴和路线差异 |


技师首版使用后台面板模拟出发、到达、维修、完成和临时不可用，无需独立手机 App。


### 2.2 必须完成


目录导入、标准工单、技师数据、初排、普通插单、风险分级、P2 备用与补派、P1/P0 受限重排、匹配评分、人工审批、客户取消、技师取消、状态锁定、模拟时钟、Agent 执行日志、路线服务、前后对比、测试和启动文档。


### 2.3 后置


RAG、历史案例检索、专业诊断、长对话、自助排障知识系统、实时 GPS、常态化半路改道、真实支付/退款、真实短信邮件、复杂认证、多租户、备件库存和微服务。


客户反馈与技师提醒首版用应用内通知，标明 simulated。不得为了演示通知擅自调用真实通信渠道。


## 3. 最终业务规则总表


| 等级 | 主要行为 | 允许影响的其他工单 | 数量上限 | 自动与人工 |
|---|---|---|---:|---|
| P3 | 正常排班、普通插单、持续监测 | 不允许改变已有有效工单 | 0 | 可行且分数 >70 自动；低分人工 |
| P2 | 有有效分配时保留安排并准备备用；无有效分配时零扰动补派 | 不允许改变其他工单 | 0 | 补派可行且 >70 自动；低分人工；无方案则待恢复 |
| P1 | 优先空闲资源，必要时有限重排 | 尚未出发的 P3 | 2 | 全部限制满足且新增/改变分配最低分 >70 自动；低分人工 |
| P0 | 紧急恢复 | 尚未出发的 P2/P3 | 5 | 0 影响且 >70 自动；0 影响低分人工；1–5 影响必须人工；超权限告警 |


补充：
- 权限针对其他工单，不阻止尚未出发的当前目标 P1/P0 重新获得安排。
- 被影响工单等级读取本轮快照的 effective_priority。
- P1 不能移动其他 P0、P1、P2；P0 不能移动其他 P0、P1。
- 所有等级都不能打断 EN_ROUTE 或 IN_PROGRESS；已到达但等待服务也按已出发锁定。
- 已完成、客户已取消工单不参与重新分配。
- 人工批准只能批准权限内且通过硬约束的方案，不能借普通批准按钮绕过规则。
- “低分但可行”与“没有找到可行方案”分开。
- Top 3 最多展示三个有实质差异的真实方案，不足三个如实展示。
- 阈值严格 >70；恰好 70 走人工。


## 4. 问题库：真实 CSV 是唯一业务来源


### 4.1 读取合同


默认路径：data/reference/repair_object_problem_database.csv。


语义：
- 工种/维修类别。
- 具体问题。
- 问题复杂度。
- 对应维修时长，分钟。


内部可规范为 trade_type、problem_name、complexity_level、repair_duration_minutes。这些是内部字段，不代表真实源表头。


实施步骤：
1. 检查文件存在性、编码、分隔符、实际表头、示例行与完整数据。
2. 兼容 UTF-8 和 UTF-8 BOM，处理空白、引号字段。
3. 建立显式列映射，可支持明确中英文别名；未知列不得盲猜。
4. 复杂度必须符合源目录采用的合法等级，时长必须为正整数分钟。
5. 工种与问题不能为空。
6. 相同工种+问题完全重复可去重；同一键对应冲突参数必须报错，不能静默取最后一行。
7. 输出导入摘要：实际表头、映射、有效条数、重复条数、错误行和原因。
8. 原始 CSV 只读保存，不擅自改写。


生成稳定 catalog_item_id，保存 source_row、source_file_hash、catalog_version。可由规范化工种+问题生成稳定键；不要仅以易变行号作为长期身份。


### 4.2 固定映射


选定 catalog_item_id 后，复杂度、服务时长、工种都从目录读取。技师工种匹配且等级达到或高于要求才有资格。


不能让 LLM 根据紧急程度、情绪、技师熟练度另算时长。首版服务时长直接使用 CSV，不擅自增加未确认缓冲。路途时间单独由 RouteProvider 计算。


前端提供目录浏览/搜索和加载状态。中英文文案/别名应映射同一目录 ID，不生成两份不一致参数。


### 4.3 快照与缺失输入


正式工单保存目录版本和参数快照。目录更新不能悄悄改变已提交工单的服务时长。


文件尚未放入时：
- 继续实现导入器、其他模块和独立测试 fixture。
- fixture 明确标为测试数据，不冒充用户目录。
- 产品显示“维修问题库未配置”，不能静默使用自编目录。
- README 写明路径；文件放入后无需改代码即可导入。


## 5. 客户 Chatbot 与 UnderstandingAgent


输入包括自然语言、姓名、电话、地址/选点、预约窗口和是否模拟付费加急。


工作流：
1. 收集必填信息。
2. 检索目录，获得可选条目。
3. 识别问题；信息不足则简短追问。
4. 通过目录 ID 读取固定复杂度和时长。
5. 展示确认卡：问题、地点、预约、加急状态。
6. 客户提交后创建正式工单并进入调度。


允许直接选择目录项。无法匹配时进入 NEEDS_INFO 或人工核对，不强行选一个错误问题。


地址通过预设地点、地图选点或独立地理编码服务获得。LLM 不得编造坐标、电话、资质或付费事实。


首版付费为明确的模拟动作：普通 base=P3，已确认模拟付费 base=P1。仅说“很急”不能直接生成 P0，不能冒充付款成功。


Chatbot 可以解释状态和安抚情绪，但不能绕过调度规则、把候选说成已派单，或承诺无依据 ETA。


投诉、取消意图最终调用后端事件服务。客户只能操作自己的模拟会话订单，不能仅凭任意订单 ID 改别人的订单。


## 6. 数据模型与时间语义


所有业务状态持久化，不只保存在前端。实体可以合理拆分，但必须表达以下信息。


### 6.1 核心实体


| 实体 | 关键字段 |
|---|---|
| RepairCatalogItem | id、trade_type、problem_name、complexity_level、repair_duration_minutes、source_version |
| WorkOrder | id、customer_ref、contact、location、description、catalog_snapshot、window_start/end、paid_expedite、base/effective_priority、reasons、lifecycle_status、scheduling_status、version |
| Technician | id、name、skills、可选 certifications、location、availability、shift、breaks、unavailable_intervals、status、version |
| Assignment | order_id、technician_id、origin、departure、arrival、service_start/end、travel、waiting、status、locked、score_components |
| ScheduleVersion | id、parent_id、scenario_id、reason、created_at、active、policy_version、route_snapshot_id |
| RiskEvent | id、idempotency_key、type、target、effective_time、payload、severity、status、run_id |
| StandbyCandidate | order_id、technician_id、earliest_start、skill_match、snapshot_version、expires_at |
| CandidatePlan | id、target_order_id、base_versions、scenario_generation、assignments/diff、affected_ids、policy_check、validation、score、metrics、status |
| Approval | plan_id、actor、decision、reason、expected_versions、timestamp |
| AgentRun | run_id、agent、step、input_refs、tool_calls、summary、duration、status、execution_mode |
| Notification | recipient_ref、type、message、delivery_mode=simulated、status |
| SimulationState | now、timezone、running、scenario_generation、seed |


字段使用 schema 校验；姓名和联系方式不参与评分。


### 6.2 状态分离


生命周期：DRAFT、NEEDS_INFO、OPEN、EN_ROUTE、ARRIVED、IN_PROGRESS、COMPLETED、CANCELLED。


调度状态：UNASSIGNED、PROPOSED、PENDING_REVIEW、ASSIGNED、UNRESOLVED。


ARRIVED 虽可能尚未维修，仍视为已出发、不可客户取消。是否按预约履约以 service_started_at 判断，提前到达等待要正确记录。


优先级独立，不用 P0 覆盖 IN_PROGRESS。预测时间与实际时间分别保存，不把预计完成当成实际完成。


### 6.3 时间


- API：带时区 ISO 8601；DB：UTC；界面：Asia/Singapore。
- Solver：同一调度日起点的整数分钟。
- 正常有效工单：window_start <= service_start <= window_end。
- 服务可以晚于 window_end 结束，但仍满足班次、休息、后继可达。
- 提前到达须等待 window_start。
- 风险截止点统一 window_end。
- 冲突区间用 [start,end)；开始恰等于 window_end 合法。


## 7. 技师与硬约束


技能来自真实目录，不限死为两个工种。模拟技师可多技能，各工种有独立等级。


必须校验：
1. 工种和所有必需技能最低等级。
2. 若配置必需资质/服务区域则必须满足；源目录没有的要求不能凭空生成。
3. 通勤、等待、维修不与任务、休息、不可用时段冲突。
4. 客户开始服务窗口。
5. 服务结束和后续路程满足班次/工时上限。
6. 同工单最多一个有效分配，同技师不重复占用。
7. 已出发、到达、执行中任务不被自动改派。
8. 不静默丢弃仍有效旧单。
9. 不安排到当前时间以前。
10. P1/P0 等级权限和数量限制。


固定休息、工时等是模拟企业配置，不宣称为当地法规。实际工时与未来计划分开统计，不能重复计入。


busy/en_route 技师不是全天排除：完成当前锁定服务后，可从完成地点和预计结束时间参与后续安排。


## 8. 优先级与风险


### 8.1 付费保底


普通 base=P3，付费 base=P1。risk_priority 根据活动风险计算，effective_priority 取更紧急者，P0 最高。


付费工单风险消失不降到 P2/P3，满足条件可以升 P0。


### 8.2 严重条件优先


| 条件 | 风险等级/动作 |
|---|---|
| 已超过 window_end 且尚未开始服务 | P0 |
| 技师取消，距 window_end <30 分钟 | P0 |
| 迟到投诉经核实已经超过截止且尚未开始服务 | P0 |
| 预计服务开始晚于 window_end | P1 |
| 技师取消，距 window_end 为 30–120 分钟，含边界 | P1 |
| 尚未预计迟到，距离 window_end <=30 分钟 | P2 |
| 技师取消，距 window_end >120 分钟 | P2 |
| 无更高风险 | P3 |
| 态度/质量等非排班投诉 | 转人工，不因此改变优先级 |


取消基础等级与其他风险合并取更严重者。不能以“取消得较早”忽略已明确预计迟到。


未分配工单 ETA 来自候选/路线估算，不能默认准时。未知则显示 unknown/unresolved，不编造；仍按事件和剩余时间处理。


COMPLETED/CANCELLED 不进入待服务监测；IN_PROGRESS 保留历史迟到，但不重复触发“尚未开始”的重排。


风险解决后移除活动原因并重算，保留历史；不能只升不降或无限加分。


### 8.3 P2


有有效分配：保留并准备最多三位备用，不主动换人。


无有效分配：允许空闲资源零扰动补派，高分自动、低分人工；无方案则待恢复。


备用人选不是资源预订，不占住三个人。启用前重新校验，不足三位如实显示。


### 8.4 过期 P0 恢复


对已超过原截止且未开始服务的目标：
- 保留原窗口和违约记录。
- 只对当前目标允许未来 recovery_start/ETA，不假装满足历史截止。
- 固定服务时长不变。
- 被移动的其他工单仍满足各自原窗口。
- 不要求先等客户确认才计算/执行政策允许的恢复；通知新 ETA，客户不接受再走改约或合法取消。
- 目标已出发/到达时仍不可自动改派，可保留当前执行、更新 ETA、告警。
- 无资源就转人工，不无限放宽其余约束。


## 9. 排班与共享模拟器


提供初始批量排班、P3 普通插单、P2 补派、P1/P0 修复、取消后资源再利用。


初排建议先高优先级，再按截止、窗口紧迫性处理，稳定 ID 打破平局。未提交批量候选可整体优化；一旦提交就形成已有承诺。


先实现共享 ScheduleSimulator 和 ConstraintValidator，再写搜索。初排、重排、基线、审批、取消后重算使用同一语义。


~~~text
arrival(j) = departure(j) + travel(origin(j), destination(j))
service_start(j) = max(arrival(j), window_start(j))
service_end(j) = service_start(j) + catalog_duration(j)
~~~


过期恢复目标仅替换其开始窗口约束。


插入时同时检查前驱→新任务、新任务→后继，沿路线传播校验到班次结束。使用真实可用锚点，不对每个未来任务都从当前地点出发。


通勤、等待、服务都是占用，不能在休息时偷偷通勤。允许等待以保持旧任务原开始时间，不能因最早到达改变就强行更改所有旧单。


锁定保留目标、技师、实际事实。单个当前任务锁定不等于全天冻结。首版不额外增加旧 Prompt 建议但未确认的“出发前15分钟冻结”。


## 10. 受限重排与影响数量


### 10.1 定义


affected_order_ids = 除本次 target_order_id 外，因调整改变技师或计划服务开始时间的已有有效工单，按 ID 去重。


- 同单换人且改时间计 1。
- 主动调整一单连带两单改时间计 3。
- 至少 1 分钟的开始时间变化计入。
- 只有路程/通勤变化而人和开始时间不变不计数，但报告差异并校验。
- 新目标和既有故障恢复目标均排除自身。
- 客户已取消任务是事实移除，不再作为有效待服务订单。


检查全部连带变化：P1 只能未出发 P3、最多2张；P0 只能未出发 P2/P3、最多5张。


不能把多个旧单全部命名 target 来躲开计数，也不能拆分自动提交规避同一调整上限。多单技师取消先更新全部事实，再按明确目标顺序处理；相互依赖或可能规避上限的组合恢复转人工。


### 10.2 搜索


建议可行插入+有界局部搜索：
1. 先尝试零扰动。
2. 必要时只对允许移动的未来任务做 relocate、swap、重新插入。
3. 移走任务必须有新去向。
4. 每次动作验证完整受影响路线和影响集合。
5. 预算内保留可行 incumbent，不宣称全局最优。
6. 返回 feasible/partial/no_solution_found/timeout/error，区分原因。


数量和等级是硬约束，不是超了扣分。无需强制计算超限方案；已发现超限结果可解释但不能进入普通批准列表。


### 10.3 多方案


最多三个不同安排：
- Faster response：目标更早开始。
- Less disruption：少改工单和时间。
- Balanced：响应、扰动、通勤、负荷综合。


同一约束、同一输入快照。根据分配与时间序列去重，相同日程不能换标题冒充三项。


重排不得丢失其他有效工单；初排可部分安排，但必须列出未分配清单，不能称全部完成。


## 11. 匹配分与政策


### 11.1 评分


先 feasible，再评分。分数是偏好，不是成功概率。


70 是已确认阈值；下面权重为可配置工程默认值：


| 维度 | 初始权重 | 依据 |
|---|---:|---|
| 技能适配 | 30% | 合格后比较实际技能等级 |
| 入站通勤 | 25% | 通勤分钟 |
| 响应效率 | 20% | 相对可开始时刻的等待 |
| 工作负荷 | 10% | 实际累计加未来计划占用 |
| 稳定性 | 15% | 影响数量和旧单开始偏移 |


每项归一化 [0,1]，加权转 0–100。实现明确可编码公式、clamp、标尺并写 docs/scoring.md，不能只写“综合评分”。


响应参考 max(now,window_start)，避免把未来预约因距现在远而判低分；过期目标参考 now。负荷不重复计实际与计划。


优先级用于队列与权限，不给同工单所有技师统一加一个无区分度的紧急分。显示分项和真实量，不写已验证的成功概率。


### 11.2 最低分


decision_score = 所有新增或改变有效分配的 match_score 最小值。


不能只看目标分数或用均分掩盖旧单差安排。对本次完全未改变的旧分配，不因其历史低分额外阻止本次操作。无变化结果返回 no_action，不编造新评分。


### 11.3 PolicyEngine


~~~text
输入缺失 -> needs_info
硬约束/权限失败 -> 不可提交
未找到可行方案 -> unresolved / no_solution_found
P3 -> 可行且 decision_score>70 自动，否则低分人工
P2 有有效安排 -> 保留+备用
P2 无有效安排 -> 零扰动且 >70 自动，否则人工/待恢复
P1 -> affected<=2、全部未出发P3、>70 自动，否则低分人工
P0 -> affected=0 且 >70 自动
      affected=0 且 <=70 人工
      1<=affected<=5 且全部未出发P2/P3 -> 必须人工
超权限 -> 告警，独立人工处理，不进入普通批准
~~~


超时但有验证通过 incumbent 时首版转人工、注明搜索未完成；无候选不能返回假方案。


比较未四舍五入原始分数，UI 处理临界值显示，避免显示70.0却无法理解为何自动。


## 12. 客户取消事件


### 12.1 权限


未出发可取消，工单作废；EN_ROUTE、ARRIVED、IN_PROGRESS 不可取消。已完成不能再走待服务取消。重复取消幂等返回原状态。


规则与优先级无关。保留记录，不物理删除。模拟付费订单取消记录状态，不实现真实退款。


### 12.2 原子流程


1. 读取最新状态、版本、时钟，核对客户身份。
2. 确认未出发。
3. 标记 CANCELLED，清除有效分配。
4. 释放未发生的通勤和维修占用。
5. 关闭风险、备用和未执行调度动作。
6. 使包含该单的未提交候选/审批失效。
7. 更新日程和版本，写审计及模拟通知。


旧审批不能复活取消订单。派单与取消并发由后端最新状态串行裁决。


### 12.3 出发竞争


出发先生效则取消拒绝；取消先成功则出发拒绝。不能两者都成功。模拟时钟已推进出发节点时，以真实状态为准，不依据前端缓存。


### 12.4 释放后路线


移除中间任务后，从真实前驱/当前位置直接去后继，不能继续用已取消地点作起点。


优先保持其他技师与开始时间，重算通勤、等待并校验。若后继变得不可达，显示风险并进入对应恢复，不静默放宽约束。


可以启动待分配订单零扰动调度来利用空档；改变其他有效工单须走目标优先级的正常重排。客户取消不赋予全局自由重排权限。


## 13. 技师取消、投诉与风险事件


### 13.1 技师取消


技师临时不可用时客户需求仍存在，不能把客户工单标为 CANCELLED。


事件含 technician_id、unavailable_start/end、reason。资源事实立即更新，找出受影响未开始任务，使无效分配失效、订单待服务。


逐单相对 window_end 判断：<30分钟 P0；30–120分钟 P1；>120分钟 P2，再叠加其他风险和付费保底。不要把全天工单统一设成同一级，不能等审批才承认技师不可用。


事件涉及 EN_ROUTE/ARRIVED/IN_PROGRESS 时，首版视为显式执行中断异常转人工，不自动解除锁定、伪造完成或半路改道。UI 明确边界。


### 13.2 投诉


客户或后台提交 complaint_type、文本和 order_id。


- 迟到投诉：根据时钟、窗口和 service_started_at 核实；确实超过截止且未开始时由该条件升 P0。
- 尚未迟到：按 ETA、临近截止等一般风险处理，不因情绪强烈强制 P0。
- 态度/质量：转人工服务队列，不进入 Solver，不因此提升等级。
- 已完成订单的历史投诉保留记录，不重新派单。


### 13.3 扫描和幂等


真实时间每分钟扫描，新事件立即处理。活动风险有稳定去重键；重复扫描更新 last_seen，不反复创建相同审批或通知。


风险等级、事实或可用资源有意义变化时才重算。事件有 received/processing/resolved/unresolved 状态。每轮工作流有终点和有限重试，不无限循环。


不要因写日志、更新 last_seen、保存备用列表就增加用于排班冲突检查的业务版本，造成候选永远过期；区分业务状态版本与观察记录版本。


## 14. Agent 架构与工具边界


三个逻辑 Agent + 确定性 Orchestrator，可在同一后端进程，不要求三个不同模型或服务。


| 模块 | 输入 | 工作 | 输出 |
|---|---|---|---|
| UnderstandingAgent | 描述、会话、目录 | 检索、追问、抽取、查表、校验 | 标准工单/待补信息 |
| SchedulingAgent | 工单、资源快照、优先级 | 路线、Solver、校验、解释 | 候选与结构化理由 |
| RiskMonitoringAgent | 时钟、状态、事件 | 规则扫描、投诉分类、风险更新 | 事件与后续动作 |
| Orchestrator | 事件、各模块输出 | 控制阶段、版本、审批、提交、失败 | 可追踪闭环 |
| PolicyEngine | 事实、分数、等级 | 判断自动/人工/禁止 | 政策结论和原因 |


建议工具：
- lookup_catalog、resolve_location、validate_order。
- load_snapshot、get_travel_matrix、find_standby_candidates。
- solve_initial、solve_insert、solve_repair、validate_plan、score_plan。
- classify_complaint、evaluate_risk。
- submit_candidate、commit_plan、cancel_order。


LLM 负责语义理解、投诉分类与解释，不直接控制技能要求、固定时长、分数、执行事实和批准结论。


schema 限制输出，保留目录 ID、订单 ID、工具事实引用。客户描述是数据，不能通过“忽略规则”等文本修改配置或绕过限制。


真实/mock 模式：
- mock 规则和目录匹配，返回同一 schema。
- real 使用可配置 provider，超时、有限重试、结构化校验。
- 失败可降级为规则和模板解释，明确标注。
- 解释数值来自 Solver/metrics，不由模型编造。


轨迹展示步骤、工具、事实摘要、耗时与错误；不展示或伪造隐藏思维链，不用三个静态聊天框冒充协作。


工作流：
received → normalize/load_snapshot → classify_priority → standby/solve → validate → score → policy → pending_review/commit/unresolved → completed。


审批等待持久化，不占住无限等待的 LLM 请求。


## 15. 候选、审批与原子提交


### 15.1 候选不生效


生成候选不修改生效日程，preview 独立。只有 commit_plan 写生效排班，自动和人工共用此入口。


候选绑定 base_schedule_version、base_data_version、scenario_generation、route_snapshot_id、policy_version、generated_at、expires_at、target_order_id、完整 affected_order_ids。


有效期可设5分钟模拟时间，属于工程默认值；事实变化可提前失效。


### 15.2 提交再校验


事务内检查：
1. 场景、版本、有效期。
2. 目标未取消/完成且仍可分配。
3. 旧单仍允许移动。
4. 技师未出发、未变不可用。
5. 当前时间下路线和窗口可行。
6. 影响数量、权限、最低分、审批条件一致。


失效返回409及结构化原因，提供重算；不能套用旧事实。


### 15.3 幂等和一致性


approve、auto_commit、cancel、事件支持 idempotency_key。重复调用返回原结果，不重复分配/通知。


同场景提交串行化，完整调整一次性生效，不先移走旧单再等待剩余步骤。失败回滚该方案，但不能复活已经独立生效的客户取消或技师不可用事实。


初排含低分时，首版整批人工；若部分提交则重新校验子集，不删除前驱后直接沿用旧通勤。


历史版本用于前后比较，不要求任意跨执行状态回滚。


## 16. 路线与地图


RouteProvider 和地图组件分离，ETA 来自路线计算，不从线长猜测。


必须有：
1. FixtureRouteProvider：固定地点和时长矩阵，保证离线测试与演示。
2. 一个真实路线适配，例如可配置 OSRM Table/Route；实施时查阅当前官方文档，记录实际验证状态。
3. 可选 estimated provider，明确估算，不称实时交通。


统一经纬度顺序和秒/分钟，不可达/缺失值不能变成零，不假设矩阵对称。单次比较绑定同一路线快照。缓存包含地点及顺序、provider、必要配置。


失败时明确整轮切换并重算，不把失败路段填零而仍声称真实路线。取消节点后重算前驱到后继。


路线几何用于展示；fixture 可示意线并标 schematic。路网估时不得冒充实时路况。


地图展示技师、客户、选中路线、目标与影响任务。瓦片失败时用本地 SVG 示意图，仍可选中和查看任务。地址无法解析时选预设地点/地图点，不用 LLM 造坐标。


## 17. 仿真时间与执行状态


统一 ClockProvider，排班、风险、取消、过期和 UI 使用同一个 now。


Demo 默认暂停，提供 +1/+5/+15分钟、运行/暂停、场景重置，显示日期和时区。


时间推进按顺序处理出发、到达、开始、完成与扫描，不能跨过出发节点却仍允许取消。


执行序列：
ASSIGNED → EN_ROUTE → ARRIVED → IN_PROGRESS → COMPLETED。


仿真可自动推进；真实模式依实际上报，预测完成不代表实际完成。超时可告警，但不能伪造事实。


计划与实际出发要一致，定时动作幂等，重启不能重复出发/完成。重置增加 scenario_generation，旧后台任务和回调不得写入新场景。


## 18. 前端功能与交互


桌面优先、默认英文界面、支持中英文客户输入。文案集中管理，首版无需完整国际化。


### 18.1 客户 Chatbot


对话、必要追问、目录识别、地点/预约选择、模拟付费、确认卡、提交、当前状态与 ETA、投诉、取消。


未出发显示取消入口；已出发显示原因。模拟模式明确标注。不同会话数据隔离，模拟身份方案写文档。


### 18.2 Dispatch Dashboard


顶部显示时间、场景、模式、模拟控制、Generate Schedule、Inject Event、Reset。


主区：
- 技师时间轴，区分 travel/wait/service/break。
- 锁定标识。
- 工单地图和路线。
- 工单列表与优先级/状态筛选。
- 无效分配和待恢复状态。
- KPI：已安排、未安排、风险、总通勤、紧急响应、影响数量。
- Agent 活动、通知。


工单详情显示描述、目录条目、固定参数、原窗口、恢复时间、等级原因、负责人和执行状态。


### 18.3 P2 面板


风险原因、原安排是否有效、最多3名备用、最早可开始时间、更新时间。“备用，不占用资源”清楚可见。


缺少有效安排时显示补派或待恢复；不能继续显示正常已安排。


### 18.4 方案与审批


最多三张卡+对比表：
- 策略、目标技师、开始/恢复 ETA。
- 分数、分项、最低改变分配分。
- 影响数、权限校验。
- 旧单原/新技师与开始时间、偏移。
- 是否满足预约。
- 通勤/负荷变化。
- 审批理由。


展开显示完整差分。预览不生效。Approve/Reject/Recompute 真实调用；过期显示原因；超权限无普通批准按钮。


### 18.5 事件操作


paid_expedite_order、technician_unavailable、lateness_complaint、non_scheduling_complaint、customer_cancel。


提供结构化目标和事件时间。客户取消可从客户侧触发并同步后台。未出发技师取消作为常规 Demo；执行中意外为人工异常边界。


### 18.6 页面完成度


关键操作有 loading/empty/error/success，刷新恢复生效日程和待审批。禁止 setTimeout 假成功、写死 KPI、只修改颜色假装后端运转。


## 19. API 合同


可调整名称，但前后端和文档同步。核心建议：


| 方法与路径 | 用途 |
|---|---|
| GET /health | 健康与模式，不返回密钥 |
| GET /api/catalog | 目录与版本、加载状态 |
| POST /api/catalog/reload | 按配置路径导入校验 |
| POST /api/chat/messages | 对话与工具流程 |
| POST /api/orders/interpret | 结构化识别 |
| GET/POST /api/orders | 列表、正式创建 |
| GET/PATCH /api/orders/{id} | 详情、受限编辑 |
| POST /api/orders/{id}/cancel | 客户取消 |
| GET /api/technicians | 资源与锚点 |
| POST /api/technicians/{id}/unavailability | 技师不可用 |
| POST /api/orders/{id}/execution-events | 出发、到达、开始、完成 |
| GET /api/schedules/current | 生效排班 |
| GET /api/schedules/{version} | 历史版本 |
| POST /api/scheduling/initial | 初排，返回run_id |
| POST /api/scheduling/dispatch | 派单/补派/重排 |
| POST /api/events | 异常事件 |
| GET /api/risks | 活动风险 |
| GET /api/orders/{id}/standby | 备用人选 |
| GET /api/runs/{id} | 进度、结果、候选、错误 |
| GET /api/plans/{id} | 候选详情 |
| POST /api/plans/{id}/approve | 校验与提交 |
| POST /api/plans/{id}/reject | 拒绝 |
| POST /api/plans/{id}/recompute | 重算 |
| GET /api/agent-runs | 执行日志 |
| POST /api/demo/reset | 场景重置 |
| POST /api/demo/clock/advance | 推进时间 |
| POST /api/demo/clock/control | 运行/暂停 |
| POST /api/evaluations | 启动评估 |
| GET /api/evaluations/{id} | 结果与导出 |


事件采用判别联合 schema。客户端不能覆盖目录复杂度、时长、有效优先级、分数或审批结论。


错误统一 code/message/details/request_id。409状态/版本冲突，422字段验证。OpenAPI 和 docs/data-contracts.md 提供请求、响应、错误示例，前端不能解析自然语言来判断逻辑。


## 20. 工程默认技术方案与配置


已有可用技术栈优先复用；空仓库建议：


| 层 | 默认 |
|---|---|
| 前端 | React + TypeScript + Vite |
| UI | Tailwind CSS + 轻量组件库 |
| 后端 | Python + FastAPI + Pydantic |
| 数据 | SQLite + SQLAlchemy，初始化/迁移 |
| CSV | csv/pandas，显式schema |
| Agent | 独立模块+显式工作流，可选LangGraph |
| Solver | 可行插入+局部搜索，OR-Tools可后续适配 |
| 路线 | fixture+真实provider |
| 更新 | 轮询，必要时SSE |
| 测试 | pytest、类型检查/build、浏览器 |
| 启动 | 本地脚本+Docker Compose |


版本在实际环境验证并提交 lockfile，不声称未经查验的最新版本。选择 OR-Tools 也须自己实现技能、权限、数量、锁定、审批等，不能把示例当完整系统。


单后端进程避免重复扫描；多进程前解决任务互斥。密钥仅后端环境，不入前端与仓库。


~~~dotenv
APP_MODE=demo
APP_TIMEZONE=Asia/Singapore
REPAIR_CATALOG_PATH=data/reference/repair_object_problem_database.csv
DATABASE_URL=sqlite:///./data/app.db
LLM_MODE=mock
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
ROUTE_MODE=fixture
OSRM_BASE_URL=
~~~


建议政策配置：


~~~yaml
dispatch:
  auto_score_threshold: 70
  threshold_operator: ">"
  decision_score: minimum_new_or_changed_assignment
priority:
  normal_base: P3
  paid_base: P1
  paid_can_escalate_to_p0: true
reschedule:
  P3:
    max_affected: 0
    movable_priorities: []
  P2:
    max_affected: 0
    movable_priorities: []
    allow_zero_disruption_repair_if_unassigned: true
  P1:
    max_affected: 2
    movable_priorities: [P3]
    require_review_if_within_limit: false
  P0:
    max_affected: 5
    movable_priorities: [P2, P3]
    require_review_if_affected: true
execution:
  locked_statuses: [EN_ROUTE, ARRIVED, IN_PROGRESS]
customer_cancel:
  allowed_before_departure_only: true
risk:
  deadline_field: window_end
  scan_interval_seconds: 60
  cancellation_p0_remaining_minutes_less_than: 30
  cancellation_p1_remaining_minutes_inclusive: [30, 120]
  cancellation_p2_remaining_minutes_greater_than: 120
standby:
  max_candidates: 3
~~~


配置结构为实施建议，不能把某个布尔字段解释成绕过低分审批或硬约束。PolicyEngine 统一解释。


## 21. 仓库组织


~~~text
backend/app/api/
backend/app/schemas/
backend/app/models/
backend/app/agents/
backend/app/orchestration/
backend/app/scheduling/
backend/app/services/
backend/app/providers/
backend/app/db/
backend/tests/
frontend/src/pages/
frontend/src/components/
frontend/src/api/
frontend/src/types/
frontend/src/stores/
config/policy.yaml
data/reference/repair_object_problem_database.csv
data/fixtures/
data/scenarios/
scripts/
docs/project-brief.md
docs/architecture.md
docs/data-contracts.md
docs/scoring.md
docs/decisions.md
docs/demo.md
docs/evaluation.md
docs/progress.md
README.md
.env.example
compose.yaml
~~~


保持业务、Agent、Solver、接口、UI 分离，避免单文件包含全部逻辑。


seed 根据真实目录创建合成工单/技师，不改原 CSV。测试 fixture 与用户数据源区分，UI 标注模式。


## 22. 数据与演示场景


工程默认：8位技师、约20张基础工单，覆盖真实目录多个工种。固定 seed、新加坡公开区域/地点、合成客户和联系方式。


命名场景需验证预期条件，不只随机生成不可行数据。场景时间从真实目录时长构造，不能为演示随意覆盖固定服务时长。


### 22.1 主线


S1 基础排班：多单、空档、未来未出发任务、已出发锁定任务。可生成初排，也可加载已验证版本。


S2 普通P3：Chatbot识别查表、提交、插入空档，其他负责人和开始时间不变。


S3 付费P1：显式模拟付款、base=P1。构造移动1–2张未出发P3且均准时的方案，高分自动执行；另验证P2不能被P1移动。


S4 P0恢复：临近预约技师取消或已迟到触发，不靠付款生成P0。影响1–5张未出发P2/P3，完整对比、人工批准、统一提交。补充零影响高分P0自动案例。


### 22.2 短片段


S5 P2：
- 有效安排临近截止，准备最多3人、不换人。
- 原技师提前取消、距截止>120分钟，无分配时零扰动补派。
- 无资源显示待恢复。


S6 客户取消：
- 未出发取消作废、释放资源。
- 新订单合法使用空档。
- 已出发取消拒绝。
- 待审批订单取消后旧审批不能复活。


### 22.3 边界


- P1要移动3张P3：禁止普通执行。
- P1要移动P2：禁止。
- P0要移动已有P1/P0：禁止。
- P0超过5张：告警。
- 技能/资质不够：无可提交结果。
- 可行但<=70：人工。
- 仅1/2候选：不补假项。
- 已过期P0：保留原违约、恢复ETA。
- 非排班投诉：排班不变、人工服务。
- 审批期间出发或取消：冲突。
- LLM/路线失败：明确降级。
- reset后旧异步结果不能写回。


编写约3分钟主线和可独立展示的补充场景，不要求一次演示全部边界。


## 23. 评估与创新表达


项目亮点：目录驱动理解、分级权限、预警准备、低扰动恢复、人机协作。不宣称已证明学术新颖性或未测量的节省比例。


至少两个策略：
1. Nearest Feasible Baseline：同一目标顺序下选择最近且满足完整路线约束的可行插入，不跨任务修复。
2. Proposed：同约束下，综合评分并按P1/P0权限进行有界修复。


基线也遵守技能、后继可达、锁定、预约。说明 proposed 动作空间更多，是系统能力比较，不只比较权重。


动态重排从相同已提交基础计划分叉，使用同一事件、时钟、目录、矩阵、资源事实。另做初排端到端比较时单独标明。


指标：
- 分配率和未分配数，按等级分组。
- 预计按原窗口开始的比例：取消订单从有效需求分母排除，未分配计未达成。
- P0恢复等待、相对原窗口的迟到，不能恢复有解就记原预约准时。
- 紧急事件至开始服务的响应时间，未服务不能记0。
- 总/新增通勤。
- 影响数、换人数、旧开始偏移。
- 工作负荷差异，明确口径。
- 硬约束违规、权限违规、取消后复活次数，已提交方案均应0。
- 求解耗时，区分API/LLM。
- 自动与人工次数。


用命名场景和至少10个固定seed，保存逐场景JSON/CSV和汇总。失败不从分母悄悄排除。方案存在响应与稳定性取舍，不强求所有指标都提升。


初排3秒、重排5秒可作为本地预算目标，不是已验证性能。超时和可行 incumbent 如实处理。


## 24. 必须验证的行为


### 24.1 目录和理解


- 真实表头映射、BOM、引号、重复与冲突行。
- 相同目录ID参数固定。
- 不同措辞映射同问题；非法目录ID拒绝。
- LLM不能覆盖时长、复杂度、付费、有效等级。
- 缺文件报错；fixture不能冒充正式目录。


### 24.2 时间和路线


- 技能不足不可派，高分不绕过。
- 前驱可达后继不可达拒绝。
- 通勤、等待、休息、服务区间正确。
- start=window_end合法；服务可在窗口结束后完成。
- 班次和不可用硬限制。
- busy完成后可排后续。
- 不可达不是0分钟。
- 取消后重算真实前驱到后继。


### 24.3 风险和权限


- P3、付费P1保底、付费升P0。
- 30/120分钟边界与window_end。
- 预计迟到优先于仅临近。
- P2有效不换人，无效零扰动补派。
- P1最多2张未出发P3；P2或3张必须失败。
- P0最多5张未出发P2/P3，已有P1/P0不能动。
- 所有连带变化计数，目标排除。
- 目标升P0不被“不能移动其他P0”误伤，但出发仍锁。
- 不能拆分提交绕过上限。
- 过期恢复例外仅目标使用。


### 24.4 分数和执行


- 70不自动，>70才可能自动。
- 使用新增/改变分配最低分。
- P0零影响低分人工，1–5影响高分也人工。
- P1权限内高分可自动。
- 不足三项不造假。
- 超时不声称数学无解。
- 不丢失/重复任务。


### 24.5 取消、事务、事件


- 客户未出发可取消；已出发/到达/维修不可。
- 取消关闭风险/备用，使审批失效。
- 旧审批不能复活。
- 取消与出发竞争单一有效结果。
- 重复取消、批准、扫描幂等。
- 技师取消保留客户需求，资源事实立即更新。
- 最新状态/时钟影响旧方案提交。
- reset后旧任务不能写回。


### 24.6 端到端


至少一条浏览器主流程：
目录/场景 → Chatbot → P3派单 → P1调整 → P0审批 → 日程更新 → 取消另一张未出发订单 → 新订单使用空档。


刷新后状态保持，UI和API/DB一致。执行后端测试、类型检查/build、浏览器主流程；未执行项明确说明，不伪造通过。测试集中在业务风险，不写大量仅镜像实现的冗余测试。


## 25. 分阶段实现


| 阶段 | 交付 | 验证 |
|---|---|---|
| 1 数据骨架 | 导入、schema、DB、时钟、基础列表 | 真实目录加载持久化 |
| 2 正常调度 | simulator、validator、初排、P3、评分提交 | 真派单、无冲突、低分分流 |
| 3 风险取消 | 分级、P2、两类取消、锁定 | 等级与取消边界正确 |
| 4 受限重排 | P1/P0、影响、候选、审批版本 | 2/5上限不突破 |
| 5 Agent交互 | Chatbot、工具、解释、地图时间轴 | 客户到后台闭环 |
| 6 验证交付 | 场景、测试、评估、启动部署 | 完整可复现Demo |


阶段可穿插UI，但先完成调度逻辑，不先投入大量聊天动画。不要被RAG或生产基础设施拖慢。


持续更新 progress。上下文切换时读取当前代码、progress、decisions继续，不从零重写。


## 26. 最终交付与运行


必须交付：
1. 前后端代码和锁定依赖。
2. CSV导入、seed、reset。
3. DB初始化/迁移和持久化。
4. 本地启动、Docker Compose。
5. .env.example及模式切换。
6. architecture：组件图、Agent边界、正常/异常/取消流程。
7. data-contracts：字段、枚举、API、时间语义、状态竞争与版本。
8. scoring：明确公式、权重、阈值与解释。
9. decisions：已确认规则和工程默认选择分开。
10. demo：主线点击步骤和补充场景。
11. evaluation：实测、原始结果、公平性和局限。
12. 实际测试输出、未验证依赖、已知问题。


mock LLM+fixture路线即可运行；用户将真实目录放入指定路径。重启保留DB，reset为独立显式动作。


默认交付本地运行和部署配置，不擅自公网发布、开通付费服务、真实支付或发送真实消息。


最终汇报实现内容、启动方法、CSV路径、重置演示、实测通过项和待配置接口；预留功能不能写成完成。


## 27. 开始执行


现在执行：
1. 检查仓库约束与已有代码。
2. 读取配置路径的真实CSV。
3. 记录表头映射、默认选择和实施清单。
4. 逐阶段编写、运行、验证。
5. 持续推进到完整可运行系统。


不必重新确认本文已确定业务规则。普通工程细节自行选择并记录；关键输入缺失先完成其他可做工作，再具体指出缺什么，不猜测输入、不伪造验证。