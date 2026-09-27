"""Exception events: customer cancel, technician unavailability, complaints, paid expedite. Idempotent by key."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.db.base import utc_now
from app.models.entities import Assignment, HumanCase, InboundEvent, Technician, WorkOrder
from app.models.enums import (
    AssignmentStatus,
    LifecycleStatus,
    PlanStatus,
    PolicyDecision,
    Priority,
    RiskStatus,
    RiskType,
    RunStatus,
    SchedulingStatus,
    TechnicianStatus,
)
from app.orchestration.orchestrator import DispatchOutcome, dispatch_order
from app.scheduling.priority import cancellation_priority
from app.services import plan_service, risk_service, standby_service
from app.services.agent_runs import start_run
from app.services.clock import Clock, get_clock
from app.services.ids import new_id
from app.services.notification_service import notify
from app.services.order_service import OrderError, set_paid_expedite
from app.services.schedule_service import invalidate_assignment, refresh_predictions
from app.services.snapshot import build_snapshot
from app.services.timeutil import iso, minutes_between, parse_iso


def _existing(db: Session, key: str | None) -> InboundEvent | None:
    if not key:
        return None
    return db.scalars(select(InboundEvent).where(InboundEvent.idempotency_key == key)).first()


def _record(db: Session, clock: Clock, key: str | None, etype: str, payload: dict[str, Any]) -> InboundEvent:
    ev = InboundEvent(id=new_id("evt"), scenario_generation=clock.scenario_generation,
                      idempotency_key=key or new_id("auto"), type=etype, payload=payload, status="processing")
    db.add(ev)
    db.flush()
    return ev


def _done(ev: InboundEvent, result: dict[str, Any], status: str = "resolved", run_id: str | None = None) -> dict[str, Any]:
    ev.status = status
    ev.result = result
    ev.processed_at = utc_now()
    ev.run_id = run_id
    return result


# ---------------------------------------------------------------- customer cancel
def customer_cancel(db: Session, order_id: str, *, customer_ref: str | None, actor: str, reason: str | None,
                    idempotency_key: str | None = None) -> dict[str, Any]:
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True}
    clock = get_clock(db)
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise OrderError("not_found", f"order {order_id} not found", status=404)
    if customer_ref is not None and order.customer_ref != customer_ref:
        raise OrderError("forbidden", "this session does not own the order", status=403)
    ev = _record(db, clock, idempotency_key, "customer_cancel", {"order_id": order_id, "actor": actor, "reason": reason})
    if order.lifecycle_status == LifecycleStatus.CANCELLED:
        return _done(ev, {"order_id": order_id, "status": "CANCELLED", "idempotent": True, "already": True})
    if order.lifecycle_status == LifecycleStatus.COMPLETED:
        _done(ev, {"rejected": "completed"}, status="unresolved")
        raise OrderError("order_completed", "completed orders cannot be cancelled as pending service", status=409)
    if order.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
        _done(ev, {"rejected": order.lifecycle_status}, status="unresolved")
        raise OrderError("already_departed", f"technician already {order.lifecycle_status.lower().replace('_', ' ')}; "
                         "cancellation is no longer allowed", {"lifecycle_status": order.lifecycle_status}, status=409)
    # atomic: mark cancelled, release assignment, close risks/standby, invalidate candidates, refresh successors
    snap_before = build_snapshot(db, clock)
    old = snap_before.assignments.get(order_id)
    order.lifecycle_status = LifecycleStatus.CANCELLED
    order.cancelled_at = clock.now
    order.cancel_reason = reason or f"cancelled by {actor}"
    order.scheduling_status = SchedulingStatus.UNASSIGNED
    order.version += 1
    row = db.scalars(select(Assignment).where(Assignment.order_id == order_id, Assignment.status == AssignmentStatus.ACTIVE)).first()
    if row is not None:
        row.status = AssignmentStatus.CANCELLED
        row.invalidated_at = utc_now()
        row.invalidated_reason = "customer cancelled"
    order.technician_id = None
    for r in risk_service.active_risks(db, order_id):
        r.status = RiskStatus.RESOLVED
        r.resolved_at = clock.now
        r.idempotency_key = f"{r.idempotency_key}:resolved:{r.id}"
    standby_service.invalidate_standby(db, order_id)
    n_plans = plan_service.invalidate_plans_for_order(db, order_id, f"order {order_id} cancelled by customer")
    db.flush()
    # successors on the same technician: recompute travel from the real predecessor (keep technician & starts)
    snap_after = build_snapshot(db, clock, matrix=snap_before.matrix)
    refreshed = refresh_predictions(db, clock, snap_after, reason=f"cancel {order_id}: successor travel recomputed")
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="cancelled", order_id=order_id,
           message=f"[simulated] Order {order_id} has been cancelled." + (" Paid expedite recorded; no real refund is processed." if order.paid_expedite else ""))
    if old is not None:
        notify(db, clock, recipient_ref=old.tech_id, recipient_type="technician", type="task_removed", order_id=order_id,
               message=f"[simulated] Task {order_id} was cancelled by the customer and removed from your route.")
    result = {"order_id": order_id, "status": "CANCELLED", "released_technician": old.tech_id if old else None,
              "invalidated_plans": n_plans, "successors_refreshed": refreshed, "paid_expedite": order.paid_expedite}
    return _done(ev, result)


# ---------------------------------------------------------------- technician unavailability
def technician_unavailable(db: Session, technician_id: str, *, start: datetime, end: datetime, reason: str,
                           idempotency_key: str | None = None) -> dict[str, Any]:
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True}
    clock = get_clock(db)
    tech = db.get(Technician, technician_id)
    if tech is None:
        raise OrderError("not_found", f"technician {technician_id} not found", status=404)
    if end <= start:
        raise OrderError("invalid_interval", "end must be after start")
    ev = _record(db, clock, idempotency_key, "technician_unavailable",
                 {"technician_id": technician_id, "start": start.isoformat(), "end": end.isoformat(), "reason": reason})
    tracer = start_run(db, agent="RiskMonitoringAgent", trigger="technician_unavailable", scenario_generation=clock.scenario_generation,
                       input_refs={"technician_id": technician_id})
    policy = get_policy()
    # 1. resource facts first
    with tracer.step("update_resource_facts") as st:
        intervals = list(tech.unavailable_intervals or [])
        intervals.append({"start": start.isoformat() + "Z", "end": end.isoformat() + "Z", "reason": reason})
        tech.unavailable_intervals = intervals
        tech.version += 1
        if start <= clock.now < end:
            tech.status = TechnicianStatus.UNAVAILABLE
        tracer.tool(st, "technician_unavailability", intervals=len(intervals))
    # 2. affected tasks
    s_min, e_min = clock.to_minutes(start), clock.to_minutes(end)
    rows = db.scalars(select(Assignment).where(Assignment.technician_id == technician_id,
                                               Assignment.status == AssignmentStatus.ACTIVE)).all()
    released: list[dict[str, Any]] = []
    interrupted: list[str] = []
    with tracer.step("invalidate_assignments") as st:
        for a in rows:
            order = db.get(WorkOrder, a.order_id)
            if order is None or order.lifecycle_status in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
                continue
            a_start, a_end = clock.to_minutes(a.departure), clock.to_minutes(a.service_end)
            if not (a_start < e_min and s_min < a_end):
                continue
            if order.lifecycle_status in policy.auto_release_on_unavailable:
                # Still on the road: nobody is at the customer's door, so there is nothing to hand over and no reason
                # to wait for a human. The order goes back on the board and is recovered in this same event, first
                # (its EXECUTION_INTERRUPTED risk makes it P0 — see priority.evaluate_order_risks). Before this, a
                # technician who fell ill mid-drive left the order locked to them until the end of the day.
                was = order.lifecycle_status
                facts = release_execution(db, clock, order, reason=f"technician {technician_id} unavailable while {was}: {reason}",
                                          free_technician=False)   # the technician's own status is set by this event
                risk_service.upsert_risk(db, clock, order_id=order.id, rtype=RiskType.EXECUTION_INTERRUPTED, severity=Priority.P0,
                                         payload={"technician_id": technician_id, "reason": reason, "released_from": was,
                                                  "released_facts": facts,
                                                  "detail": f"technician became unavailable while {was.lower().replace('_', ' ')}; re-dispatching"},
                                         technician_id=technician_id, run_id=tracer.run.id)
                released.append({"order_id": order.id, "remaining_minutes": minutes_between(clock.now, order.window_end),
                                 "cancellation_priority": Priority.P0.value, "released_from": was})
                notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="reassignment_pending", order_id=order.id,
                       message=f"[simulated] Your technician had to turn back; we are sending someone else for order {order.id} as a priority.")
                notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="execution_interrupted", order_id=order.id,
                       message=f"[simulated] {tech.name} reported unavailable while {was} on {order.id}; released and re-dispatching at P0.")
                continue
            if order.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
                # At the customer's home, possibly mid-repair: a second technician needs the first one's context, so
                # the lock is kept and a person decides (the order detail offers "Stop this visit" for that).
                interrupted.append(order.id)
                risk_service.upsert_risk(db, clock, order_id=order.id, rtype=RiskType.EXECUTION_INTERRUPTED, severity=Priority.P0,
                                         payload={"technician_id": technician_id, "reason": reason, "detail": "execution interrupted; manual handling"},
                                         technician_id=technician_id, status=RiskStatus.MANUAL, run_id=tracer.run.id)
                notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="execution_interrupted", order_id=order.id,
                       message=f"[simulated] {tech.name} reported unavailable while {order.lifecycle_status} on {order.id}. Manual handling required; lock not released automatically.")
                from app.services import human_service
                human_service.flag_for_human(db, clock, source="POLICY_REQUIRED", category="execution_interrupted", urgency="critical",
                                             reason_summary=f"{tech.name} unavailable ({reason}) while {order.lifecycle_status} on {order.id}; the lock is kept, decide manually",
                                             customer_id=order.customer_id, order_id=order.id, evidence_refs=[f"order:{order.id}", f"technician:{technician_id}"],
                                             suggested_next_action="contact the technician/customer; reassign or reschedule by hand")
                continue
            remaining = minutes_between(clock.now, order.window_end)
            invalidate_assignment(db, clock, order.id, f"technician {technician_id} unavailable: {reason}")
            plan_service.invalidate_plans_for_order(db, order.id, f"technician {technician_id} unavailable")
            standby_service.invalidate_standby(db, order.id)
            sev = cancellation_priority(remaining, policy)
            risk_service.upsert_risk(db, clock, order_id=order.id, rtype=RiskType.TECHNICIAN_CANCELLED, severity=sev,
                                     payload={"technician_id": technician_id, "remaining_minutes": remaining, "reason": reason,
                                              "detail": f"technician cancelled with {remaining} min to window_end"},
                                     technician_id=technician_id, run_id=tracer.run.id)
            released.append({"order_id": order.id, "remaining_minutes": remaining, "cancellation_priority": sev.value})
            notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="reassignment_pending", order_id=order.id,
                   message=f"[simulated] Your technician became unavailable; we are re-assigning order {order.id}.")
        tracer.tool(st, "invalidate_assignments", released=[r["order_id"] for r in released], interrupted=interrupted)
    # also invalidate pending candidates that use this technician
    from app.models.entities import CandidatePlan
    for p in db.scalars(select(CandidatePlan).where(CandidatePlan.status.in_([PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED]))).all():
        if any(r["technician_id"] == technician_id for r in (p.assignments or [])):
            p.status = PlanStatus.INVALIDATED
            p.status_reason = f"technician {technician_id} became unavailable"
    db.flush()
    # 3. recover, explicit order (most urgent first), facts already updated
    outcomes: list[DispatchOutcome] = []
    with tracer.step("recover_orders") as st:
        ids = [r["order_id"] for r in released]
        moves_so_far = 0  # affected orders already auto-moved by earlier targets of this event
        snap = build_snapshot(db, clock)
        for oid in ids:
            o = db.get(WorkOrder, oid)
            if o is not None:
                risk_service.evaluate_order(db, clock, snap, o)
        ordered = sorted(ids, key=lambda oid: (Priority(db.get(WorkOrder, oid).effective_priority).rank, db.get(WorkOrder, oid).window_end, oid))  # type: ignore[union-attr]
        for oid in ordered:
            oc = dispatch_order(db, oid, trigger="technician_unavailable", force_manual_if_affected=moves_so_far > 0)
            moves_so_far += oc.affected_count
            outcomes.append(oc)
        # later auto-commits in this same event move the schedule version; pending plans generated earlier in the loop
        # would be stale at approval time → recompute them now so the dispatcher sees approvable candidates
        from app.models.entities import CandidatePlan as _Plan
        from app.services.snapshot import active_schedule_version
        current = active_schedule_version(db)
        for oc in list(outcomes):
            if oc.decision != PolicyDecision.MANUAL.value:
                continue
            stale = [p for p in db.scalars(select(_Plan).where(_Plan.run_id == oc.run_id, _Plan.status == PlanStatus.PENDING_REVIEW)).all()
                     if p.base_schedule_version != current]
            if stale:
                for p in stale:
                    p.status = PlanStatus.EXPIRED
                    p.status_reason = f"schedule changed (v{p.base_schedule_version} → v{current}) by a later recovery in the same event; recomputed"
                outcomes.append(dispatch_order(db, oc.target_order_id, trigger="technician_unavailable_recompute",
                                               force_manual_if_affected=moves_so_far > 0))
        tracer.tool(st, "dispatch", results=[o.as_dict() for o in outcomes])
        st["summary"] = ", ".join(f"{o.target_order_id}={o.decision}" for o in outcomes) or "no orders affected"
    tracer.finish("completed", f"{len(released)} order(s) released, {len(interrupted)} execution interruption(s)",
                  {"released": released, "interrupted": interrupted, "dispatch": [o.as_dict() for o in outcomes]})
    result = {"technician_id": technician_id, "released": released, "interrupted": interrupted,
              "dispatch": [o.as_dict() for o in outcomes], "run_id": tracer.run.id}
    return _done(ev, result, run_id=tracer.run.id)


# ---------------------------------------------------------------- complaints
def complaint(db: Session, order_id: str, *, complaint_type: str, text: str, customer_ref: str | None,
              idempotency_key: str | None = None, classification: dict[str, Any] | None = None) -> dict[str, Any]:
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True}
    clock = get_clock(db)
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise OrderError("not_found", f"order {order_id} not found", status=404)
    if customer_ref is not None and order.customer_ref != customer_ref:
        raise OrderError("forbidden", "this session does not own the order", status=403)
    ev = _record(db, clock, idempotency_key, "complaint", {"order_id": order_id, "complaint_type": complaint_type, "text": text})
    tracer = start_run(db, agent="RiskMonitoringAgent", trigger=f"complaint:{complaint_type}", scenario_generation=clock.scenario_generation,
                       target_order_id=order_id)
    result: dict[str, Any]
    if order.lifecycle_status in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
        risk_service.upsert_risk(db, clock, order_id=order_id, rtype=RiskType.NON_SCHEDULING_COMPLAINT, severity=Priority.P3,
                                 payload={"complaint_type": complaint_type, "text": text, "detail": "historical complaint on closed order"},
                                 status=RiskStatus.MANUAL, run_id=tracer.run.id)
        result = {"order_id": order_id, "handling": "manual_service_queue", "priority_changed": False, "note": "order closed; record kept"}
        tracer.finish("completed", "historical complaint recorded", result)
        return _done(ev, result, run_id=tracer.run.id)
    if complaint_type == "lateness":
        with tracer.step("verify_lateness") as st:
            past_deadline = clock.now > order.window_end
            not_started = order.service_started_at is None
            verified = past_deadline and not_started
            tracer.tool(st, "evaluate_risk", now=clock.now.isoformat(), window_end=order.window_end.isoformat(),
                        service_started=not not_started, verified=verified)
        if verified:
            risk_service.upsert_risk(db, clock, order_id=order_id, rtype=RiskType.LATENESS_COMPLAINT_VERIFIED, severity=Priority.P0,
                                     payload={"text": text, "detail": "lateness complaint verified: past deadline, not started"}, run_id=tracer.run.id)
            snap = build_snapshot(db, clock)
            risk_service.evaluate_order(db, clock, snap, order)
            outcome = dispatch_order(db, order_id, trigger="lateness_complaint_verified")
            result = {"order_id": order_id, "verified": True, "effective_priority": order.effective_priority, "dispatch": outcome.as_dict()}
        else:
            snap = build_snapshot(db, clock)
            state = risk_service.evaluate_order(db, clock, snap, order)
            result = {"order_id": order_id, "verified": False, "effective_priority": order.effective_priority,
                      "note": "not past deadline or service already started; handled as general risk",
                      "reasons": [r.as_dict() for r in state.reasons]}
        notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="complaint_ack", order_id=order_id,
               message="[simulated] We received your lateness complaint and are checking the schedule.")
        tracer.finish("completed", "lateness complaint processed", result)
        return _done(ev, result, run_id=tracer.run.id)
    # attitude / quality / other: manual service queue, no priority change, no solver
    risk_service.upsert_risk(db, clock, order_id=order_id, rtype=RiskType.NON_SCHEDULING_COMPLAINT, severity=Priority.P3,
                             payload={"complaint_type": complaint_type, "text": text, "classification": classification or {},
                                      "detail": f"{complaint_type} complaint routed to manual service queue"},
                             status=RiskStatus.MANUAL, run_id=tracer.run.id)
    notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="service_complaint", order_id=order_id,
           message=f"[simulated] {complaint_type} complaint on {order_id}: {text[:120]}")
    result = {"order_id": order_id, "handling": "manual_service_queue", "priority_changed": False}
    tracer.finish("completed", "non-scheduling complaint routed to manual queue", result)
    return _done(ev, result, run_id=tracer.run.id)


# ---------------------------------------------------------------- paid expedite
def paid_expedite(db: Session, order_id: str, *, customer_ref: str | None, idempotency_key: str | None = None) -> dict[str, Any]:
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True}
    clock = get_clock(db)
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise OrderError("not_found", f"order {order_id} not found", status=404)
    if customer_ref is not None and order.customer_ref != customer_ref:
        raise OrderError("forbidden", "this session does not own the order", status=403)
    ev = _record(db, clock, idempotency_key, "paid_expedite", {"order_id": order_id})
    set_paid_expedite(db, order)
    plan_service.invalidate_plans_for_order(db, order_id, "priority changed by paid expedite")
    outcome = dispatch_order(db, order_id, trigger="paid_expedite")
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="expedite", order_id=order_id,
           message=f"[simulated] Expedite payment recorded for {order_id}; priority is now {order.effective_priority}.")
    return _done(ev, {"order_id": order_id, "effective_priority": order.effective_priority, "dispatch": outcome.as_dict()}, run_id=outcome.run_id)


# ---------------------------------------------------------------- releasing an executing order
def release_execution(db: Session, clock: Clock, order: WorkOrder, *, reason: str, free_technician: bool = True) -> dict[str, Any]:
    """Un-lock an order whose technician has departed and put it back on the board as OPEN.

    Used by two callers with the same mechanics but different triggers: the dispatcher's explicit "stop this visit",
    and the automatic path when the technician becomes unavailable while still on the road.

    The execution facts are not erased — depart/arrive/start rows stay in `execution_events`, the recorded breach stays
    on the order — only the order's live cursor is reset so a fresh technician can be dispatched from a clean state.
    Returns the facts that were released so the caller can record them.
    """
    facts = {"lifecycle_status": order.lifecycle_status, "technician_id": order.technician_id,
             "departed_at": iso(order.departed_at), "arrived_at": iso(order.arrived_at),
             "service_started_at": iso(order.service_started_at)}
    old_tech_id = order.technician_id
    invalidate_assignment(db, clock, order.id, reason)
    order.lifecycle_status = LifecycleStatus.OPEN
    order.departed_at = order.arrived_at = order.service_started_at = None
    order.version += 1
    tech = db.get(Technician, old_tech_id) if (old_tech_id and free_technician) else None
    if tech is not None and tech.status in (TechnicianStatus.EN_ROUTE, TechnicianStatus.ARRIVED, TechnicianStatus.BUSY):
        blocked = any(parse_iso(i["start"]) <= clock.now < parse_iso(i["end"]) for i in (tech.unavailable_intervals or []))
        tech.status = TechnicianStatus.UNAVAILABLE if blocked else TechnicianStatus.AVAILABLE
        tech.version += 1
    plan_service.invalidate_plans_for_order(db, order.id, reason)
    standby_service.invalidate_standby(db, order.id)
    db.flush()
    return facts


# ---------------------------------------------------------------- dispatcher: abort a departed execution
def abort_execution(db: Session, order_id: str, *, actor: str, reason: str, outcome: str = "reschedule",
                    idempotency_key: str | None = None) -> dict[str, Any]:
    """Release an order whose technician already departed (EN_ROUTE / ARRIVED / IN_PROGRESS).

    Until V3.1 such an order was a dead end: the customer could not cancel it (409 already_departed), the scan and the
    orchestrator skip executing orders, and `technician_unavailable` deliberately keeps the lock and only raises an
    EXECUTION_INTERRUPTED risk. Nothing could then move the order again. This is the dispatcher's explicit way out —
    a deliberate, audited decision, never something the simulator or an agent performs on its own.

    The execution facts are NOT erased: every depart/arrive/start stays in `execution_events`, the breach stays on the
    order, and the released timestamps are copied into the inbound event. Only the order's own live cursor is reset so
    that a new technician can be dispatched from a clean OPEN state.

    outcome: "reschedule" (default) → re-dispatch through the normal recovery path; "cancel" → close the order.
    """
    prior = _existing(db, idempotency_key)
    if prior is not None:
        return {**prior.result, "idempotent": True}
    clock = get_clock(db)
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise OrderError("not_found", f"order {order_id} not found", status=404)
    if outcome not in ("reschedule", "cancel"):
        raise OrderError("invalid_outcome", "outcome must be 'reschedule' or 'cancel'", kind="DATA_INCOMPLETE")
    if not (reason or "").strip():
        raise OrderError("reason_required", "aborting an execution in progress requires a reason", kind="DATA_INCOMPLETE")
    ev = _record(db, clock, idempotency_key, "abort_execution",
                 {"order_id": order_id, "actor": actor, "reason": reason, "outcome": outcome})
    if order.lifecycle_status not in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
        _done(ev, {"rejected": order.lifecycle_status}, status="unresolved")
        raise OrderError("not_executing", f"order is {order.lifecycle_status}; this action only releases an order whose "
                         "technician already departed — use cancel or re-run dispatch instead",
                         {"lifecycle_status": order.lifecycle_status}, status=409)
    tracer = start_run(db, agent="Orchestrator", trigger="abort_execution", scenario_generation=clock.scenario_generation,
                       target_order_id=order_id, input_refs={"order_id": order_id, "actor": actor, "outcome": outcome})
    old_tech_id = order.technician_id
    with tracer.step("release_execution_lock") as st:
        released_facts = release_execution(db, clock, order, reason=f"execution aborted by {actor}: {reason}")
        risk_service.resolve_risk(db, clock, order_id, RiskType.EXECUTION_INTERRUPTED)
        tracer.tool(st, "release_execution_lock", released=released_facts, technician_released=old_tech_id)
        st["summary"] = f"released {order_id} from {released_facts['lifecycle_status']}"
    db.flush()
    # the policy case that asked a human to decide is answered by this decision
    from app.services import human_service
    closed_case = None
    if order.human_case_id:
        case = db.get(HumanCase, order.human_case_id)
        if case is not None and case.status != "resolved" and case.category == "execution_interrupted":
            human_service.resolve(db, clock, case.id, resolution=f"execution aborted by {actor} ({outcome}): {reason}", author=actor)
            closed_case = case.id
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="execution_aborted", order_id=order_id,
           message=f"[simulated] The visit for order {order_id} was interrupted and stopped by our dispatcher. "
                   + ("We are arranging a new technician." if outcome == "reschedule" else "The order has been cancelled."))
    if old_tech_id:
        notify(db, clock, recipient_ref=old_tech_id, recipient_type="technician", type="task_removed", order_id=order_id,
               message=f"[simulated] Task {order_id} was stopped by the dispatcher ({reason}) and removed from your route.")
    result: dict[str, Any] = {"order_id": order_id, "released_from": released_facts["lifecycle_status"],
                              "released_technician": old_tech_id, "outcome": outcome, "reason": reason,
                              "closed_human_case_id": closed_case, "released_facts": released_facts}
    if outcome == "cancel":
        result["cancel"] = customer_cancel(db, order_id, customer_ref=None, actor=actor,
                                           reason=f"execution aborted by {actor}: {reason}")
        result["lifecycle_status"] = LifecycleStatus.CANCELLED
    else:
        snap = build_snapshot(db, clock)
        risk_service.evaluate_order(db, clock, snap, order)
        oc = dispatch_order(db, order_id, trigger="abort_execution")
        result["dispatch"] = oc.as_dict()
        result["lifecycle_status"] = order.lifecycle_status
        snap_after = build_snapshot(db, clock)
        refresh_predictions(db, clock, snap_after, reason=f"execution of {order_id} aborted: successors re-simulated")
    tracer.finish(RunStatus.COMPLETED, f"execution aborted ({outcome})", {"outcome": outcome})
    return _done(ev, result, run_id=tracer.run.id)
