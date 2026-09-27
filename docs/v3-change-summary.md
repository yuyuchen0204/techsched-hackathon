# V3 change summary (third-meeting upgrade)

Written for the project team: what changed from V2, why, and where to look. The brief is `docs/v3-upgrade-brief.md`.
Everything is incremental on the V2 repository; the V2 scheduling core (simulator, validator, scoring, PolicyEngine,
solver, `commit_plan` as the only writer) is unchanged.

## 1. Three-end closed loop

| End | Route | What it does |
|---|---|---|
| Customer app (mobile-first) | `/customer`, `/customer/orders/:id` | identity selector (returning customers), chat that only offers catalog problems, **address modal** (search / postal code / map pin + mandatory unit or "no unit"), feasible windows with technician and earliest start, "talk to a human", safety entry point, order page with cancel / expedite (idempotent) / complaint / reschedule request / handoff replies / rating after completion / live tracking after departure. |
| Technician app (simulated) | `/technician` | identity selector, day bar, dynamic rest facts + declared rest, current/next job with address + unit + customer history hint, route map, depart → arrive → start → complete (manual mode only), leave form (idempotent), service report after completion. |
| Dispatcher | `/` | 4 KPIs (pending orders, orders at risk, pending human, available technicians; more counters on demand), human queue tab (take / reply / resolve, safety incidents), agent activity tab (tasks with phases + fast-path runs), live technician positions with status colours that **glide continuously** along the road geometry (markers persist across 1.5-s polls with a CSS transform transition; the same for the customer's tracking map), one-click **Reset demo** (scenario main at 08:30, every table cleared, customer / technician tabs in the same browser reset themselves via a storage broadcast), **Demo controls** drawer (scenario loader with load summary, event injector) and **Dev** drawer (runtime facts, duration shadow report, tool traces). Inject Event is gone from the main surface. |

Backend: `app/api/routes_v3.py` (customer / technician / positions / human cases / incidents / agent tasks / dev),
`app/api/routes_demo.py` (+ `/api/demo/scenarios`, `/api/demo/load-summary`).

## 2. Data model (additive, migrated)

New tables: `customers`, `customer_addresses`, `execution_events`, `service_reports`, `customer_feedback`, `human_cases`,
`safety_incidents`, `agent_tasks`, `tool_traces`, `break_blocks`, `customer_questions`, `reschedule_requests`,
`duration_observations`, `model_versions`, `schema_migrations`.
New columns: `work_orders.customer_id / address (JSON) / report_status / excluded_technician_ids / human_case_id / expedite_event_key`,
`technicians.sim_mode / demo_login`. `app/db/migrations.py` adds missing columns with `ALTER TABLE`, back-fills
`address` from the existing location (`unit_pending: true`) and records versions in `schema_migrations`. The live DB was
backed up to `data/app.db.pre-v3.bak` before the first V3 start.

Error bodies now carry `kind ∈ INVALID_STATE | VERSION_CONFLICT | POLICY_VIOLATION | DATA_INCOMPLETE | NOT_FOUND | FORBIDDEN | TOOL_UNAVAILABLE | SEARCH_BUDGET_EXHAUSTED`
next to the existing `code`/`message`.

## 3. Customer experience

* Problem selection is **catalog-only** (model suggestions are filtered to catalog ids; the chat offers alternatives as buttons).
* Address: coordinates come only from a search result (OneMap → Nominatim) or a map pin; a unit / floor is mandatory
  unless explicitly "not applicable"; saved addresses are reused for returning customers (`use_saved`, `use_saved_address`).
* Windows: `negotiation_service.propose_windows` runs zero-disturbance trials in 30-minute steps and shows only feasible
  windows with the technician and earliest start; "none of these" asks for more or hands off to a human. A requested
  window that is not feasible is validated on submit and re-negotiated, never silently accepted.
* Customer history (§8): `customer_service.customer_history(role=customer|technician)` scopes fields by role; a repeat
  hint is worded as "similar repair record", only from **completed** orders of the same customer (or the same precise
  point), never from shared preset areas.
* Preferences (§8.3): a technician rated ≤2 by this customer becomes a soft ranking penalty (`PREFERENCE_WEIGHT=8`),
  never an exclusion; when only that technician is feasible the chat says so and offers a per-order exclusion.
* Safety (§11): deterministic triggers with negation / past guards plus an optional model flag (`safety_concern`) create a
  `SafetyIncident` + critical human case; the emergency entry point only opens a `tel:` link or records a demo
  assistance request. Numbers (SCDF 995, Police 999, City Energy 1800 752 1800) were verified against gov.sg /
  cityenergy.com.sg and carry their source in `config/policy.yaml`.

## 4. Human cases (§9)

`human_service.flag_for_human(source=CUSTOMER_REQUEST | POLICY_REQUIRED | AGENT_ESCALATION)` with idempotency keys
and open-case merge (evidence appended, urgency escalated). Policy-required cases are created when a plan needs approval
(`plan_approval`, auto-resolved by approve/reject) and when an executing task is interrupted (`execution_interrupted`).
While a customer's case is open the assistant is paused: messages go to the case, no scheduling commitments are made,
and dispatcher replies appear in the customer app. Resolving a case wakes any agent task waiting on it.

## 5. Agent runtime (§10) — see `docs/v3-agent-tools.md`

18 tools with structured results, role gating and argument checks; `AgentTask` / `ToolTrace` persistence; budgets
12 / 3 / 2; wake-ups on customer answers and human resolutions; `MockPolicy` (rules) and `ModelPolicy` (DeepSeek via the
OpenAI-compatible provider, JSON action protocol). Model calls never hold the process lock: a dedicated worker thread
decides outside the lock and applies each tool call in a short locked transaction (the chat endpoint likewise
pre-computes interpretation / classification on a read-only session), so the clock and all three apps stay responsive
while the model thinks. Live check on the scarce scenario: the model-driven task for an
unassignable P3 order read the context, ran one zero-disturbance trial, proposed alternative windows and escalated with
a precise summary in 4 tool calls (`hc_… no_admissible_slot`).

## 6. Dynamic rest (§7) replaces the fixed lunch

Scenarios no longer carry a uniform break. `break_service.work_facts` counts travel + service since the last recorded
rest; after 180 min (pre-evaluation 60 min before) a `break` task searches 90 min ahead for a zero-disturbance slot and
commits it with a schedule-version check (`submit_break`); 240 min without rest escalates. Technicians can declare a rest
(fact, re-checked by the risk scan). Rest blocks are unavailable intervals in every later solve.

## 7. Execution facts and duration data (§12–§13)

`ExecutionEvent` records who moved an order (simulation / technician app / dispatcher) and flags anomalies (started
before/after the window, late vs plan, much shorter/longer than baseline). Manual technicians are driven only by the app
(single driver). Completion creates a `DurationObservation` (actual = start → complete, quality flags instead of guesses)
and a pending `ServiceReport`. The shadow model (`median_by_problem_time_split`, ≥5 samples, 90 days) predicts next to
the CSV baseline and reports MAE; scheduling durations stay on the catalog (`duration_prediction.mode: shadow` in `config/policy.yaml`).

## 8. Scenarios and evaluation (§17–§18)

`data/scenarios/main.json` (busy baseline), `relaxed.json` (12 orders), `scarce.json` (two technicians on leave, 32
orders, 7 unassigned at seed). `POST /api/demo/reset {scenario}` seeds history (alice / bob / carol) for returning-customer
demos. Tests: 90 (`backend/tests`, 26 new in `test_v3.py`); browser E2E `scripts/e2e/main_flow.mjs` drives all three
ends (mobile width for customer and technician) — 21 checks, no console errors under DeepSeek + OSRM + OneMap.

## 9. Not done / deliberately left out

* No real SMS, payment, dialling or GPS; positions are simulated from route geometry and the sim clock (labelled).
* The dashboard's `created_at` on human cases is wall-clock UTC (cases are operational records, not sim events).
* `InterpretedRequest.safety_concern` is prompted for the real model; the mock provider never sets it (deterministic
  triggers cover the demo).
