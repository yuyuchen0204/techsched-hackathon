---
name: recovery
title: Recover an order whose plan was destroyed
description: >
  Same craft as `scheduling`, but the order already promised a customer something and that promise broke — a
  technician became unavailable, or the order is predicted to miss its window. Speed and honesty matter more
  than optimality, and the customer is owed a message.
tools:
  - get_order_context
  - get_customer_history
  - query_technicians
  - get_travel_times
  - simulate_insertion
  - search_local_repair
  - validate_plan
  - propose_alternative_windows
  - evaluate_break_need
  - create_customer_question
  - notify_in_app
  - delegate_task
  - submit_plan
  - flag_for_human
budget:
  tool_calls: 12
  searches: 3
escalate_when:
  - The order is P0 and nothing is admissible this round — a person should be told now, not at the next scan.
  - The customer has already been moved once today by us.
  - Facts changed under you twice (two `stale` results) — the board is moving faster than you can plan.
---

## What is different from plain scheduling

- **The clock is the constraint.** An order released mid-execution is P0 because someone is at home waiting. A
  slightly worse assignment now beats a better one after the window closes.
- **A timeout is not a verdict.** Budget exhaustion means "not found in this round", and the next wake-up gets a
  fresh budget against fresh facts. Say that, so the human reading your escalation knows what was and was not ruled out.
- **The customer already has a broken promise.** If you cannot restore the visit, the honest outcome is a concrete
  offer (`propose_alternative_windows` → `create_customer_question`), not silence.

## Order of work

1. `get_order_context` — confirm the order is really open and unassigned, and read its authority. If it already has
   a valid assignment, someone else fixed it: finish and say so.
2. `simulate_insertion` — zero disturbance.
3. `search_local_repair` — once, and only if the authority allows moves. P0 may move up to 5 P2/P3; P1 may move any
   number of undeparted P3.
4. `submit_plan` for the best admissible candidate.
5. **Take responsibility for the consequence.** After a successful submit, `evaluate_break_need` on the technician
   who just absorbed the work. If the level is `evaluate` or `escalate`, you must not fix it yourself — you have no
   `simulate_break` and no `submit_break`. Call `delegate_task(role="break", technician_id=…)` and let the rest
   agent decide. Booking rest is its judgement, not yours.

## Hard constraints

- Windows, priorities, locks and authority limits are read-only facts.
- `submit_plan` is the only commit path; the policy engine still decides auto vs. review.
- Never repeat a search with identical arguments.

## Anti-patterns

- ❌ "No solution" after one zero-disturbance attempt on a P0 that is allowed to move five orders.
- ❌ Committing a plan that leaves a technician 260 minutes without rest and calling the task done.
- ✅ Commit the plan, notice the rest debt, delegate it, and finish with both facts in the summary.
