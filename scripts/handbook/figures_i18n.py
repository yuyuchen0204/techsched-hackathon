"""English labels for the handbook figures.

The English edition renders the same figures from the same data; only the labels change.
`EXACT` maps a complete Chinese label to its English text; `PATTERNS` holds whole-string
regular expressions for labels that carry generated numbers.
"""
from __future__ import annotations

PATTERNS: list[tuple[str, str]] = [
    (r'复杂度 (\d)', r'Complexity \1'),
    (r'(\d+) 分钟', r'\1 min'),
]

EXACT: dict[str, str] = {
    # ---------------------------------------------------------------- catalog
    '按工种的问题条目数': 'Catalogue entries by trade',
    '共 46 条，10 个工种': '46 entries across 10 trades',
    '按复杂度的条目数与固定维修时长': 'Entries by complexity, with the fixed repair duration',
    '复杂度与时长在问题库中一一对应，时长不由模型估算':
        'Complexity maps 1:1 to duration; duration is never model-estimated',
    '技师技能等级必须 ≥ 问题复杂度，否则该技师不是候选。':
        'A technician qualifies only when their skill level is at least the problem complexity.',
    '数据来源：data/reference/repair_object_problem_database.csv':
        'Source: data/reference/repair_object_problem_database.csv',

    # ---------------------------------------------------------------- skills matrix
    '场景 main 的技师技能矩阵（8 名技师 × 10 个工种）':
        'Technician skill matrix, scenario main (8 technicians × 10 trades)',
    '格内数字为技能等级 1–5。空格表示该技师不持有该工种；技能等级低于问题复杂度时不进入候选':
        'Cell values are skill levels 1–5. A blank cell means the technician does not hold that trade; '
        'a level below the problem complexity is never a candidate',
    '可服务技师数': 'Technicians available',
    '标红的工种在本场景中只有一名技师可服务。该技师不可用时，相关工单不存在零打扰的恢复方案，进入恢复流程或人工队列。':
        'Trades marked in red have exactly one qualified technician in this scenario. When that technician becomes '
        'unavailable, those orders have no zero-disturbance recovery and enter the recovery flow or the human queue.',
    '技能等级': 'Skill level',
    '数据来源：data/scenarios/main.json': 'Source: data/scenarios/main.json',
    '空调': 'Aircon', '水暖': 'Plumbing', '冰箱': 'Fridge', '洗衣机': 'Washer', '热水器': 'Water htr',
    '电气照明': 'Electrical', '门锁五金': 'Locks', '燃气灶': 'Gas stove', '家具木工': 'Furniture',
    '网络智能': 'Network',

    # ---------------------------------------------------------------- scoring weights
    'match_score 的五个分量与权重': 'The five components of match_score and their weights',
    'decision_score = 方案中所有新增或变更分配的 match_score 最小值；> 70 才可自动执行':
        'decision_score = the minimum match_score over all new or changed assignments in the plan; '
        'strictly above 70 to auto-execute',
    '技能匹配 skill_fit': 'Skill fit', '通勤 travel': 'Travel', '响应 response': 'Response',
    '稳定性 stability': 'Stability', '工作量 workload': 'Workload',
    '等级低于复杂度即 0 分': 'scores 0 when the level is below the complexity',
    '满分惩罚尺度 60 分钟': 'full-penalty scale 60 min',
    '满分惩罚尺度 120 分钟': 'full-penalty scale 120 min',
    '受影响参考数 2，换技师额外 0.5 惩罚': 'affected ref. 2; +0.5 on a technician change',
    '班次尺度 60 分钟': 'shift scale 60 min',
    '合计': 'Total',
    '数据来源：config/policy.yaml → scoring（工程默认值，可调整）':
        'Source: config/policy.yaml → scoring (engineering defaults, adjustable)',

    # ---------------------------------------------------------------- V2 evaluation
    'V2 对照实验：同一初始排班、同一事件序列下的两种调度策略':
        'V2 controlled experiment: two dispatch strategies over the same initial schedule and event sequence',
    '10 个随机种子 × 每种子 6.1 个事件 = 61 个事件；路线 fixture；策略版本 2026-09-14-v2':
        '10 random seeds × 6.1 events per seed = 61 events; route provider fixture; policy version 2026-09-14-v2',
    '基线：最近可行插入': 'Baseline: nearest feasible insertion',
    '本系统：打分 + 权限内有界重排': 'This system: scored, with authority-bounded repair',
    '可行排班率': 'Assignment rate',
    '未分配的事件计为未达成': 'unassigned events count as missed',
    '紧急事件未服务数': 'Urgent events left unserved',
    '共 10 个 P0/P1 事件': 'out of 10 P0/P1 events',
    '受影响的既有工单数': 'Existing orders affected',
    '被移动或改派的其他客户工单': 'other customers’ orders moved',
    '新增通勤总分钟': 'Added travel, total minutes',
    '全部技师相对基准排班的通勤增量': 'vs. the baseline schedule',
    '两种策略在已提交方案中的硬约束违规与越权违规均为 0 起；决策分布：基线 assign 35 · unresolved 26，本系统 auto 28 · manual 11 · unresolved 22。':
        'Both strategies committed 0 plans with a hard-constraint or authority violation. Decision mix: baseline '
        'assign 35 · unresolved 26; this system auto 28 · manual 11 · unresolved 22.',
    ' 数据来源：data/evaluation/latest.json': ' Source: data/evaluation/latest.json',

    # ---------------------------------------------------------------- V3 evaluation
    'V3 对照实验：快路径与 Agent 编排（每个场景 10 个随机种子的均值）':
        'V3 controlled experiment: fast path vs. agent orchestration (mean of 10 seeds per scenario)',
    '同样的世界、事件、策略、求解器与预算；唯一差别是工单进入 UNRESOLVED 之后如何编排':
        'Same worlds, events, policy, solver and budgets; the only difference is what happens after UNRESOLVED',
    '快路径（V2 流水线）': 'Fast path (V2 pipeline)',
    '快路径 + Agent 有界调查': 'Fast path + bounded agent investigation',
    '含 Agent 协商出的备选时间窗': 'includes alternative windows negotiated by the agent',
    '原时间窗准时开始率': 'On-window start rate (original window)',
    '两种编排在此项上完全相同': 'identical under both orchestrations',
    '每个种子的均值': 'mean per seed',
    '场景规模：relaxed（12 单） · main（20 单） · scarce（32 单 · 2 人请假）':
        'Scenario size: relaxed (12 orders) · main (20 orders) · scarce (32 orders, 2 technicians on leave)',
    '两种编排在已提交方案中的硬约束违规与越权违规在全部场景中均为 0。"备选窗口被客户接受" 是一个假设，单独统计，不计入原时间窗准时率。':
        'Neither orchestration committed a plan with a hard-constraint or authority violation in any scenario. '
        '“Served in an alternative window” is an assumption, reported separately and not counted as on-window.',
    '数据来源：data/evaluation/latest_v3.json': 'Source: data/evaluation/latest_v3.json',

    # ---------------------------------------------------------------- agent cost
    'Agent 调查的成本与产出随资源稀缺度的变化':
        'Cost and output of agent investigation as resources get scarcer',
    '横轴按资源由宽松到稀缺排列。预算上限：每次唤醒 12 次工具调用 / 3 次方案搜索 / 2 次瞬时重试':
        'Scenarios ordered from ample to scarce. Budget ceiling per wake-up: 12 tool calls / 3 plan searches / '
        '2 transient retries',
    '每种子的工具调用次数': 'Tool calls per seed',
    '方案搜索是其中的子集': 'plan searches are a subset of these',
    '工具调用': 'Tool calls', '其中方案搜索': 'of which plan searches',
    '编排处理耗时（毫秒）': 'Orchestration processing time (ms)',
    '不含模型调用延迟': 'excludes model latency',
    '每种子的调查产出': 'Investigation outcomes per seed',
    '两类结局的次数': 'count of the two terminal outcomes',
    '备选窗口服务': 'Served in alt. window', '人工升级': 'Human escalations',
    'scarce 场景中多数调查以人工升级结束。在资源不足时这是设计预期的结局：预算耗尽不进入重试循环，而是以带证据的人工事项收尾。':
        'In the scarce scenario most investigations end in a human escalation. When resources are genuinely '
        'insufficient that is the intended outcome: an exhausted budget does not enter a retry loop, it produces '
        'an evidenced human case.',
    '数据来源：data/evaluation/latest_v3.json · config/policy.yaml → agent':
        'Source: data/evaluation/latest_v3.json · config/policy.yaml → agent',

    # ---------------------------------------------------------------- rest comparison
    '固定午休与动态休息的对照（同一批世界的初始排班）':
        'Fixed lunch break vs. dynamic rest (initial schedule over the same worlds)',
    '固定午休 = 全员 12:00–13:00 不可用；动态休息 = 累计工作 180–240 分钟之间寻找 ≥ 30 分钟的零打扰空档':
        'Fixed lunch = 12:00–13:00 unavailable for everyone; dynamic rest = search for a zero-disturbance gap of '
        '≥ 30 min between 180 and 240 cumulative work minutes',
    '固定午休': 'Fixed lunch', '动态休息': 'Dynamic rest',
    '初始未分配工单数': 'Orders unassigned at seed',
    '种子排班阶段无法安置的工单': 'orders that could not be placed in the seed schedule',
    '通勤总分钟': 'Total travel minutes',
    '全部技师当日通勤合计': 'all technicians, whole day',
    '休息升级为人工事项次数': 'Rest escalations to a human case',
    '240 分钟仍无休息即升级': 'escalated after 240 minutes without rest',
    '评测中的动态休息使用"空闲空档"代理指标；线上实现会额外重新校验路线可行性与后继时间窗，因此评测中的"找到休息位"是一个上界。':
        'The harness uses an idle-gap proxy for dynamic rest. The live implementation additionally re-validates '
        'route feasibility and successor windows, so “rest slot found” here is an upper bound.',

    # ---------------------------------------------------------------- tests
    '后端测试用例分布（共 128 个 pytest 用例，13 个测试文件）':
        'Backend test cases by file (128 pytest cases across 13 files)',
    'tests/conftest.py 强制 LLM_MODE=mock，后端测试完全离线且确定性':
        'tests/conftest.py forces LLM_MODE=mock, so backend tests run fully offline and deterministically',
    '数据来源：backend/tests/（pytest --collect-only）': 'Source: backend/tests/ (pytest --collect-only)',

    # ---------------------------------------------------------------- authority matrix
    'P0–P3 的重排权限矩阵': 'P0–P3 rescheduling authority matrix',
    '权限规定可移动的工单类型与数量上限；优先级不参与评分，对同一工单的所有候选技师为常量':
        'Authority defines which orders may move and how many; priority does not enter the score — it is constant '
        'across every candidate technician for the same order',
    '目标优先级': 'Target priority', '可移动的其他工单': 'Other orders it may move',
    '最多影响工单数': 'Max orders affected', '已出发任务': 'Departed tasks',
    '强制人工审批的条件': 'Conditions forcing dispatcher review',
    '一律不可移动': 'never movable', '无': 'none', '不限': 'unlimited',
    '决策分 ≤ 70': 'decision_score ≤ 70',
    '决策分 ≤ 70；已有有效分配时保持不动并准备备选技师':
        'decision_score ≤ 70; keeps its assignment and prepares standby technicians when one is valid',
    '影响 ≥ 1 张工单即须人工审批；决策分 ≤ 70；超限方案仅告警，不提供审批入口':
        'any plan affecting ≥ 1 order requires review; decision_score ≤ 70; over-limit plans are alerts only, '
        'with no approval control',
    '被移动的工单必须仍在各自的时间窗内。同一事件中若第一个目标已移动其他工单，第二个目标的任何再次移动方案强制进入人工审批，以防止拆分自动提交累加越权。':
        'A moved order must still start inside its own window. Within one event, once the first target has moved '
        'other orders, any further-moving plan for the second target is forced to review, so split auto-commits '
        'cannot add up beyond one target’s limit.',
    '数据来源：config/policy.yaml → reschedule / dispatch':
        'Source: config/policy.yaml → reschedule / dispatch',

    # ---------------------------------------------------------------- pipeline diagram
    '派单流水线的阶段与结局': 'Stages and outcomes of the dispatch pipeline',
    '阶段定义见 orchestration/orchestrator.py；每个阶段作为一条 AgentRun step 记录':
        'Stages are defined in orchestration/orchestrator.py; each is recorded as one AgentRun step',
    '加载快照': 'Load snapshot', '风险分级': 'Classify risk', '求解候选': 'Solve candidates',
    '独立校验': 'Validate independently', '计算受影响': 'Compute affected', '权限检查': 'Check authority',
    '打分': 'Score', '策略裁决': 'Policy decision',
    '自动提交': 'Auto-commit', 'decision_score > 70 且权限内': 'decision_score > 70 and within authority',
    '人工审批': 'Dispatcher review', '低分 / P0 影响他人 / 搜索未完成':
        'low score / P0 affecting others / search incomplete',
    '无解': 'No solution', '创建 Agent 任务继续调查': 'an agent task continues the investigation',
    'schedule_service.commit_plan 是实时排班表的唯一写入路径，每次提交生成一个新的 ScheduleVersion（含父版本、原因、模拟时间与完整分配快照）。':
        'schedule_service.commit_plan is the only writer of the live schedule; every commit creates a new '
        'ScheduleVersion carrying its parent version, reason, simulated time and a full assignment snapshot.',
    '来源：backend/app/orchestration/orchestrator.py · backend/app/scheduling/policy.py':
        'Source: backend/app/orchestration/orchestrator.py · backend/app/scheduling/policy.py',

    # ---------------------------------------------------------------- lifecycle diagram
    '工单生命周期与锁定边界': 'Work-order lifecycle and the locking boundary',
    '状态取自 backend/app/models/enums.py；ARRIVED 起计为"已出发"，不可被任何方案修改':
        'States come from backend/app/models/enums.py; ARRIVED onwards counts as departed and cannot be modified '
        'by any plan',
    '会话草稿': 'chat draft', '槽位未补全': 'slots incomplete', '已建单待执行': 'created, awaiting execution',
    '在途': 'en route', '已到达': 'arrived', '施工中': 'in progress', '已完成': 'completed',
    '仅限出发前': 'only before departure',
    '锁定区：technician / departure / service_start 不可被任何候选方案修改':
        'Locked region: technician / departure / service_start cannot be modified by any candidate plan',
    '出发后客户取消返回 409 already_departed；取消与出发同时发生时，先持久化者生效。':
        'A customer cancellation after departure returns 409 already_departed; when cancel and depart race, '
        'whichever persists first wins.',
    '调度状态独立于生命周期，取值为 UNASSIGNED · PROPOSED · PENDING_REVIEW · ASSIGNED · UNRESOLVED。':
        'Scheduling status is independent of the lifecycle: UNASSIGNED · PROPOSED · PENDING_REVIEW · '
        'ASSIGNED · UNRESOLVED.',
    '来源：backend/app/models/enums.py · backend/app/scheduling/validator.py':
        'Source: backend/app/models/enums.py · backend/app/scheduling/validator.py',

    # ---------------------------------------------------------------- agent loop diagram
    'Agent 任务循环与四类出口': 'The agent task loop and its four exits',
    '每次唤醒的预算：12 次工具调用 / 3 次方案搜索 / 2 次瞬时重试（config/policy.yaml → agent）':
        'Budget per wake-up: 12 tool calls / 3 plan searches / 2 transient retries (config/policy.yaml → agent)',
    '创建任务': 'Create task', 'create_task · 去重键': 'create_task · dedupe key',
    '策略决定下一步': 'Policy picks the next step',
    '调用工具': 'Call the tool', '角色门 + 技能白名单 + 参数校验':
        'role gate + skill allow-list + argument check',
    '记录轨迹': 'Record the trace', 'ToolTrace：为什么 · 做了什么 · 结果':
        'ToolTrace: why · what · outcome',
    '未终止则回到策略：预算未用尽时继续下一步':
        'Not terminal → back to the policy: continue while budget remains',
    '成功结束': 'Succeeded', 'succeeded：方案已提交或已确认无需处理':
        'succeeded: the plan was submitted, or no action was needed',
    '等待': 'Waiting', 'waiting_customer / waiting_human / waiting_agent：任务挂起，被显式唤醒':
        'waiting_customer / waiting_human / waiting_agent: the task suspends until explicitly woken',
    'no_solution：自动转为带证据的人工事项':
        'no_solution: converted automatically into an evidenced human case',
    '预算耗尽': 'Budget exhausted', 'budget_exhausted：同样自动转人工，不进入重试循环':
        'budget_exhausted: likewise escalated to a human; never a retry loop',
    '来源：backend/app/agents/runtime.py · backend/app/agents/policies.py · config/agent_skills/*.md':
        'Source: backend/app/agents/runtime.py · backend/app/agents/policies.py · config/agent_skills/*.md',

    # ---------------------------------------------------------------- architecture diagram
    '系统技术架构（模块清单由代码目录生成）':
        'System architecture (module inventory generated from the code tree)',
    '单进程 FastAPI 后端 + React 单页应用；箭头方向为调用方向':
        'Single-process FastAPI backend + React single-page app; arrows show the direction of calls',
    '前端　React 19 · TypeScript 6 · Vite 8 · Tailwind 4 · Leaflet（127.0.0.1:5174）':
        'Frontend　React 19 · TypeScript 6 · Vite 8 · Tailwind 4 · Leaflet (127.0.0.1:5174)',
    '5 个页面 · 22 个组件 · 统一出口 api/client.ts · 轮询 stores/usePolling.ts':
        '5 pages · 22 components · single HTTP exit api/client.ts · polling in stores/usePolling.ts',
    'REST · 2–4 秒轮询（位置 1.5 秒）': 'REST · 2–4 s polling (positions 1.5 s)',
    'API 层　backend/app/api（FastAPI，127.0.0.1:8100）':
        'API layer　backend/app/api (FastAPI, 127.0.0.1:8100)',
    '路由 · 事件判别联合 · 统一错误信封 · 进程状态锁 locked()':
        'routers · discriminated event union · uniform error envelope · process state lock locked()',
    'Agent 与编排层　backend/app/agents · backend/app/orchestration':
        'Agent & orchestration layer　backend/app/agents · backend/app/orchestration',
    '编排器为确定性状态机；Agent 运行时在独立线程中决策，工具调用在短事务内提交':
        'The orchestrator is a deterministic state machine; the agent runtime decides on its own thread and '
        'applies each tool call in a short locked transaction',
    '调度核心　backend/app/scheduling（纯函数，可脱库单测）':
        'Scheduling core　backend/app/scheduling (pure functions, unit-testable without a database)',
    '快照 → 模拟 → 校验 → 受影响集合 → 打分 → 权限与策略 → 求解':
        'snapshot → simulate → validate → affected set → score → authority & policy → solve',
    '服务层　backend/app/services（30 个模块）': 'Service layer　backend/app/services (30 modules)',
    'schedule_service.commit_plan 是实时排班表的唯一写入路径':
        'schedule_service.commit_plan is the only writer of the live schedule',
    '外部服务适配层　backend/app/providers（失败时整体降级并在界面标注）':
        'Provider layer　backend/app/providers (whole-round degradation on failure, labelled in the UI)',
    '数据层　backend/app/models（SQLAlchemy 实体）· backend/app/db（SQLite WAL + 增量迁移）':
        'Data layer　backend/app/models (SQLAlchemy entities) · backend/app/db (SQLite WAL + additive migrations)',
    '来源：backend/app/ 与 frontend/src/ 的实际目录内容':
        'Source: the actual contents of backend/app/ and frontend/src/',
    # ---------------------------------------------------------------- business proposal figures
    '调度流程：今天 vs 使用 TechSched': 'The dispatch process: today vs. with TechSched',
    '左栏为目标客户当前的做法（基于访谈假设），右栏为本系统运行后的同一条流程':
        'The left column is how the target customer works today (an assumption, not a measurement); '
        'the right column is the same process once this system is running',
    '今天：电话 + 表格 + 个人经验': 'Today: phone calls, a spreadsheet and personal judgement',
    '使用 TechSched': 'With TechSched',
    '客户通过即时通讯描述问题': 'The customer describes the problem over instant messaging',
    '文员电话确认地址与单元号': 'An administrator confirms the address and unit number by phone',
    '在表格上找人，凭经验判断是否来得及': 'A technician is picked on a spreadsheet, feasibility judged from experience',
    '电话通知技师': 'The technician is told by phone',
    '技师请假：重新推演全天安排': 'Technician takes leave: the whole day is re-derived by hand',
    '逐个电话通知被影响的客户': 'Every affected customer is called individually',
    '客户在 App 描述一次，系统匹配问题库': 'The customer describes it once; the system matches the catalogue',
    '地址经地理编码确认，单元号必填': 'The address is confirmed by geocoding; the unit number is mandatory',
    '只展示当前真的排得进去的时间窗': 'Only windows that are genuinely available are offered',
    '常规工单自动派单并通知': 'Routine orders are dispatched and notified automatically',
    '技师请假：系统重排并生成方案卡片': 'Technician takes leave: the system re-plans and prepares a decision card',
    '调度员批准或拒绝，理由与版本入库': 'The dispatcher approves or rejects; the reason and version are stored',
    '文员': 'Admin', '文员 + 客户': 'Admin + customer', '文员 + 技师': 'Admin + technician',
    '系统': 'System', '调度员': 'Dispatcher',
    '每次变更需要重新推演；过程与理由没有留存':
        'Every change means re-deriving the day; nothing about the reasoning is kept',
    '常规工单零人工介入；每个决定带版本、理由与影响清单':
        'Zero human involvement on routine orders; every decision carries a version, a reason and an impact list',
    '流程步骤为目标客户当前做法的假设，未经实地计时；右栏对应本系统的实际行为':
        'The left-hand steps are an assumption about current practice and were not timed on site; '
        'the right-hand steps describe this system\u2019s actual behaviour',

    '每一个调度决定的去向': 'Where every dispatch decision ends up',
    '分流规则写在 config/policy.yaml，不由模型判断；自动化的边界对企业是可配置、可审计的':
        'The routing rules live in config/policy.yaml and are not decided by the model; the boundary of '
        'automation is configurable and auditable by the business',
    '工单或突发事件': 'Work order or disruption',
    '新单 · 请假 · 迟到 · 投诉 · 加急': 'new order · leave · lateness · complaint · expedite',
    '确定性调度流水线': 'Deterministic dispatch pipeline',
    '可行性校验 · 权限检查 · 打分': 'feasibility check · authority check · scoring',
    '自动执行': 'Executed automatically',
    '决策分 > 70、在权限内、且无强制人工规则':
        'score above 70, within authority, and no rule forcing review',
    '调度员审批': 'Dispatcher approval',
    '分数不足 / P0 影响其他客户 / 搜索未完成':
        'score too low / a P0 affecting other customers / search incomplete',
    '人工队列': 'Human queue',
    '无可行方案 / 无合格技师 / 安全事件 / 客户要求':
        'no feasible plan / no qualified technician / safety event / the customer asks',
    '进入"人工队列"之前，Agent 先在有界预算内调查一轮：提出客户可接受的备选时间窗，或整理出带证据的人工事项。它不会静默失败，也不会无限重试。':
        'Before a case reaches the human queue, an agent investigates once within a bounded budget: it either '
        'offers the customer an alternative window, or assembles an evidenced case for a person. It never fails '
        'silently and never retries without limit.',
    '企业可以通过修改一个配置文件调整自动化边界（例如把自动执行的分数门槛调高），但任何配置都无法绕过可行性校验与权限上限。':
        'The business can move the automation boundary by editing one configuration file (for example raising the '
        'score threshold for automatic execution), but no configuration can bypass the feasibility check or the '
        'authority limits.',
    '来源：config/policy.yaml · backend/app/scheduling/policy.py':
        'Source: config/policy.yaml · backend/app/scheduling/policy.py',

    '从原型到生产的推进路径': 'The path from prototype to production',
    '每一阶段需要补齐的能力，以及该阶段可以验证的东西':
        'What has to be added at each stage, and what that stage lets you verify',
    '阶段一　原型（已完成）': 'Stage 1\u3000Prototype (complete)', '已部署可访问': 'deployed and reachable',
    '数据：合成技师与客户；真实问题库 CSV': 'Data: synthetic technicians and customers; the real catalogue CSV',
    '系统：单进程 + SQLite；真实模型、路网与地理编码':
        'Systems: single process on SQLite; real model, routing and geocoding',
    '人工：全部审批与人工队列已实现': 'People: all approval points and the human queue are implemented',
    '可验证：调度规则、权限边界、Agent 行为与可追溯性':
        'Verifies: the dispatch rules, the authority boundary, agent behaviour and traceability',
    '阶段二　试点（1 家企业 · 4–8 周）': 'Stage 2\u3000Pilot (one company, 4–8 weeks)',
    '需要企业配合': 'needs a partner company',
    '数据：导入真实问题库、技师名册与历史工单':
        'Data: import the real catalogue, technician roster and historical orders',
    '系统：接入真实通知通道；自建路网服务':
        'Systems: connect a real notification channel; self-host the routing service',
    '人工：保留全部审批点，按周复盘误判':
        'People: keep every approval point; review misjudgements weekly',
    '可验证：KPI 基线与改善幅度、人工介入比例':
        'Verifies: the KPI baseline and the size of the improvement; the share of human involvement',
    '阶段三　生产': 'Stage 3\u3000Production', '规模化前提': 'prerequisites for scale',
    '数据：多企业隔离；问题库版本管理': 'Data: tenant isolation; catalogue version management',
    '系统：多租户、PostgreSQL、多进程、真实支付与定位':
        'Systems: multi-tenancy, PostgreSQL, multi-process, real payment and positioning',
    '人工：分角色权限与完整审计': 'People: role-based permissions and a full audit trail',
    '可验证：单位成本、留存与跨部门扩展':
        'Verifies: unit cost, retention and expansion across departments',
    '本系统当前处于阶段一并已线上运行（见技术设计文档第 7 章）。阶段二不需要重写系统：问题库、技师名册与业务规则都是配置，接入真实通道是替换 provider 实现。':
        'The system is at stage 1 today and is already running online (see chapter 7 of the technical design '
        'document). Stage 2 requires no rewrite: the catalogue, the roster and the business rules are all '
        'configuration, and connecting a real channel means swapping a provider implementation.',
    '阶段二与阶段三的时间与范围为规划假设，尚未实施':
        'The timing and scope of stages 2 and 3 are planning assumptions and have not been carried out',
}
