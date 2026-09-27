# Architecture

Single backend process (FastAPI + SQLite) + React SPA. Three logical agents plus a deterministic orchestrator live in
the same process; the LLM (mock or Anthropic) only does semantic understanding, complaint classification and never
decides skills, durations, scores, execution facts or approvals.

```
┌───────────────────────────── frontend (React/Vite, :5174) ─────────────────────────────┐
│  Customer Chatbot page                     Dispatch Dashboard page                      │
│  (session-scoped orders, confirm card,     (clock controls, timeline, map, orders,      │
│   simulated payment, status, cancel,        risks, review queue/plan cards, agent log,  │
│   complaint)                                notifications, versions, event injector)    │
└──────────────┬──────────────────────────────────────────┬───────────────────────────────┘
               │ /api/chat/*                              │ /api/* (polling 2–4 s)
┌──────────────▼──────────────────────────────────────────▼───────────────────────────────┐
│ backend/app/api  (routers, discriminated event union, error envelope, state lock)        │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ agents/understanding   UnderstandingAgent: lookup_catalog · resolve_location · time hint  │
│ agents/scheduling_agent  explanation from solver metrics (no model-made numbers)         │
│ services/risk_service   RiskMonitoringAgent: rule scan, risk events, effective priority   │
│ orchestration/orchestrator  received→snapshot→classify→standby/solve→validate→score→      │
│                             policy→commit | pending_review | unresolved                  │
│ scheduling/  domain · simulator · validator · affected · scoring · policy · solver        │
│ services/    schedule_service (commit_plan = ONLY writer of the live schedule),           │
│              plan_service (approve/reject/recompute with in-transaction re-validation),   │
│              event_service (cancel, technician unavailability, complaints, paid expedite),│
│              execution_service (depart/arrive/start/complete, clock advance, scan loop),  │
│              standby_service, initial_service, chat_service, evaluation_service,          │
│              catalog_importer/catalog_service, demo_service (seed/reset), clock           │
│ providers/route  fixture · estimated · osrm  (whole-matrix degradation, None=unreachable) │
│ providers/llm    mock (rule/alias) · anthropic (structured outputs) · degradable wrapper  │
│ models/ SQLAlchemy entities  ·  db/ SQLite (WAL)                                          │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

## Agent boundaries

| Module | Input | Does | Output | May NOT |
|---|---|---|---|---|
| UnderstandingAgent | customer text, session draft, catalog, preset locations | interpret (LLM/mock), validate ids against catalog, resolve location to a preset, parse time hints, ask one clarifying question | draft fields / options / confirmation card | invent coordinates, phone, payment facts, durations |
| SchedulingAgent | order, snapshot, priority | route matrix, solver, validator, scorer, explanation text | candidates + structured reasons | change fixed parameters, decide approvals |
| RiskMonitoringAgent | clock, orders, assignments, events | rule scan, cancellation/complaint classification, risk events with stable keys, effective priority | risk events, dispatch triggers | move orders directly |
| Orchestrator | events and module outputs | stages, versions, candidate persistence, commit / review / unresolved, AgentRun tracing | traceable closed loop | hold an LLM request while waiting for approval (approval waits are rows) |
| PolicyEngine | facts, scores, priority | auto / manual / forbidden / no_action / standby | decision + reasons | be bypassed by UI buttons or config booleans |

## Normal flow (new order)

1. Chat confirm → `order_service.create_order` (catalog snapshot copied onto the order; base priority from paid flag).
2. `dispatch_order`: build snapshot → evaluate risk → `solve_insert` (qualified technicians × insertion positions;
   P1/P0 additionally relocate/reorder one movable order) → each candidate: `validate_plan` (independent), affected set,
   authority, scores → PolicyEngine.
3. Candidates persisted as `CandidatePlan` rows (preview only). AUTO → `commit_plan` (new `ScheduleVersion`, assignment
   rows, order status, notifications). MANUAL → order `PENDING_REVIEW`, plans wait. None → `UNRESOLVED` + dispatcher alert;
   over-limit plans are stored with status `OVER_LIMIT` for explanation only.

## Exception flow (technician unavailable)

Facts first (interval stored, technician version bumped) → each overlapping assignment: departed → `EXECUTION_INTERRUPTED`
risk, manual; undeparted → invalidated, `TECHNICIAN_CANCELLED` risk with remaining minutes (P0/P1/P2) → pending plans
using the technician invalidated → recovery dispatch per order, most urgent first; if more than one order was released,
any plan that affects other orders is forced to review.

## Cancel flow (customer)

Owner check → must be `OPEN` (EN_ROUTE/ARRIVED/IN_PROGRESS → 409 `already_departed`; COMPLETED → 409) → atomically:
lifecycle `CANCELLED`, assignment row `CANCELLED`, risks resolved, standby invalidated, candidate plans containing the order
invalidated (approval later returns 409), successors re-simulated from the real predecessor (technician & starts kept),
notifications. Repeated cancel is idempotent. Departure vs cancel is decided by the persisted lifecycle under the process lock.

## Time & clock

`SimulationState.now` is the single business time (UTC in DB, `+08:00` in API, minutes-from-local-midnight in the solver).
`advance_clock` steps one minute at a time: due execution events (depart→arrive→start→complete) then a scan (prediction
refresh, risk evaluation, plan expiry, dispatch of what changed). The background loop (worker thread) advances the clock when
running and otherwise scans every `RISK_SCAN_INTERVAL_SECONDS`. Reset bumps `scenario_generation`; every scenario-scoped row
carries it, so stale results from a previous generation are ignored.

## Concurrency

All mutating endpoints and the background loop run under one re-entrant process lock (`locked` decorator owns lock +
session + commit before the response). Reads are lock-free. Multi-process deployment is explicitly out of scope.
