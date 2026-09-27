"""Post-order expedite = simulated payment + automatic move to the earliest start, and its revert ("Keep original time").

Nothing here changes the rules: the order becomes P1 through the existing paid-expedite event, the solver runs under the
P1 authority (any number of undeparted P3 orders may move, each inside its own window), the PolicyEngine decides auto vs review with
the usual threshold, commits go through commit_plan / approve_plan, and every message is an in-app simulated notification.
The only additions are the search for the EARLIEST start (the customer accepts any start from now on: the solver's urgent
front-insertion sends the technician who can reach the customer soonest and re-homes the P3 jobs that no longer fit) and the
window update that follows a move (same length, starting at the new planned start)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.scheduling_agent import explain_candidate
from app.config import get_policy
from app.models.entities import CandidatePlan, WorkOrder
from app.models.enums import LifecycleStatus, PlanStatus, PolicyDecision, Priority, RunStatus
from app.orchestration.orchestrator import dispatch_key, dispatch_order
from app.scheduling.domain import Snapshot
from app.scheduling.policy import decide
from app.scheduling.solver import Candidate, SolveResult, solve_insert
from app.services import human_service, plan_service
from app.services.agent_runs import start_run
from app.services.clock import Clock
from app.services.event_service import _done, _existing, _record
from app.services.notification_service import notify
from app.services.order_service import OrderError, set_paid_expedite
from app.services.schedule_service import commit_plan
from app.services.snapshot import active_schedule_version, build_snapshot
from app.services.timeutil import hhmm, iso, parse_iso

DEPARTED_MESSAGE = "Expedite is only possible before the technician departs."
ALREADY_MESSAGE = "Already expedited — no second charge."
PREVIEW_BUDGET_MS = 1500


# ------------------------------------------------------------------ search (read-only)
def _trial(snap: Snapshot, order_id: str, *, window_start: int, window_end: int, priority: Priority) -> Snapshot:
    spec = replace(snap.orders[order_id], window_start=window_start, window_end=window_end, priority=priority)
    return replace(snap, orders={**snap.orders, order_id: spec}, assignments=dict(snap.assignments))


def _search_earlier(snap: Snapshot, order_id: str, priority: Priority, *, budget_ms: int | None = None) -> tuple[SolveResult, Candidate | None]:
    """Earliest reachable start for the order: the customer accepts any start from now until the original deadline.
    Candidates are ranked by start time first (whoever can arrive soonest), then by how few orders move."""
    spec = snap.orders[order_id]
    trial = _trial(snap, order_id, window_start=min(spec.window_start, snap.now), window_end=spec.window_end, priority=priority)
    res = solve_insert(trial, trial.orders[order_id], get_policy(), allow_relocate=True, budget_ms=budget_ms)
    cands = sorted(res.candidates, key=lambda c: (c.target_start, c.affected.count, -(c.decision_score or 0)))
    return res, cands[0] if cands else None


def _search_original(snap: Snapshot, order_id: str, priority: Priority, ws: int, we: int, *, budget_ms: int | None = None) -> tuple[SolveResult, Candidate | None]:
    """A start inside the original window again; zero-disturbance first, then bounded P1 moves."""
    trial = _trial(snap, order_id, window_start=ws, window_end=we, priority=priority)
    res = solve_insert(trial, trial.orders[order_id], get_policy(), allow_relocate=True, budget_ms=budget_ms)
    cands = sorted(res.candidates, key=lambda c: (c.affected.count, -(c.decision_score or 0), c.target_start))
    return res, cands[0] if cands else None


def preview(db: Session, clock: Clock, order: WorkOrder) -> dict[str, Any]:
    """Read-only trial for the order page / chat: what would expediting do right now? Never commits or notifies."""
    if order.lifecycle_status != LifecycleStatus.OPEN:
        return {"order_id": order.id, "possible": False, "reason": DEPARTED_MESSAGE, "earliest_start": None, "affected_count": 0,
                "current_start": None, "text": DEPARTED_MESSAGE}
    snap = build_snapshot(db, clock)
    existing = snap.assignments.get(order.id)
    current = clock.from_minutes(existing.service_start) if existing else None
    if order.id not in snap.orders:
        return {"order_id": order.id, "possible": False, "reason": "order not in today's schedule", "earliest_start": None, "affected_count": 0,
                "current_start": iso(current), "text": "Expedite raises priority; no earlier slot is available today."}
    priority = Priority.most_urgent(Priority(get_policy().paid_base), Priority(order.risk_priority))  # what the order becomes when paid
    res, cand = _search_earlier(snap, order.id, priority, budget_ms=PREVIEW_BUDGET_MS)
    earlier = cand is not None and (existing is None or cand.target_start < existing.service_start)
    if not earlier:
        return {"order_id": order.id, "possible": True, "earliest_start": None, "affected_count": 0, "current_start": iso(current),
                "decision": None, "text": "Expedite raises priority; no earlier slot is available today."}
    assert cand is not None
    n = cand.affected.count
    outcome = decide(target_priority=priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                     authority=cand.authority, policy=get_policy(), has_changes=bool(cand.changed_ids), search_incomplete=res.search_incomplete)
    start = clock.from_minutes(cand.target_start)
    return {"order_id": order.id, "possible": True, "earliest_start": iso(start), "affected_count": n, "current_start": iso(current),
            "technician_id": cand.plan[order.id].tech_id, "technician_name": snap.techs[cand.plan[order.id].tech_id].name,
            "decision": outcome.decision.value, "decision_score": cand.decision_score,
            "text": f"Earliest possible start after expedite: {hhmm(start)} (moves {n} other appointment{'s' if n != 1 else ''})"
                    + (" · dispatcher confirmation needed" if outcome.decision != PolicyDecision.AUTO else "")}


# ------------------------------------------------------------------ state helpers
def state_view(order: WorkOrder) -> dict[str, Any] | None:
    """Customer-facing summary of the expedite state (order list, order page, chat)."""
    st = dict(order.expedite_state or {})
    if not st and not order.paid_expedite:
        return None
    status = st.get("status") or "unchanged"
    # Customer-facing: the internal P-grade is deliberately not spelled out here (see the dispatcher board for it).
    if status == "moved" and st.get("original_planned_start") and st.get("new_planned_start"):
        summary = f"Expedited · moved from {hhmm(parse_iso(st['original_planned_start']))} to {hhmm(parse_iso(st['new_planned_start']))}"
    elif status == "moved":
        summary = f"Expedited · scheduled at {hhmm(parse_iso(st['new_planned_start']))}" if st.get("new_planned_start") else "Expedited"
    elif status == "pending_review":
        summary = (f"Expedited · earlier slot {hhmm(parse_iso(st['proposed_start']))} awaiting dispatcher confirmation" if st.get("proposed_start")
                   else "Expedited · technician awaiting dispatcher confirmation")
    elif status == "revert_pending":
        summary = "Expedited · restoring the original time (awaiting dispatcher confirmation)"
    elif status == "reverted":
        summary = "Expedited · original time kept"
    else:
        summary = "Expedited · time unchanged"
    return {**st, "status": status, "summary": summary}


def _set_state(order: WorkOrder, **fields: Any) -> None:
    order.expedite_state = {**(order.expedite_state or {}), **fields}


def _customer_message(db: Session, clock: Clock, order: WorkOrder, text: str, kind: str, dedupe: str | None = None) -> None:
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type=kind, order_id=order.id, message=text, dedupe_key=dedupe)
    _set_state(order, message=text, message_at=iso(clock.now))


def _notify_moves(db: Session, clock: Clock, snap: Snapshot, cand: Candidate, order: WorkOrder, *, target_note: str) -> None:
    """Moved customers keep their window (existing rule); technicians see the change on their current/next job."""
    target = cand.plan.get(order.id)
    version_tag = snap.schedule_version
    if target is not None:
        notify(db, clock, recipient_ref=target.tech_id, recipient_type="technician", type="schedule_update", order_id=order.id,
               message=f"Schedule updated: {order.id} moved to {hhmm(clock.from_minutes(target.service_start))} ({target_note}).",
               dedupe_key=f"expedite:tech:{order.id}:{target.tech_id}:{target.service_start}:{version_tag}")
        before = snap.assignments.get(order.id)
        if before is not None and before.tech_id != target.tech_id:
            notify(db, clock, recipient_ref=before.tech_id, recipient_type="technician", type="schedule_update", order_id=order.id,
                   message=f"Schedule updated: {order.id} was reassigned to another technician ({target_note}).",
                   dedupe_key=f"expedite:tech:{order.id}:{before.tech_id}:removed:{version_tag}")
    for oid in cand.changed_ids:
        if oid == order.id:
            continue
        a = cand.plan[oid]
        other = db.get(WorkOrder, oid)
        if other is None:
            continue
        notify(db, clock, recipient_ref=other.customer_ref, recipient_type="customer", type="eta_update", order_id=oid,
               message=f"Your appointment was adjusted to {hhmm(clock.from_minutes(a.service_start))}–{hhmm(clock.from_minutes(a.service_end))} "
                       f"to make room for an urgent job; your original window {hhmm(other.window_start)}–{hhmm(other.window_end)} is still respected.",
               dedupe_key=f"expedite:moved:{oid}:{a.tech_id}:{a.service_start}:{version_tag}")
        notify(db, clock, recipient_ref=a.tech_id, recipient_type="technician", type="schedule_update", order_id=oid,
               message=f"Schedule updated: {oid} moved to {hhmm(clock.from_minutes(a.service_start))} (to make room for an expedited job).",
               dedupe_key=f"expedite:tech:{oid}:{a.tech_id}:{a.service_start}:{version_tag}")


def _window_for(order: WorkOrder, clock: Clock, start_minutes: int) -> tuple[datetime, datetime]:
    """New window: starts at the new planned start, keeps the original length."""
    start = clock.from_minutes(start_minutes)
    return start, start + (order.window_end - order.window_start)


# ------------------------------------------------------------------ apply (auto commit / review / unchanged)
def _attempt(db: Session, clock: Clock, order: WorkOrder, *, mode: str, trigger: str, actor: str) -> dict[str, Any]:
    """mode 'earlier': move to the earliest start if it is earlier than the planned one. mode 'restore': back to the
    original window. Returns {"outcome": auto|manual|unchanged|unresolved, "message": customer text, ...}."""
    policy = get_policy()
    snap = build_snapshot(db, clock)
    existing = snap.assignments.get(order.id)
    tracer = start_run(db, agent="Orchestrator", trigger=trigger, scenario_generation=clock.scenario_generation, target_order_id=order.id,
                       input_refs={"order_id": order.id, "order_version": order.version, "mode": mode})
    run = tracer.run
    st = dict(order.expedite_state or {})
    if mode == "earlier":
        if existing is None:
            # no appointment yet: the ordinary P1 dispatch is the earliest option there is
            tracer.finish(RunStatus.COMPLETED, "no assignment; delegated to dispatch", {"decision": "dispatch"})
            outcome = dispatch_order(db, order.id, trigger=trigger)
            after = build_snapshot(db, clock, matrix=snap.matrix)
            a = after.assignments.get(order.id)
            if outcome.decision == PolicyDecision.AUTO.value and a is not None:
                _set_state(order, status="moved", original_window_start=iso(order.window_start), original_window_end=iso(order.window_end),
                           original_planned_start=None, new_planned_start=iso(clock.from_minutes(a.service_start)), moved_at=iso(clock.now),
                           plan_id=outcome.committed_plan_id, affected_order_ids=[])
                msg = (f"Your order is expedited (simulated payment). New appointment: {hhmm(order.window_start)}–{hhmm(order.window_end)}, "
                       f"technician {after.techs[a.tech_id].name}, planned start {hhmm(clock.from_minutes(a.service_start))}.")
            elif outcome.decision == PolicyDecision.MANUAL.value:
                _set_state(order, status="pending_review", proposed_start=None, run_id=outcome.run_id)
                msg = "Your order is expedited. A dispatcher is confirming a technician; we will update you here."
            else:
                _set_state(order, status="unchanged")
                msg = ("Your order is expedited: priority raised, so it will not be displaced by regular orders. "
                       "No technician is available yet — we keep looking and will update you.")
            _customer_message(db, clock, order, msg, "expedite")
            return {"outcome": outcome.decision, "message": msg, "run_id": outcome.run_id, "dispatch": outcome.as_dict()}
        with tracer.step("solve", agent="SchedulingAgent") as step:
            res, cand = _search_earlier(snap, order.id, Priority(order.effective_priority))
            tracer.tool(step, "solve_repair", status=res.status.value, explored=res.explored, elapsed_ms=res.elapsed_ms, candidates=len(res.candidates))
            step["summary"] = f"{res.status.value}: earliest {hhmm(clock.from_minutes(cand.target_start)) if cand else '—'}"
        if cand is None or cand.target_start >= existing.service_start:
            _set_state(order, status="unchanged", checked_at=iso(clock.now))
            order.last_dispatch_key = dispatch_key(order, snap)
            msg = ("Your order is expedited: priority raised, so it will not be displaced by regular orders. "
                   "No earlier technician is available today — your appointment time is unchanged.")
            _customer_message(db, clock, order, msg, "expedite")
            tracer.finish(RunStatus.NO_ACTION, "no earlier start than the planned one", {"decision": "no_action"})
            return {"outcome": "unchanged", "message": msg, "run_id": run.id, "planned_start": iso(clock.from_minutes(existing.service_start))}
        new_ws, new_we = _window_for(order, clock, cand.target_start)
        original = {"original_window_start": st.get("original_window_start") or iso(order.window_start),
                    "original_window_end": st.get("original_window_end") or iso(order.window_end),
                    "original_planned_start": st.get("original_planned_start") or iso(clock.from_minutes(existing.service_start))}
        note = "expedited"
    else:  # restore
        ws_dt, we_dt = parse_iso(st["original_window_start"]), parse_iso(st["original_window_end"])
        with tracer.step("solve", agent="SchedulingAgent") as step:
            res, cand = _search_original(snap, order.id, Priority(order.effective_priority), clock.to_minutes(ws_dt), clock.to_minutes(we_dt))
            tracer.tool(step, "solve_repair", status=res.status.value, explored=res.explored, elapsed_ms=res.elapsed_ms, candidates=len(res.candidates))
            step["summary"] = f"{res.status.value}: {len(res.candidates)} candidate(s) inside the original window"
        if cand is None:
            current = hhmm(clock.from_minutes(existing.service_start)) if existing else "the current time"
            msg = f"The original time is no longer available; your appointment stays at {current}. Priority stays P1."
            _set_state(order, status="moved", restore_note="original window no longer feasible")
            _customer_message(db, clock, order, msg, "expedite_restore")
            tracer.finish(RunStatus.NO_ACTION, "original window no longer feasible", {"decision": "no_action"})
            return {"outcome": "unchanged", "message": msg, "run_id": run.id}
        new_ws, new_we = ws_dt, we_dt
        original = {k: st[k] for k in ("original_window_start", "original_window_end", "original_planned_start") if k in st}
        note = "original time restored"

    priority = Priority(order.effective_priority)
    outcome_obj = decide(target_priority=priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok, authority=cand.authority,
                         policy=policy, has_changes=bool(cand.changed_ids), search_incomplete=res.search_incomplete)
    od = outcome_obj.as_dict()
    target = cand.plan[order.id]
    new_start = clock.from_minutes(target.service_start)
    tech_name = snap.techs[target.tech_id].name
    n = cand.affected.count
    explanation = (f"{'Expedite' if mode == 'earlier' else 'Restore original time'}: {order.id} planned start {hhmm(new_start)}"
                   + (f" (was {hhmm(clock.from_minutes(existing.service_start))})" if existing else "")
                   + f"; window becomes {hhmm(new_ws)}–{hhmm(new_we)}. " + explain_candidate(clock, snap, cand, order.id, od))
    if od["decision"] == PolicyDecision.AUTO.value:
        order.window_start, order.window_end = new_ws, new_we
        plan = plan_service.persist_candidate(db, clock, snap, cand, run_id=run.id, target_id=order.id, target_priority=priority, outcome=od,
                                              status=PlanStatus.PROPOSED, explanation=explanation)
        plan.metrics = {**(plan.metrics or {}), "expedite_window": {"start": iso(new_ws), "end": iso(new_we), "mode": mode, **original}}
        with tracer.step("commit", agent="Orchestrator") as step:
            version = commit_plan(db, clock, snap, cand.plan, reason=f"{trigger} {order.id} ({actor})", plan_id=plan.id,
                                  metrics={**cand.metrics(snap), "expedite": mode})
            tracer.tool(step, "commit_plan", schedule_version=version.id, affected=cand.affected.affected_ids)
            step["summary"] = f"committed schedule version {version.id}"
        plan.status, plan.status_reason = PlanStatus.COMMITTED, "auto-committed by policy"
        plan_service.supersede_siblings(db, plan)
        _finish_move(db, clock, snap, cand, order, mode=mode, plan_id=plan.id, schedule_version=version.id, original=original, note=note, existing_start=existing.service_start if existing else None)
        tracer.finish(RunStatus.COMPLETED, f"auto-committed (score {cand.decision_score:.1f})", {"decision": "auto", "plan_id": plan.id, "schedule_version": version.id})
        return {"outcome": "auto", "message": (order.expedite_state or {}).get("message"), "run_id": run.id, "plan_id": plan.id,
                "schedule_version": version.id, "planned_start": iso(new_start), "affected_count": n, "technician_name": tech_name}

    # review: the current appointment stays until a dispatcher decides
    plan = plan_service.persist_candidate(db, clock, snap, cand, run_id=run.id, target_id=order.id, target_priority=priority, outcome=od,
                                          status=PlanStatus.PENDING_REVIEW, explanation=explanation)
    plan.metrics = {**(plan.metrics or {}), "expedite_window": {"start": iso(new_ws), "end": iso(new_we), "mode": mode, **original}}
    order.pending_plan_run_id = run.id
    order.last_dispatch_key = dispatch_key(order, snap)
    reason = od["reasons"][-1] if od.get("reasons") else "plan needs approval"
    human_service.flag_for_human(db, clock, source="POLICY_REQUIRED", category="plan_approval", urgency="high",
                                 reason_summary=f"{order.id} ({priority.value}, {'expedited' if mode == 'earlier' else 'restore original time'}): "
                                                f"planned start {hhmm(new_start)} needs approval — {reason}",
                                 customer_id=order.customer_id, order_id=order.id, evidence_refs=[f"plan:{plan.id}", f"run:{run.id}"],
                                 suggested_next_action="approve or reject the candidate plan in the review queue")
    notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="expedite_review", order_id=order.id,
           message=f"Expedited order {order.id} needs approval for {'an earlier slot' if mode == 'earlier' else 'restoring its original time'} "
                   f"(moves {n} P3 order{'s' if n != 1 else ''}).", dedupe_key=f"expedite_review:{order.id}:{mode}:{snap.schedule_version}")
    if mode == "earlier":
        _set_state(order, status="pending_review", proposed_start=iso(new_start), plan_id=plan.id, run_id=run.id, **original)
        msg = (f"Your order is expedited. A dispatcher is confirming an earlier slot (planned start {hhmm(new_start)}); "
               "your original appointment stays until then.")
    else:
        _set_state(order, status="revert_pending", plan_id=plan.id, run_id=run.id)
        msg = "A dispatcher is confirming your original time; the earlier slot stays until then. Priority stays P1."
    _customer_message(db, clock, order, msg, "expedite" if mode == "earlier" else "expedite_restore")
    tracer.finish(RunStatus.PENDING_REVIEW, "candidate awaits dispatcher review", {"decision": "manual", "plan_id": plan.id})
    return {"outcome": "manual", "message": msg, "run_id": run.id, "plan_id": plan.id, "planned_start": iso(new_start), "affected_count": n}


def _finish_move(db: Session, clock: Clock, snap: Snapshot, cand: Candidate, order: WorkOrder, *, mode: str, plan_id: str, schedule_version: int,
                 original: dict[str, Any], note: str, existing_start: int | None) -> None:
    """After a committed move (auto or approved): state, customer message, technician / moved-customer notifications."""
    target = cand.plan[order.id]
    new_start = clock.from_minutes(target.service_start)
    tech_name = snap.techs[target.tech_id].name
    order.pending_plan_run_id = None
    if mode == "earlier":
        _set_state(order, status="moved", new_planned_start=iso(new_start), moved_at=iso(clock.now), plan_id=plan_id, schedule_version=schedule_version,
                   affected_order_ids=list(cand.affected.affected_ids), technician_id=target.tech_id, **original)
        was = f" (was {hhmm(clock.from_minutes(existing_start))})" if existing_start is not None else ""
        msg = (f"Your order is expedited (simulated payment). New appointment: {hhmm(order.window_start)}–{hhmm(order.window_end)}, "
               f"technician {tech_name}, planned start {hhmm(new_start)}{was}. Tap ‘Keep original time’ if the earlier slot does not suit you.")
        _customer_message(db, clock, order, msg, "expedite")
    else:
        _set_state(order, status="reverted", reverted_at=iso(clock.now), restored_planned_start=iso(new_start), plan_id=plan_id, schedule_version=schedule_version)
        msg = (f"Your original time is restored: {hhmm(order.window_start)}–{hhmm(order.window_end)}, technician {tech_name}, "
               f"planned start {hhmm(new_start)}. Priority stays P1 (simulated payment, no refund).")
        _customer_message(db, clock, order, msg, "expedite_restore")
    _notify_moves(db, clock, snap, cand, order, target_note=note)
    from app.services import risk_service
    after = build_snapshot(db, clock, matrix=snap.matrix)
    risk_service.evaluate_order(db, clock, after, order)
    order.last_dispatch_key = dispatch_key(order, after)


# ------------------------------------------------------------------ public entry points
def expedite(db: Session, clock: Clock, order: WorkOrder, *, actor: str, idempotency_key: str | None = None) -> dict[str, Any]:
    """One atomic operation: simulated payment (base P1) + automatic move to the earliest start. Idempotent per order."""
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True, "message": ALREADY_MESSAGE}
    if order.lifecycle_status != LifecycleStatus.OPEN:
        raise OrderError("invalid_state", DEPARTED_MESSAGE, {"lifecycle_status": order.lifecycle_status}, status=409, kind="INVALID_STATE")
    if order.paid_expedite:
        return {"order_id": order.id, "effective_priority": order.effective_priority, "idempotent": True, "message": ALREADY_MESSAGE,
                "expedite": state_view(order)}
    ev = _record(db, clock, idempotency_key, "paid_expedite", {"order_id": order.id, "actor": actor, "auto_earliest": True})
    set_paid_expedite(db, order)
    plan_service.invalidate_plans_for_order(db, order.id, "priority changed by paid expedite")
    _set_state(order, paid_at=iso(clock.now), actor=actor)
    result = _attempt(db, clock, order, mode="earlier", trigger="expedite", actor=actor)
    out = {"order_id": order.id, "effective_priority": order.effective_priority, "idempotent": False, **result, "expedite": state_view(order)}
    return _done(ev, out, run_id=result.get("run_id"))


def keep_original_time(db: Session, clock: Clock, order: WorkOrder, *, actor: str, idempotency_key: str | None = None) -> dict[str, Any]:
    """Undo the move: original window back, re-planned at P1 (zero-disturbance first); no refund (simulated). Idempotent."""
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True}
    st = dict(order.expedite_state or {})
    status = st.get("status")
    if status in ("reverted", "revert_pending"):
        return {"order_id": order.id, "idempotent": True, "message": "Your original time is already being kept.", "expedite": state_view(order)}
    if status != "moved":
        raise OrderError("invalid_state", "This order was not moved earlier by an expedite; there is nothing to restore.", status=409, kind="INVALID_STATE")
    if order.lifecycle_status != LifecycleStatus.OPEN:
        raise OrderError("invalid_state", "The original time can only be restored before the technician departs.", status=409, kind="INVALID_STATE")
    ev = _record(db, clock, idempotency_key, "expedite_keep_original_time", {"order_id": order.id, "actor": actor})
    plan_service.invalidate_plans_for_order(db, order.id, "customer keeps the original time")
    result = _attempt(db, clock, order, mode="restore", trigger="expedite_restore", actor=actor)
    out = {"order_id": order.id, "effective_priority": order.effective_priority, "idempotent": False, **result, "expedite": state_view(order)}
    return _done(ev, out, run_id=result.get("run_id"))


def on_plan_committed(db: Session, clock: Clock, snap: Snapshot, cand: Candidate, plan: CandidatePlan, order: WorkOrder) -> None:
    """Approval hook: a reviewed expedite / restore plan was committed with its window change."""
    ew = (plan.metrics or {}).get("expedite_window") or {}
    mode = ew.get("mode", "earlier")
    original = {k: ew[k] for k in ("original_window_start", "original_window_end", "original_planned_start") if k in ew}
    before = snap.assignments.get(order.id)
    _finish_move(db, clock, snap, cand, order, mode=mode, plan_id=plan.id, schedule_version=active_schedule_version(db),
                 original=original, note="expedited" if mode == "earlier" else "original time restored",
                 existing_start=before.service_start if before else None)


def on_plan_rejected(db: Session, clock: Clock, plan: CandidatePlan, order: WorkOrder) -> None:
    """Rejection hook: the appointment that was in place stays; the customer is told."""
    mode = ((plan.metrics or {}).get("expedite_window") or {}).get("mode", "earlier")
    if mode == "earlier":
        _set_state(order, status="unchanged", review_note="earlier slot rejected by the dispatcher")
        msg = "The earlier slot could not be confirmed by the dispatcher; your original appointment stays. Priority remains P1."
    else:
        _set_state(order, status="moved", review_note="restore rejected by the dispatcher")
        msg = "Your original time could not be restored by the dispatcher; the earlier appointment stays. Priority remains P1."
    order.pending_plan_run_id = None
    _customer_message(db, clock, order, msg, "expedite")


def refresh_pending(db: Session, clock: Clock) -> list[str]:
    """Scan hook: an expedite / restore that waited for review but whose candidate expired is searched again, so the
    customer never waits for a plan that no longer exists."""
    out: list[str] = []
    rows = db.scalars(select(WorkOrder).where(WorkOrder.scenario_generation == clock.scenario_generation,
                                              WorkOrder.lifecycle_status == LifecycleStatus.OPEN)).all()
    for o in rows:
        st = o.expedite_state or {}
        if st.get("status") not in ("pending_review", "revert_pending") or not st.get("plan_id"):
            continue
        plan = db.get(CandidatePlan, st["plan_id"])
        if plan is not None and plan.status == PlanStatus.PENDING_REVIEW:
            continue
        if plan is not None and plan.status == PlanStatus.COMMITTED:
            continue  # approval hook already finished this one
        _attempt(db, clock, o, mode="earlier" if st["status"] == "pending_review" else "restore", trigger="expedite_recompute", actor="system")
        out.append(o.id)
    return out
