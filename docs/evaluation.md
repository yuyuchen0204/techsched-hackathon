# Evaluation

Offline harness: `backend/app/services/evaluation_service.py` (`POST /api/evaluations`, or
`backend/.venv/bin/python -c "from app.services.evaluation_service import run_evaluation; run_evaluation()"`).
Outputs: `data/evaluation/eval_<stamp>.json`, `eval_<stamp>_events.csv`, `latest.json`.

## Method
- 10 fixed seeds; each seed: 18 synthetic P3 orders built from the **real catalog** (trades covered by the 8 scenario
  technicians), fixture locations/matrix, windows 60–120 min between 09:00 and 17:00. `solve_initial` produces the committed
  base plan (same for both strategies).
- Event sequence per seed (identical for both strategies, same clock): 3 P3 inserts at 08:45, a paid P1 with a 30-minute
  window at 08:50, one technician cancellation at 09:15 whose undeparted orders become recovery targets with the
  cancellation priority (P0/P1/P2 by remaining minutes). Time advances between events; departed tasks are locked and
  finished tasks completed exactly as the live clock does.
- **Baseline — Nearest Feasible**: among all qualified technicians and insertion positions, the zero-disturbance,
  fully validated insertion with the smallest inbound travel (no relocations, no scoring, no policy).
- **Proposed**: the system's `solve_insert` (insertion + bounded relocate/reorder under P1/P0 authority), scored,
  PolicyEngine applied; plans marked *manual* are counted as approved in the offline run (the dispatcher would see them).
- Both strategies obey skills, windows, breaks, shift end, locks and successor reachability; the proposed strategy simply
  has a larger action space. This compares system capability, not just weights.

## Results (run on 2026-09-15, policy 2026-09-14-v2, fixture routes)

| Metric | Baseline (nearest feasible) | Proposed |
|---|---:|---:|
| events (61 = 10 seeds × 6.1) | 61 | 61 |
| assignment rate | 0.574 | **0.639** |
| predicted on-window start rate (unassigned = missed) | 0.574 | **0.639** |
| urgent (P0/P1) events / unserved | 10 / 6 | 10 / **3** |
| urgent response, mean minutes after ready time (served only) | 2 | 0 |
| added travel, total minutes | 1006 | 1121 |
| affected existing orders (total) / technician changes / start shift minutes | 0 / 0 / 0 | 6 / 3 / 205 |
| hard-constraint or authority violations in committed plans | 0 | 0 |
| solve time mean / max (ms) | 0 / 0 | 0.9 / 17 |
| decisions | assign 35 · unresolved 26 | auto 28 · manual 11 · unresolved 22 |
| unassigned by priority | P1 6 · P2 10 · P3 10 | P1 3 · P2 9 · P3 10 |

Per seed (assigned / events): 1: 4/6 → 5/6 · 2: 3/4 → 3/4 · 3: 6/7 → 6/7 · 4: 3/7 → 5/7 · 5: 3/5 → 3/5 · 6: 3/7 → 3/7 ·
7: 4/6 → 4/6 · 8: 2/6 → 2/6 · 9: 2/6 → 3/6 · 10: 5/7 → 5/7. Initial scheduling: all seeds `partial` (1–4 of 18 unplaceable
by construction), 5–9 ms each.

## Reading the numbers honestly
- The gain is where authority exists: **P1 unserved drops from 6 to 3** because the proposed strategy may shift/relocate up
  to two undeparted P3s. P2 and P3 events are identical by rule (zero disturbance) — the small P2 difference comes from
  scoring choosing a slot that leaves room for a later recovery.
- The price is visible: +115 travel minutes and 6 affected customers (3 technician changes, 205 minutes of start shifts).
  This is the response-vs-stability trade-off the brief asks to expose, not a free improvement.
- 22–26 of 61 events stay unresolved in both strategies: synthetic days are deliberately dense and cancellation recoveries
  with >120 min left are P2 (no disturbance allowed). Failures are kept in the denominator.
- Everything is synthetic (locations, matrix, demand). No claim of real-world savings, academic novelty, or measured
  business impact. Budgets (3 s initial / 5 s repair) are far from binding here (max 17 ms); larger instances were not measured.
- Not measured: real LLM latency (mock only), real OSRM latency, multi-day horizons.

---

## V3 (§18.4) — orchestration and rest, per load scenario

Harness: `run_v3_evaluation()` in `backend/app/services/evaluation_service.py` (`POST /api/evaluations/v3`); output
`data/evaluation/latest_v3.json`. Run on 2026-09-16, policy `2026-09-16-v3`, fixture routes, 10 seeds per scenario,
worlds sized like the scenarios (main 20, relaxed 12, scarce 32 orders; scarce has two technicians on leave).

**Same worlds, events, policy, solver and budgets; only the orchestration of `UNRESOLVED` differs.**
`fast_path` = V2 pipeline. `agent` = fast path, then the bounded investigation the runtime performs (≤ 3 plan searches):
zero-disturbance trials over later 90-minute windows; the earliest feasible one is taken **as if the customer accepted it**
(assumption, reported as `served_in_alt_window`, *not* counted as on-window); nothing feasible → human escalation.
All numbers are per-seed means.

| scenario | strategy | assignment rate | on-window (original) | urgent unserved | affected | served in alt window | human escalations | tool calls | plan searches | processing ms | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | fast_path | 0.655 | 0.655 | 0.4 | 0.4 | – | – | 0 | 0 | 0 | 0 |
| main | agent | 0.781 | 0.655 | 0.2 | 0.6 | 0.8 | 1.3 | 10.6 | 5.1 | 14.4 | 0 |
| relaxed | fast_path | 0.889 | 0.889 | 0.1 | 0.3 | – | – | 0 | 0 | 0 | 0 |
| relaxed | agent | 0.926 | 0.889 | 0.0 | 0.3 | 0.2 | 0.4 | 3.0 | 1.4 | 4.2 | 0 |
| scarce | fast_path | 0.312 | 0.312 | 0.9 | 0.1 | – | – | 0 | 0 | 0 | 0 |
| scarce | agent | 0.518 | 0.312 | 0.6 | 0.6 | 1.1 | 3.3 | 23.6 | 11.5 | 37.3 | 0 |

Reading: the agent never improves the *original-window* rate — it cannot, because the fast path already exhausts the
authority-bounded search. What it adds is a **bounded, evidenced next step**: an alternative window the customer can
accept, or a human case with the attempted actions. The cost is visible (tool calls, searches, milliseconds) and grows
with scarcity; hard-constraint / authority violations stay at zero in every committed plan. More calls are not a better
result: in `scarce` most investigations end in an escalation, which is the intended behaviour when resources are missing.

**Rest — fixed lunch vs dynamic** (initial schedule of the same worlds; dynamic rest measured as an idle gap ≥ 30 min
between 180 and 240 cumulative work minutes):

| scenario | mode | initial unassigned | utilisation mean / stdev | travel total | no rest needed | rest slot found | rest escalations |
|---|---|---|---|---|---|---|---|
| main | fixed_lunch | 2.7 | 0.294 / 0.156 | 477 | 4.0 | 3.5 | 0.5 |
| main | dynamic | 1.4 | 0.281 / 0.156 | 513 | 4.8 | 2.6 | 0.6 |
| relaxed | fixed_lunch | 1.5 | 0.180 / 0.130 | 271 | 6.4 | 1.5 | 0.1 |
| relaxed | dynamic | 0.8 | 0.164 / 0.126 | 286 | 6.5 | 1.5 | 0.0 |
| scarce | fixed_lunch | 9.2 | 0.385 / 0.251 | 641 | 2.3 | 3.2 | 2.5 |
| scarce | dynamic | 6.6 | 0.382 / 0.256 | 710 | 2.4 | 3.7 | 1.9 |

Reading: removing the uniform 12:00–13:00 block leaves more orders placeable at seed (fewer initial unassigned) at the
price of more travel; whether a technician then actually gets a rest depends on the day — in `main` the proxy finds a
slot for 2.6 of the ~3.2 technicians who need one, and 0.6 per day would reach the escalation threshold without a
zero-disturbance slot (these become high-priority human items in the live system). The live implementation is stricter
than this proxy (it re-validates route feasibility and successor windows), so the proxy is an upper bound on slots found.

Live agent behaviour (real model, `scarce`, 2026-09-16): 5 model-driven tasks for unassignable orders, 4–5 tool calls
and 2–3 searches each, all ending in an evidenced human case (`no_admissible_slot`) with the feasible later windows
attached; no loops, no budget exhaustion, no degraded runs.
