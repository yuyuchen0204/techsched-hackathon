# V3 — Agent tools, task runtime and budgets

Written for engineers extending the scheduling agents. Source of truth: `backend/app/agents/tools.py` (registry),
`backend/app/agents/runtime.py` (task loop), `backend/app/agents/policies.py` (decision policies), `config/policy.yaml` (budgets).

## 1. Principles (brief §10–§11)

* **Tools are the only way an agent touches the system.** Every tool returns a structured `ToolResult`
  `{status, snapshot_version, data, reason_codes, evidence_refs}`; free text is never parsed for facts.
* **The model chooses, the engine decides.** A policy (model or rule-based) only *names* a stored plan id; `submit_plan`
  re-validates it against the current schedule version and the PolicyEngine commits or opens an approval. A model cannot
  widen authority through tool arguments: unknown / mistyped args are rejected (`INVALID_ARGS`), tools are role-gated
  (`ROLE_NOT_ALLOWED`), and priority limits are enforced inside the tool, not in the prompt.
* **Read tools are trials, not reservations.** `simulate_insertion`, `search_local_repair`, `propose_alternative_windows`
  and `simulate_break` never change the live schedule; candidates are stored as `PROPOSED` plans with a base version and expiry.
* **Two fences, not one.** The role gate on a `ToolSpec` says what a role *may* do; the role's skill file
  (`config/agent_skills/<role>.md`) says what it *does* — a narrower list. A tool the skill withholds returns
  `forbidden / TOOL_NOT_IN_SKILL`. This is how responsibilities stay separate: the `recovery` role is permitted
  `submit_break` by the role gate, but its playbook withholds it, so a technician it has pushed over the rest
  threshold must be handed to the `break` agent instead of quietly fixed in passing.
* **Bounded search.** Per wake-up: `max_tool_calls_per_wakeup: 12`, `max_plan_searches_per_wakeup: 3`
  (`simulate_insertion`, `search_local_repair`, `propose_alternative_windows` count), `transient_retries: 2`
  (`TOOL_UNAVAILABLE`). Exhaustion never loops: the task flags a human with the full trace.
* **Fast path first.** Ordinary orders never reach the agent runtime: the V2 pipeline (insertion → bounded repair →
  PolicyEngine) answers in one request. A task is created only on `UNRESOLVED`, on rest evaluation, or when a human
  resolves a case that was blocking a task.

## 2. Tool registry

`*` = required argument. Roles: `customer`, `scheduling`, `recovery`, `break`, `dispatcher`.

| Tool | Kind | Roles | Arguments | Purpose |
|---|---|---|---|---|
| `search_repair_catalog` | read | all | query*: string, limit: integer | Search the repair catalog (trade, problem, complexity, fixed duration). |
| `search_address` | read | all | query*: string | Geocode a typed address / postal code / landmark into candidates (OneMap → Nominatim). |
| `get_customer_history` | read | all | customer_id: string | Authorised history: orders, actual problems, feedback, negative technicians (role-scoped). |
| `get_order_context` | read | all | order_id: string | Current order facts: status, priority, window, risks, assignment, recent plans, **authority**. |
| `query_technicians` | read | scheduling, recovery, break, dispatcher | trade_type: string, min_level: integer | Technicians with skills, status, next free time/location. |
| `get_travel_times` | read | scheduling, recovery, break, dispatcher | from_location_id*: string, to_location_ids*: array | Travel-time estimates between locations (matrix cache). |
| `simulate_insertion` | read · search | scheduling, recovery, dispatcher | order_id: string | Zero-disturbance insertion trial; candidates stored as proposals. |
| `search_local_repair` | read · search | scheduling, recovery, dispatcher | order_id: string | Bounded repair within the order's authority (P1: ≤2 undeparted P3; P0: ≤5 undeparted P2/P3). |
| `validate_plan` | read | scheduling, recovery, dispatcher | plan_id*: string | Re-validate a stored plan against current facts; returns the policy decision. |
| `propose_alternative_windows` | read · search | all | order_id, catalog_item_id, location_id, excluded_technicians, exclude_windows, after, count, paid | Feasible appointment windows (30-min steps, 90-min windows, zero disturbance, with technician + earliest start). |
| `evaluate_break_need` | read | break, recovery, dispatcher | technician_id: string | Work-since-last-rest facts and the level (`none` / `pre_evaluate` / `evaluate` / `escalate`). |
| `simulate_break` | read | break, recovery, dispatcher | technician_id, start, minutes, window_start, window_end | Zero-disturbance rest trial: one start, or a window search returning slots + rejection reasons. |
| `create_customer_question` | write · **waits for customer** | all | kind, question*, options, order_id | Structured question in the customer app; the task pauses until answered. |
| `flag_for_human` | write · **waits for human** | all | category*, urgency, reason_summary*, evidence_refs, attempted_actions, unresolved_questions, suggested_next_action, order_id, incident_id, idempotency_key | Human case with evidence; the task pauses until the case is resolved. |
| `create_safety_incident` | write | all | danger_type*, description*, known_location, order_id | Records an incident (+ critical human case). Never reports externally. |
| `submit_plan` | write | scheduling, recovery, dispatcher | plan_id*, expected_version | Re-validates; PolicyEngine commits (`submitted: committed`) or queues (`submitted: pending_review`). |
| `submit_break` | write | break, recovery, dispatcher | technician_id, start*, minutes, expected_version, idempotency_key, reason | Commits a rest block after re-validating zero disturbance in the same transaction. |
| `delegate_task` | write · **waits for another agent** | scheduling, recovery, dispatcher | role*, goal*, order_id, technician_id, reason, context | Hands a sub-problem to another role; the task pauses until the child finishes (depth ≤ 2, never your own role). |
| `notify_in_app` | write | all | recipient_ref, recipient_type, type, message*, order_id, idempotency_key | In-app notification (`delivery_mode=simulated`). `recipient_ref` is a session id, a customer id or `dispatcher`; a back-office role that omits it addresses the dispatcher. |

### Result statuses and reason codes

`status ∈ ok | no_solution | stale | forbidden | data_incomplete | error | budget_exhausted`.
Reason codes reuse the API error vocabulary: `INVALID_STATE`, `VERSION_CONFLICT`, `POLICY_VIOLATION`, `DATA_INCOMPLETE`,
`NOT_FOUND`, `FORBIDDEN`, `TOOL_UNAVAILABLE`, `SEARCH_BUDGET_EXHAUSTED`, plus tool-specific ones
(`NO_QUALIFIED_TECHNICIAN`, `NO_ZERO_DISTURBANCE_SLOT`, `BREAK_IN_PAST`, `OUTSIDE_SHIFT`, `OVERLAPS_EXECUTING_TASK`,
`ROUTE_INFEASIBLE`, `SUCCESSOR_START_SHIFT`, `SUCCESSOR_WINDOW_VIOLATION`, `ROLE_NOT_ALLOWED`, `INVALID_ARGS`, `UNKNOWN_TOOL`,
`TOOL_NOT_IN_SKILL`, `DELEGATE_SAME_ROLE`, `DELEGATION_DEPTH_EXCEEDED`, `UNKNOWN_ROLE`, `NO_BUDGET_TO_DELEGATE`, `NO_RECIPIENT`).

## 3. Task runtime (`runtime.run_task`)

```
create_task(role, goal, order/technician/customer, dedupe_key)   # None when an equivalent live task exists
run_task:
  loop (≤ max_tool_calls):
    action = policy.decide(task, history, ctx)        # ModelPolicy (real) or MockPolicy (rules)
    finish → status ∈ succeeded | no_solution | failed
    call_tool → trace "planned" → invoke() → trace returned|validated|submitted|error
    wait == customer → status waiting_customer (pending_question_id)
    wait == human    → status waiting_human (human_case_id)
    wait == agent    → status waiting_agent (waiting_child_id); the child runs, then wakes the parent
    stale twice      → status stale (re-plan on next wake-up)
    TOOL_UNAVAILABLE > transient_retries → failed
  budget_exhausted / no_solution → automatic flag_for_human (category agent_budget_exhausted / no_solution)
```

* Every phase is persisted as a `ToolTrace` row (`planned → called → returned → validated → submitted`, or `error`,
  and a final `decision` row for the ending), with args (redacted), the policy's own reason (`thought`), result status,
  reason codes, evidence refs, duration and `decided_by` (`model` / `mock`). The dashboard **Agents → Reasoning** tab
  renders these rows as a timeline; `/api/dev/tool-traces` lists them raw.
* **Wake-ups**: `answer_question` (customer app) → `wake_task_for_question`; `human_service.resolve` → waiting tasks become
  `pending` with `wakeup_reason`; a delegated child finishing → `_wake_parent`; `execution_service.scan` runs `run_pending` (mock inline, real in the worker thread).
* **Dedupe keys**: `sched:{order_id}:{schedule_version}` for scheduling/recovery, `break:{tech}:{last_break_end}` for rest.
* **Execution mode** follows `LLM_MODE`: `real` uses `ModelPolicy` (JSON action protocol through the configured
  OpenAI-compatible / Anthropic provider); unavailability degrades to `MockPolicy` and the trace says so (`degraded`).
* **Locking**: `worker_step` (dedicated `agent-worker` thread) runs `_begin` under the state lock, then for each step
  `_decide` (the model call) **without** the lock and `_step` (tool call + traces + state) in a short locked transaction;
  a scenario reset between steps ends the task as `stale`. `run_task` (mock / tests / explicit `/run`) does the same
  sequence inline in the caller's session.

## 4. Policies

* **ModelPolicy** (`policies.ModelPolicy`): the model receives the task goal, role guidance, the compact tool history
  (statuses, reason codes, candidate ids/scores) and the tool schemas; it must return one JSON action
  `{kind: call_tool|finish, tool, args, status, summary}` validated by `ActionSchema`. Invalid JSON → one retry → mock.
* **MockPolicy**: deterministic, result-driven rules (labelled `mock` in every trace):
  * scheduling/recovery: `get_order_context` → `simulate_insertion` → `submit_plan` (best admissible candidate);
    no candidate → `search_local_repair` when authority allows moves (`max_affected > 0`) → `submit_plan`;
    `NO_QUALIFIED_TECHNICIAN` → `flag_for_human`; with a customer session → `propose_alternative_windows`
    → `create_customer_question`; otherwise `finish(no_solution)` (→ human).
  * break: `evaluate_break_need` → `simulate_break` (search window) → `submit_break` (earliest slot) →
    stale → one re-search → `flag_for_human(rest_conflict)`.
* Tests in `backend/tests/test_v3.py` cover: success stops after one submit; zero-disturbance failure switches to bounded
  repair (never a retry of the same search); budget exhaustion → human case with attempted actions; role/arg rejection;
  a plan beyond authority becomes `pending_review`, never a commit.

## 5. Skills (`config/agent_skills/*.md`)

One markdown file per role. YAML front matter is the machine-readable contract; the body is the playbook the model is
given verbatim, appended to the shared `SYSTEM` prompt by `policies.system_prompt(role)`.

| Front-matter key | Effect |
|---|---|
| `name` | The role it applies to (`scheduling`, `recovery`, `break`, `customer`, `dispatcher`). |
| `tools` | **Enforced** allow-list, built into `ToolContext.allowed_tools`. May only narrow the role gate, never widen it (a test asserts this). `"*"` or omitted = no narrowing. |
| `budget.tool_calls` / `budget.searches` | Per-wake-up ceiling for this role, overriding `policy.yaml` when tighter. |
| `escalate_when` | Appended to the prompt as the conditions that make `flag_for_human` mandatory. |

`GET /api/agent-skills` returns the loaded set plus, per skill, `withheld_by_skill` (permitted by role, withheld by the
playbook) — that column is what the Agents → Skills panel renders. `POST /api/agent-skills/reload` re-reads the files
without restarting the backend. `backend/app/agents/skills.py` is the loader; a malformed file is logged and skipped
rather than taking the app down.

## 6. Agent-to-agent delegation (`delegate_task`)

A task may hand a sub-problem to another role and pause until that child finishes.

* **Bounds.** Depth ≤ 2 (`MAX_DELEGATION_DEPTH`); never to your own role (`DELEGATE_SAME_ROLE`); the child's tool budget
  is carved out of the parent's remaining budget (`budget_cap` in the child's facts), so a chain can never cost more
  than one top-level task was allowed to spend.
* **Mechanics.** `delegate_task` returns `wait: "agent"`; the runtime sets the parent to `waiting_agent` with
  `waiting_child_id`, then starts the child — inline for mock tasks, via the worker thread for model tasks. When the
  child reaches a terminal status, `_wake_parent` sets the parent back to `pending` and writes the outcome into
  `facts_summary.delegate_result` (and appends it to `child_results`, which survives across wake-ups).
* **Where it fires today.** `recovery → break`: after a successful `submit_plan`, the recovery agent calls
  `evaluate_break_need` on the technician who absorbed the work and hands the rest decision over, because its own
  playbook withholds `simulate_break`/`submit_break`. `dispatcher → recovery`: the supervisor task
  (`POST /api/agent-tasks/supervise`) reads every broken order, delegates one recovery agent per order in urgency
  order, and reports one consolidated result instead of N scattered notifications.

## 7. Traces as a reasoning timeline

Every `planned` trace now stores the policy's own `summary` in `ToolTrace.thought`, and `_finish` writes a final
`decision` / `finish` row — so a trace reads as *why → what → how it ended* rather than a list of calls with no
conclusion. The dispatcher's Agents → Reasoning panel pairs each `planned` row with the result row that answered it
and nests a delegated child under the step that handed it over.

## 8. Agent scorecard (`GET /api/agent-scorecard`)

`services/agent_metrics.py` grades the agent layer, not the schedule (that is `evaluation_service`). All of it is
derived from `AgentTask` / `ToolTrace` rows, so it cannot drift from what happened:

| Metric | Question it answers |
|---|---|
| `autonomy.rate_pct` | How much settled without a person. |
| `cost.avg_tool_calls` / `budget_exhausted` | What a task costs, and how often it runs out. |
| `discipline.wasted_pct` | Calls that could never have worked (`ROLE_NOT_ALLOWED`, `TOOL_NOT_IN_SKILL`, `INVALID_ARGS`, budget) plus repeated identical searches — the one thing every playbook forbids. |
| `handover_quality.rate_pct` | Escalations carrying `evidence_refs`, `attempted_actions` and `suggested_next_action`. |
| `collaboration` | Delegations, sub-tasks, depth, and which role handed work to which. |
| `transparency.rate_pct` | Steps that recorded why, not just what. |
| `execution_mode` | Model vs. rule policy, and tasks that degraded to the rule policy. |

## 9. Adding a tool

1. Implement `t_<name>(ctx, args) -> ToolResult` in `tools.py`; read tools must not write; write tools must be idempotent
   (accept `idempotency_key`) and re-validate against `expected_version` where a schedule is involved.
2. Register a `ToolSpec` with a JSON schema (`_schema(props, required)`), `writes`, `roles`, `is_search`.
3. Add a rule branch to `MockPolicy` (so the mock path stays complete) and list the tool in every
   `config/agent_skills/*.md` whose role should actually use it — a tool absent from a skill is unreachable for that role.
4. Add a test: authority rejection + the happy path.
