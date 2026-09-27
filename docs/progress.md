# Progress Log

Project: Technician Scheduling (V2 brief, `docs/project-brief.md`; V3 upgrade brief, `docs/v3-upgrade-brief.md`). Fresh build in
`project_v2`; the sibling `project/` folder is the V1 prototype (old catalog design) and is not reused.

Legend: ✅ done & verified here · ⚠️ written but not verified against a live service · ⬜ not done

## Environment
- Python 3.12.14 (`uv`), FastAPI 0.141 / SQLAlchemy 2.0.52 / Pydantic 2.13 / anthropic SDK 1.5 — `backend/uv.lock`, `requirements*.lock`.
- Node 26.7, React 19, Vite 8, Tailwind 4, Leaflet 1.9, Playwright 1.63 (dev) — `frontend/package-lock.json`.
- Ports 8100 (API) / 5174 (UI) because 8000/5173 are occupied by the V1 prototype on this machine.
- Docker not installed on the dev machine → `compose.yaml` + Dockerfiles ⚠️ unverified.

## Phase 1 — Data skeleton ✅
- Real CSV read from `data/reference/repair_object_problem_database.csv` (copied from the user's folder, unchanged):
  headers `Trade Type, Specific Problem, Problem Complexity, Repair Duration (min)`; UTF-8 no BOM; comma; 46 valid rows,
  0 duplicates, 0 errors; 10 trades; complexity 1–5; durations 20/35/50/75/105. Stable ids `cat_<sha1[:12]>`, file hash,
  catalog version, source row persisted. BOM/quotes/Chinese aliases/duplicate/conflict/invalid-row handling unit-tested.
- SQLite schema (17 tables), WAL; ClockProvider (scenario day 2026-09-15, start 08:30 SGT).

## Phase 2 — Normal scheduling ✅
- Shared `ScheduleSimulator` (JIT departure, breaks/unavailability, pinned starts, recovery targets) and independent
  `ConstraintValidator`; insertion + bounded relocate/reorder solver with 3 deduplicated strategies and budget statuses;
  scoring per `docs/scoring.md`; PolicyEngine; initial batch scheduling with review-on-any-low-score.
- Seed `main`: 8 technicians / 20 orders / 2 locked tasks; baseline committed as a schedule version.

## Phase 3 — Risk & cancellation ✅
- Risk rules (overdue, verified lateness, cancellation 30/120 boundaries, predicted late, approaching, unassigned ETA
  unknown); effective priority with paid floor; risk events with stable keys, `last_seen` observation only.
- Technician unavailability (facts first, per-order classification, execution interruption → manual, ordered recovery,
  multi-order combos forced to review); customer cancel (atomic release, plan invalidation, successor recomputation,
  departed/foreign-session refusal, idempotency); execution events and minute-by-minute clock advance.

## Phase 4 — Constrained rescheduling ✅
- Authority checks (movable priorities, max affected, departed lock), affected-set semantics (§10.1), over-limit plans as
  explanation-only alerts, candidate persistence with base versions/expiry, approval re-validation in-transaction (409
  with structured codes), reject, recompute, sibling supersession.

## Phase 5 — Agents & UI ✅
- UnderstandingAgent (mock rule/alias matcher EN+ZH; Anthropic structured-output adapter ⚠️ untested live; degradation
  labelled), chat service (slot filling, confirmation card, simulated payment, status/ETA, cancel, complaint), AgentRun traces.
- Dashboard: clock controls, KPIs, technician timeline (travel/wait/service/break/unavailable/locked), Leaflet map with
  schematic fallback, orders table + detail drawer (fixed params, reasons, execution panel, standby), risks, review queue
  with plan cards/diff tables/approve-reject-recompute, agent activity, notifications, versions, event injector.
- Customer page: chat with option chips, catalog browser, session-scoped orders with cancel/expedite/complaint.

## Phase 6 — Verification & delivery ✅ (with the ⚠️ items listed below)
- Evaluation harness (baseline vs proposed, 10 seeds) → `docs/evaluation.md`, `data/evaluation/`.
- Docs: README, architecture, data-contracts, scoring, decisions, demo, evaluation. Scripts: setup/dev/tests/reset/e2e.

## Test & check runs (latest, 2026-09-15)
| Check | Result |
|---|---|
| `pytest` (backend/tests, 11 files) | **64 passed** (catalog, time/routes, priority/policy/authority/scores, DB flows, API contract, standby, OpenAI-compat provider, OSRM adapter + point resolution — all offline) |
| `ruff check app tests` | all checks passed |
| `mypy app` | no issues (66 files) |
| `tsc -b` + `vite build` | pass |
| Browser main flow `scripts/e2e/run.sh` (Playwright, headless Chromium) | **15/15 checks pass**, 0 console/page errors, under OSRM public routing + DeepSeek (also under fixture/mock): reset → chat P3 auto → chat paid P1 (≤2 affected P3 auto) → chat lock order → technician cancel <30 min → P0 (P2 siblings zero-disturbance auto) → P0 plan with 1 affected P3 → manual → approve → moved order keeps its window → cancel undeparted / refuse departed → new order fills gap → reload persists |
| Clock Run/Pause via background thread | verified (4 real seconds → +4 sim minutes) |
| Evaluation run (10 seeds, 61 events) | 0 hard/authority violations both strategies; see `docs/evaluation.md` |

## Not verified / known limitations
- **Real LLM — verified**: `LLM_PROVIDER=openai_compat` with DeepSeek `deepseek-ai/DeepSeek-V4.1-Flash` via ModelScope was called
  live from the real code path (interpret EN/ZH, complaint classification, chatbot turn → confirmation card); 4–19 s per turn.
  The `anthropic` provider is code-complete but not called live. **Real OSRM**: adapter + mocked-HTTP handling only.
- **Docker / compose**: not run (no Docker on the machine).
- Single process only; locations restricted to presets (map click snaps); no half-way rerouting; no cross-execution rollback
  (history versions only); pending candidates are regenerated when they expire (30 sim minutes) or the schedule changes.
- The `main` scenario's P0 review case is "overdue + zero affected + low score → manual"; a P0 that must move 1–5 P2/P3
  orders is covered by unit tests but not by a hand-crafted seed order.

## Follow-up changes
- Vite bound to `127.0.0.1` (default bind was IPv6-only, `http://127.0.0.1:5174` failed).
- `openai_compat` LLM provider added and verified live with DeepSeek/ModelScope; `.env` now runs `LLM_MODE=real`; the key lives only in the gitignored `.env` (and in the user's `backend/api_test.py`, also gitignored).

- Chat turn logic: the model now receives `already_collected` slots + sim time/day and is told not to re-ask; the server
  acknowledges what a turn captured and asks exactly one next question; past times are explained instead of dropped
  (reported by the user as mismatched questions/options with the real model).
- Chat UI renders the customer's message optimistically (~80 ms) with a thinking bubble while the model runs; stale options/card hidden meanwhile.
- Run `scripts/e2e/run.sh` with `LLM_MODE=mock` (its waits assume sub-second understanding).

- **Real routing (option B) — verified live**: `ROUTE_MODE=osrm` against `https://router.project-osrm.org` (public demo):
  18-point table in one call, real road polylines (≈200 points/leg), Ubi→Tampines 9.8 min/7.4 km free-flow (fixture said 20);
  baseline schedule total travel 297 min vs 349 with fixture. Disk cache for matrices, in-memory cache for geometry,
  degradation to `estimated`. Dashboard map draws road geometry per leg; customers can pin an exact point on the map
  (`POST /api/locations/resolve`, chat option `map_pick`); browser-checked: pinned order near Changi assigned with 38 min
  real travel. Self-hosted OSRM is documented in README (not run here: no Docker).
- Tests force `ROUTE_MODE=fixture`; 56 tests pass (OSRM adapter with mocked HTTP, point resolution/snapping).

- Review-queue redesign (single column, comparison table, wider panel); over-limit plans superseded once the target commits;
  same-event stale-plan recompute + UI auto-recompute on stale approve. New S4 demo path: technician cancels <30 min before
  the deadline → P0 → plan moves one P3 → manual → approve (browser E2E 15/15 under OSRM + DeepSeek, 0 console errors).

- **Geocoding (option C)**: OneMap adapter — **verified live with the user's OneMap account** (block address + unit number,
  Chinese "邮编 520123", landmark); query normalization (Blk/Block/unit numbers stripped), postal codes queried alone,
  chain continues to Nominatim on zero results; explicit clock times now beat day-part defaults ("下午4点" → 16:00);
  user-reported "B210A clementi ave 6" (B glued to the block number) now normalized for every provider → exact OneMap hit,
  and an area-name fallback is explained to the customer instead of silently using the nearest preset — and
  Nominatim adapter (verified live), chain + disk cache, UnderstandingAgent address resolution (exact / candidates / fallback),
  chat candidate buttons, mock address extraction. Live chatbot run with DeepSeek + Nominatim + OSRM: landmark → exact point,
  street → 4 candidates → pick → confirmation card. 61 tests pass.

- Event injector: optional "unavailable until HH:MM"; same-event move accounting refined (only a second mover is forced
  to review); an in-place recovery with no earlier option is `no_action` instead of an unresolved alert. Verified P1-by-
  cancellation (105 min → auto, 1 P3 moved) and P0-overdue (unresolved → breach 12:01 → auto recovery 13:10) walkthroughs.

## Bugs found & fixed during verification
- Event-loop deadlock: the background loop waited for the process lock inside the event loop while a yield-dependency
  released it only after the response → moved the loop to a worker thread and made mutating endpoints own lock+session.
- Candidate rows stored only tech/time-changed assignments, dropping travel-only successor updates → approval failed
  (`origin_mismatch`); now every modified row is stored.
- Approval staleness looked at all technician versions (execution events elsewhere expired every plan); narrowed to the
  involved orders, with validation re-checking technician facts.
- Scoring calibration: zero-disturbance plans lost to marginal travel gains; stability scale and technician-change penalty adjusted.
- Time-hint parsing ignored minutes and ranges ("between 9:30 and 9:50"); replaced.


## V3 upgrade (third meeting, 2026-09-16) ✅

Implemented in the order recommended by the brief (§19). Details: `docs/v3-change-summary.md`, tools: `docs/v3-agent-tools.md`,
demo: `docs/v3-demo-guide.md`, decisions: `docs/decisions.md` (V3 table), numbers: `docs/evaluation.md` (V3 section).

- **Phase A — data/state foundation ✅**: additive migration (`app/db/migrations.py`, `schema_migrations`), live DB backed up
  (`data/app.db.pre-v3.bak`); 15 new tables (customers, addresses, execution events, service reports, feedback, human cases,
  safety incidents, agent tasks, tool traces, break blocks, customer questions, reschedule requests, duration observations,
  model versions); error `kind` vocabulary; policy additions (breaks / agent budgets / emergency channels / duration prediction).
- **Phase B — three-end loop ✅**: customer app (`/customer`, identity selector, catalog-only suggestions, address modal with
  OneMap search / postal / map pin + mandatory unit, feasible windows, handoff, safety entry point, open questions) and order
  page (`/customer/orders/:id`: cancel / idempotent expedite / complaint / reschedule / handoff replies / rating / tracking);
  technician app (`/technician`: manual/auto, depart→complete, leave, rest, service report, history hint, route map);
  dispatcher: 4 KPIs, human queue (take/reply/resolve, incidents), agent activity (tasks + phases), live positions with
  status legend, Demo-controls drawer (scenarios + load summary + event injector), Dev drawer (runtime, duration report, traces).
- **Phase C — tools & agent behaviour ✅**: 18 tools, `ToolResult` + reason codes, role/arg gating, `AgentTask`/`ToolTrace`,
  budgets 12/3/2, wake-ups, MockPolicy + ModelPolicy (DeepSeek). Live: 5 model-driven tasks on `scarce`, 4–5 calls each,
  all evidenced escalations, none degraded.
- **Phase D — rest, duration, scenarios, tests, docs ✅**: dynamic rest (`break_service`, break tasks, declared rest);
  execution facts + anomalies; duration observations + shadow model + report; scenarios main / relaxed / scarce with
  synthetic customer history; emergency numbers verified against gov.sg / cityenergy.com.sg; V3 offline evaluation
  (fast path vs agent, fixed lunch vs dynamic rest, per scenario).

### Test & check runs (latest, 2026-09-16)
- `pytest`: **90 passed** (26 new in `tests/test_v3.py`); `ruff` clean; `mypy` clean (86 files); `tsc -b` clean.
- Browser E2E `scripts/e2e/run.sh` (three ends, customer/technician at 400 px, real DeepSeek + OSRM + OneMap): **21/21 checks, 0 console errors**;
  screenshots in `data/evaluation/screenshots/` (01…10).
- Offline V3 evaluation: `data/evaluation/latest_v3.json` (10 seeds × 3 scenarios).

### Not verified / known limitations (V3)
- Docker still not run here (no Docker on the machine).
- Positions are simulated from route geometry + sim clock; no GPS. Notifications, payments and dialling are simulated.
- The shadow duration model only has synthetic + simulated observations; MAE numbers describe the demo data, nothing else.
- `safety_concern` from the real model is best-effort; the deterministic triggers are what the demo relies on.
- Human-case `created_at` is wall-clock UTC on the dashboard (operational record), while everything else shows sim time.

### Bugs found & fixed during V3 verification
- Customer-history orders (earlier days) leaked into today's snapshot/board (36 orders instead of 20) → excluded by `window_end < day_origin`.
- Repeat-fault hint matched other customers' orders at the same preset area (Bedok Mall) and today's open orders → completed
  orders of the same customer or the same precise point only.
- Unit numbers were stored with the typed `#` and rendered as `##05-123` → normalised on save.
- Leaflet `_leaflet_pos` errors when a map unmounted during a pending `invalidateSize`/animation (mobile pages navigate fast) → timers cleared, `map.stop()` before `remove()`.
- The model wrote a priority (`P3`) into `flag_for_human.urgency` → normalised to the urgency scale inside the tool.
- Unresolved orders created a new agent task on every schedule-version bump while a human was already on the case → one live task per order.
- Policy-required cases did not exist (review queue and human queue disagreed) → `plan_approval` and `execution_interrupted` cases, auto-closed on approve/reject.

### 2026-09-17 follow-ups (user requests)
- **Reset demo** button on the dispatcher top bar: one click reloads scenario main at 08:30 and clears every table; a
  `localStorage` broadcast makes open customer / technician tabs drop their session / reload (customer tabs also detect a
  changed scenario generation on their own). Verified in the browser: customer session replaced and chat emptied,
  technician back to auto mode; E2E now uses this button (21/21, 0 console errors).
- **Continuous technician motion**: markers persist across polls (`setLatLng` + `transition: transform 1.5s linear`,
  disabled during Leaflet zoom animation), positions polled every 1.5 s; the customer tracking marker gets the same
  treatment and the map fits once instead of re-centering on every poll. Sampled at 250 ms while the clock runs:
  32/32 distinct positions, ≤ 3.6 px per sample (was one jump every 2 s).
- **Fix: UI froze after loading `scarce` (real model)**. Cause: the agent worker ran a whole model-driven task under
  the global state lock and on the clock tick; the chat endpoint also called the model under the lock. `scarce` has 7
  unassignable orders → 7 real tasks × 4–5 DeepSeek calls → the lock was held for minutes, so Load / +1m / Run / chat
  hung. Fix: model decisions outside the lock, one short locked step per tool call, dedicated worker thread; chat model
  work pre-computed on a read-only session. Measured with 7 real tasks queued: `+1m` 30–100 ms, scenario switch 50 ms,
  dashboard action during a chat model call 36 ms. 90 tests, E2E 21/21.
