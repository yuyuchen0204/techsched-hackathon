> V3: the three-end demo script is `docs/v3-demo-guide.md` (中文); Inject Event and scenario loading now live in the dashboard's Demo-controls drawer.

# Demo script

Start both servers (`scripts/dev.sh`), open the dashboard (`/`) and the chatbot (`/customer`) in two tabs. The sim clock
starts **paused at 08:30**; nothing moves until you press `+1m/+5m/+15m` or `Run`. Every step below was executed by
`scripts/e2e/run.sh` (Playwright) under the shipped configuration (**OSRM public routing + DeepSeek via ModelScope**); it also
passes with `ROUTE_MODE=fixture` / `LLM_MODE=mock`. Exact minutes and travel numbers differ between route modes; the
decisions (auto / manual / forbidden) follow the same rules.

## Main line (~3 minutes)

| # | Action | What to point at | Verify (API/UI) |
|---|---|---|---|
| 0 | Dashboard → **Reset** | 8 technicians, 20 orders, baseline schedule v2; `wo_001` EN_ROUTE and `wo_002` IN_PROGRESS shown with 🔒; catalog badge "46 items" | `/health` `catalog_items=46` |
| 1 | Chatbot: *"My fridge is making a loud noise, I am at Hougang, please come around 2pm"* → fill name/phone → **Confirm** | UnderstandingAgent maps to *Refrigerator – Unusual noise (complexity 2, 35 min)*, location Hougang, window 14:00–15:30 from the text; confirmation card; reply shows technician & start | order P3 `ASSIGNED`, dashboard: new task in Bala's row, **no other task moved** |
| 2 | Chatbot (New customer): *"Aircon not cooling at all in Paya Lebar, need someone between 9:30 and 9:50, my name is Priya, phone 91110002"* → **Simulate expedite payment** → **Confirm** | card shows *P1 (paid expedite, simulated)*; after submit open the order: plan **committed affecting N undeparted P3** (no cap for P1 since 2026-09-18; exact count depends on the route mode) | Agent activity → steps: load_snapshot → classify_priority → solve → validate_score_policy → commit |
| 3 | Chatbot (New customer): *"My door lock is damaged and needs replacement, Punggol, between 10:30 and 10:40, my name is Lee Ann phone 91110004"* → **Confirm** | P3 assigned to Farah (tech_06); her timeline shows the block with departure ≈10:17 | order `ASSIGNED`, technician `tech_06` |
| 4 | Dashboard: advance to **one minute before Farah's departure** (`+15m` ×7 → 10:15, `+1m` → 10:16) → **Inject Event → technician_unavailable → tech_06** | Farah's row turns red. Released orders are classified per remaining time: the lock order has **<30 min → P0**; `wo_015`/`wo_019` (>120 min → P2) are re-dispatched with **zero disturbance** automatically. The P0's only plan moves one P3 (`wo_012` to another technician) → **manual by rule** even though its score is > 70. Review queue shows the card with the diff table; hover it to highlight the affected order on the map | risks: `TECHNICIAN_CANCELLED … P0`; KPI "Pending review 1" |
| 5 | **Approve & commit** | schedule → new version; the P0 order starts 10:36 (inside 10:30–10:40) with Devi; `wo_012` now with Hana, still inside its own window; both customers get simulated ETA notifications | `/api/orders/<lock>` `ASSIGNED`; `/api/orders/wo_012` window kept |
| 6 | Click an undeparted order (e.g. `wo_020`) → **Cancel order**; try cancelling an IN_PROGRESS one | first: CANCELLED, technician released, successors recomputed from the real predecessor; second: refused *"technician already in progress"* | `409 already_departed` |
| 7 | Chatbot (New customer): same trade/location/window as the cancelled order → Confirm | new order lands in the freed capacity | `ASSIGNED` |
| 8 | Reload the dashboard | everything is still there (DB-backed) | schedule version unchanged |

## Supplementary scenarios

- **S5 P2 with a valid assignment** (verified): at 08:30, chatbot: *"aircon remote not working, Tampines, between 9:00 and 9:45, my name is Kim phone 90002222"* → Confirm → Bala starts 09:35 (after `wo_003`). `+15m` ×3 (→09:15, 30 min to the deadline, Bala not yet departed) → risk `APPROACHING_DEADLINE P2`, order stays with Bala (**no technician change**); order detail → *P2 standby* panel. In this instance the list is honestly empty (no other qualified technician can reach Tampines by 09:45 without moving someone else) — the panel says so instead of inventing candidates; `backend/tests/test_standby.py` shows the list filling when alternatives exist.
- **S5 P2 without a valid assignment**: technician cancel > 120 min before a deadline (step 4 above, `wo_015`/`wo_019`) → zero-disturbance re-dispatch; if no free qualified slot exists → *UNRESOLVED / awaiting recovery*, never a forced move (with fixture routes, injecting tech_06 at 09:00 leaves `wo_007` unresolved until it turns overdue → P0 → low-score zero-affected plan → manual review; with OSRM routes the same case auto-resolves because travel is shorter).
- **S6 approval cannot revive a cancelled order**: create a low-score order (far location), cancel it while its plan is pending → plan becomes INVALIDATED; approve → `409`.
- **Lateness complaint**: chatbot → "your technician is late" → verified against clock/window/`service_started_at`; before the deadline it is *not* escalated; after → P0.
- **Non-scheduling complaint**: "the technician was rude" → manual service queue, priority unchanged, no solver run.
- **Execution interruption**: technician_unavailable for a technician that is EN_ROUTE/IN_PROGRESS → `EXECUTION_INTERRUPTED` risk (manual), lock not released, no auto reassignment.
- **Generate Schedule**: after adding orders via `POST /api/orders {dispatch:false}`, the button runs the initial batch; a batch containing any score ≤ 70 goes to review as a whole.

## Edge cases (covered by tests, `backend/tests`)
P1 moving any number of P3 → allowed (no cap) · P1 moving a P2 → forbidden · P0 moving P0/P1 → forbidden · P0 > 5 → over-limit alert ·
skill insufficient → no candidate even with perfect travel · score exactly 70 → manual · 1–2 candidates → no fake third ·
overdue recovery keeps the breach record · reset invalidates old results · cancel/depart race → single outcome.
