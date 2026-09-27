# Data contracts

OpenAPI is served at `/docs` (`/openapi.json`). This file explains the semantics the schema cannot.

## Time
- API: ISO 8601 with offset (`2026-09-15T09:30+08:00`, Asia/Singapore). Inputs may be any offset or naive (treated as local).
- DB: naive UTC. Solver: integer minutes from local midnight of the scenario day.
- Windows: `window_start ≤ service_start ≤ window_end` (start exactly at `window_end` is legal; service may end after it).
  Intervals are `[start, end)`. Deadline for risk = `window_end`.
- Predicted vs actual: `assignment.departure/arrival/service_start/service_end` are predictions until the matching actual
  timestamp exists on the order (`departed_at`, `arrived_at`, `service_started_at`, `completed_at`).

## Enumerations
- lifecycle: `DRAFT NEEDS_INFO OPEN EN_ROUTE ARRIVED IN_PROGRESS COMPLETED CANCELLED` (ARRIVED counts as departed/locked).
- scheduling: `UNASSIGNED PROPOSED PENDING_REVIEW ASSIGNED UNRESOLVED`.
- priority: `P0 P1 P2 P3`; `base_priority` (P3 / P1 paid), `risk_priority`, `effective_priority` = most urgent.
- plan status: `PROPOSED PENDING_REVIEW COMMITTED REJECTED EXPIRED INVALIDATED OVER_LIMIT SUPERSEDED`.
- policy decision: `auto manual forbidden no_action unresolved needs_info standby`.
- solve status: `feasible partial no_solution_found timeout error`.
- risk types: `OVERDUE_NOT_STARTED PREDICTED_LATE APPROACHING_DEADLINE TECHNICIAN_CANCELLED LATENESS_COMPLAINT_VERIFIED NON_SCHEDULING_COMPLAINT EXECUTION_INTERRUPTED UNASSIGNED_ETA_UNKNOWN`; status `active | manual | resolved`.

## Entities (tables)
`catalog_items`, `catalog_imports`, `locations`, `work_orders` (with `catalog_snapshot`, `priority_reasons`, `version`,
`last_dispatch_key`, `pending_plan_run_id`, `recovery_start`, `breach_recorded_at`), `technicians` (skills map, breaks,
`unavailable_intervals`, `version`), `assignments` (ACTIVE/INVALIDATED/COMPLETED/CANCELLED, `locked`, `score_components`),
`schedule_versions` (parent, reason, sim_now, active, policy/route snapshot, full assignment snapshot), `risk_events`
(`idempotency_key = order:type`, first/last_seen), `standby_candidates`, `candidate_plans` (base schedule/data versions,
route snapshot id, policy version, expiry, assignments = every row that differs from the base, diff, affected ids,
policy_check, validation, decision_score, metrics), `approvals` (idempotency_key, result), `agent_runs` (steps with tool
calls and facts), `notifications` (`delivery_mode=simulated`, dedupe key), `simulation_state`, `chat_sessions`,
`inbound_events` (idempotency_key, status received/processing/resolved/unresolved), `evaluations`.

## Versions & races
- `work_orders.version` / `technicians.version` change on business facts (status, priority, assignment, availability).
  Observation-only updates (risk `last_seen`, standby lists) never bump them.
- A candidate binds `base_schedule_version` + versions of the orders it touches. Approval re-validates inside the
  transaction: scenario generation, plan status, expiry, target still OPEN & undeparted, schedule version unchanged,
  involved orders unchanged, then full hard-constraint + authority + score re-check against the current snapshot.
  Failures → `409` with codes `plan_expired | schedule_changed | facts_changed | target_closed | target_departed |
  revalidation_failed | over_limit | forbidden | plan_not_pending | scenario_reset`; the plan is marked EXPIRED/INVALIDATED.
- `expected_versions` (optional on approve) → `409 version_conflict` with the mismatch.
- Departure vs cancel: whichever persists first wins; the other gets `409 already_departed` or the departure is skipped.
- Idempotency: `idempotency_key` on cancel, events, approve, reject → the stored result is returned unchanged.

## Errors
`{"error": {"code", "message", "details", "request_id"}}` — `422` validation/field errors, `409` state/version conflicts,
`404` unknown ids, `403` wrong customer session.

## API (all under `/api` unless noted)
| Method & path | Notes |
|---|---|
| `GET /health` | mode, catalog status, policy version, sim time (no secrets) |
| `GET /catalog?q=`, `POST /catalog/reload`, `GET /locations?include_custom=` | |
| chat option `geocode {label, lat, lon, name, source}` | ambiguous address candidates; selecting one sends `select_location {lat, lon, name}` |
| `POST /locations/resolve {lat, lon, name?}` | map pin → `{location, snapped, note, arbitrary_points_supported}`; snaps to nearest preset in fixture mode; 422 outside the Singapore box |
| `GET /routes/technician/{id}`, `GET /routes/geometry?from_id&to_id` | road geometry per leg (`schematic=false` with OSRM), planned vs free-flow minutes, km |
| `POST /chat/messages` `{session_id, message?, action?, payload?}` | actions: `select_catalog select_location (location_id | lat,lon,name) set_window set_contact toggle_paid confirm cancel_order complaint status reset`; option type `map_pick` asks the UI to open the map picker |
| `GET /chat/sessions/{id}` · `POST /orders/interpret {text}` | structured understanding only (no side effects) |
| `GET/POST /orders`, `GET/PATCH /orders/{id}`, `POST /orders/{id}/cancel`, `POST /orders/{id}/execution-events {event}`, `GET /orders/{id}/standby` | client can never set complexity/duration/priority/score |
| `GET /technicians`, `POST /technicians/{id}/unavailability` | technicians include anchor and route |
| `GET /schedules/current`, `GET /schedules/versions`, `GET /schedules/{version}` | |
| `POST /scheduling/initial`, `POST /scheduling/dispatch {order_id}` | |
| `POST /events` | discriminated union on `type`: `paid_expedite_order technician_unavailable lateness_complaint non_scheduling_complaint customer_cancel` |
| `GET /risks`, `GET /plans?status=`, `GET /plans/{id}`, `POST /plans/{id}/approve|reject|recompute` | |
| `GET /runs/{id}`, `GET /agent-runs`, `GET /notifications` | |
| `GET /demo/clock`, `POST /demo/clock/advance {minutes}`, `POST /demo/clock/control {running}`, `POST /demo/reset {scenario}`, `POST /demo/scan` | |
| `POST /evaluations {seeds?}`, `GET /evaluations`, `GET /evaluations/{id}` | |

Example — approve conflict:
```json
{"error":{"code":"facts_changed","message":"underlying facts changed","details":{"stale":["order:wo_007"]},"request_id":"…"}}
```
