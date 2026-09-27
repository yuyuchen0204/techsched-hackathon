"""RiskMonitoringAgent core: deterministic scan of pending orders, risk events with stable dedupe keys."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import Assignment, RiskEvent, Technician, WorkOrder
from app.models.enums import AssignmentStatus, LifecycleStatus, Priority, RiskStatus, RiskType
from app.scheduling.domain import Snapshot
from app.scheduling.priority import RiskReason, combine, evaluate_order_risks
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.timeutil import parse_iso

# Risks that are facts recorded by events (not derived every scan); resolved explicitly.
EVENT_RISKS = {RiskType.TECHNICIAN_CANCELLED, RiskType.LATENESS_COMPLAINT_VERIFIED, RiskType.NON_SCHEDULING_COMPLAINT,
               RiskType.EXECUTION_INTERRUPTED}


@dataclass
class OrderRiskState:
    order: WorkOrder
    reasons: list[RiskReason]
    predicted_start: int | None
    has_valid_assignment: bool
    changed: bool


def risk_key(order_id: str, rtype: RiskType) -> str:
    return f"{order_id}:{rtype.value}"


def active_risks(db: Session, order_id: str | None = None) -> list[RiskEvent]:
    q = select(RiskEvent).where(RiskEvent.status.in_([RiskStatus.ACTIVE, RiskStatus.MANUAL]))
    if order_id:
        q = q.where(RiskEvent.target_order_id == order_id)
    return list(db.scalars(q.order_by(RiskEvent.first_seen)).all())


def upsert_risk(db: Session, clock: Clock, *, order_id: str | None, rtype: RiskType, severity: Priority,
                payload: dict[str, Any] | None = None, technician_id: str | None = None,
                status: RiskStatus = RiskStatus.ACTIVE, run_id: str | None = None) -> tuple[RiskEvent, bool]:
    key = risk_key(order_id or technician_id or "global", rtype)
    row = db.scalars(select(RiskEvent).where(RiskEvent.idempotency_key == key)).first()
    created = False
    if row is None or row.status == RiskStatus.RESOLVED:
        if row is not None:
            row.idempotency_key = f"{key}:resolved:{row.id}"  # keep history, free the key
        row = RiskEvent(id=new_id("risk"), scenario_generation=clock.scenario_generation, idempotency_key=key,
                        type=rtype.value, target_order_id=order_id, technician_id=technician_id,
                        effective_time=clock.now, payload=payload or {}, severity=severity.value, status=status,
                        first_seen=clock.now, last_seen=clock.now, run_id=run_id)
        db.add(row)
        created = True
    else:
        row.last_seen = clock.now  # observation only — never bumps business versions
        if row.severity != severity.value:
            row.severity = severity.value
            created = True  # severity change is a meaningful change
        if payload:
            row.payload = {**(row.payload or {}), **payload}
    db.flush()
    return row, created


def resolve_risk(db: Session, clock: Clock, order_id: str, rtype: RiskType) -> bool:
    key = risk_key(order_id, rtype)
    row = db.scalars(select(RiskEvent).where(RiskEvent.idempotency_key == key)).first()
    if row is None or row.status == RiskStatus.RESOLVED:
        return False
    row.status = RiskStatus.RESOLVED
    row.resolved_at = clock.now
    row.idempotency_key = f"{key}:resolved:{row.id}"
    db.flush()
    return True


def _tech_unavailable_at(tech: Technician, when_minutes: int, clock: Clock) -> bool:
    for iv in tech.unavailable_intervals or []:
        s = clock.to_minutes(parse_iso(iv["start"]))
        e = clock.to_minutes(parse_iso(iv["end"]))
        if s <= when_minutes < e:
            return True
    return False


def evaluate_order(db: Session, clock: Clock, snap: Snapshot, order: WorkOrder) -> OrderRiskState:
    """Evaluate one order's risk reasons against the current snapshot and persist effective priority."""
    policy = get_policy()
    spec = snap.orders.get(order.id)
    assignment = snap.assignments.get(order.id)
    predicted_start = assignment.service_start if assignment else None
    has_valid = False
    if assignment is not None and spec is not None:
        tech = db.get(Technician, assignment.tech_id)
        has_valid = tech is not None and not _tech_unavailable_at(tech, assignment.departure, clock) \
            and (spec.recovery_target or assignment.service_start <= spec.window_end)
    existing = {r.type: r for r in active_risks(db, order.id)}
    cancel_remaining = None
    if RiskType.TECHNICIAN_CANCELLED.value in existing and not has_valid:
        cancel_remaining = int(existing[RiskType.TECHNICIAN_CANCELLED.value].payload.get("remaining_minutes", 0))
    verified_late = RiskType.LATENESS_COMPLAINT_VERIFIED.value in existing
    # an interrupted execution keeps its P0 until the order is validly re-assigned (or, for a kept lock, until a human acts)
    interrupted = RiskType.EXECUTION_INTERRUPTED.value in existing and not has_valid
    reasons = evaluate_order_risks(
        now=clock.now_minutes, window_end=clock.to_minutes(order.window_end), lifecycle_status=order.lifecycle_status,
        service_started=order.service_started_at is not None, predicted_start=predicted_start,
        has_valid_assignment=has_valid, tech_cancel_remaining=cancel_remaining, verified_late_complaint=verified_late,
        policy=policy, execution_interrupted=interrupted,
    )
    # derived risks: upsert present, resolve absent (event risks are resolved by their own handlers)
    present = {r.type for r in reasons}
    changed = False
    for r in reasons:
        if r.type in (RiskType.TECHNICIAN_CANCELLED, RiskType.LATENESS_COMPLAINT_VERIFIED, RiskType.EXECUTION_INTERRUPTED):
            continue  # already recorded by the event handler; severity is what the event stored
        _, created = upsert_risk(db, clock, order_id=order.id, rtype=r.type, severity=r.priority, payload={"detail": r.detail})
        changed = changed or created
    for rtype_value, row in existing.items():
        rtype = RiskType(rtype_value)
        if rtype in EVENT_RISKS:
            # technician cancellation / an interrupted drive are resolved once the order has a valid assignment again.
            # A kept lock (ARRIVED / IN_PROGRESS, status MANUAL) is not "valid" in this sense until a human releases it.
            if rtype in (RiskType.TECHNICIAN_CANCELLED, RiskType.EXECUTION_INTERRUPTED) and has_valid \
                    and row.status != RiskStatus.MANUAL:
                resolve_risk(db, clock, order.id, rtype)
                changed = True
            if rtype == RiskType.LATENESS_COMPLAINT_VERIFIED and order.service_started_at is not None:
                resolve_risk(db, clock, order.id, rtype)
                changed = True
            continue
        if rtype not in present:
            resolve_risk(db, clock, order.id, rtype)
            changed = True
    if order.lifecycle_status in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
        for rtype_value in list(existing):
            resolve_risk(db, clock, order.id, RiskType(rtype_value))
        reasons = []
    risk_p, eff = combine(Priority(order.base_priority), reasons)
    new_reasons = [{"type": "BASE", "priority": order.base_priority,
                    "detail": "paid expedite (simulated)" if order.paid_expedite else "normal order"}]
    new_reasons += [r.as_dict() for r in reasons]
    if order.risk_priority != risk_p.value or order.effective_priority != eff.value:
        order.risk_priority = risk_p.value
        order.effective_priority = eff.value
        order.version += 1
        changed = True
    if order.priority_reasons != new_reasons:
        order.priority_reasons = new_reasons
    if clock.now > order.window_end and order.service_started_at is None and order.breach_recorded_at is None \
            and order.lifecycle_status not in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
        order.breach_recorded_at = clock.now
    db.flush()
    return OrderRiskState(order, reasons, predicted_start, has_valid, changed)


def pending_orders(db: Session, clock: Clock) -> list[WorkOrder]:
    return list(db.scalars(select(WorkOrder).where(
        WorkOrder.scenario_generation == clock.scenario_generation,
        WorkOrder.lifecycle_status.notin_([LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED, LifecycleStatus.DRAFT,
                                          LifecycleStatus.NEEDS_INFO]),
    ).order_by(WorkOrder.window_end, WorkOrder.id)).all())


def active_assignment(db: Session, order_id: str) -> Assignment | None:
    return db.scalars(select(Assignment).where(Assignment.order_id == order_id,
                                               Assignment.status == AssignmentStatus.ACTIVE)).first()
