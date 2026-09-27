"""Committing plans (single entry point for auto and manual), schedule versions, prediction refresh."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.db.base import utc_now
from app.models.entities import Assignment, ScheduleVersion, WorkOrder
from app.models.enums import AssignmentStatus, LifecycleStatus, SchedulingStatus
from app.scheduling.domain import Assign, Snapshot
from app.scheduling.simulator import simulate_route
from app.services.clock import Clock
from app.services.ids import new_id


def _assignment_row(clock: Clock, a: Assign, version_id: int, gen: int) -> Assignment:
    return Assignment(
        id=new_id("asg"), scenario_generation=gen, order_id=a.order_id, technician_id=a.tech_id,
        schedule_version_id=version_id, origin_location_id=a.origin_location_id,
        departure=clock.from_minutes(a.departure), arrival=clock.from_minutes(a.arrival),
        service_start=clock.from_minutes(a.service_start), service_end=clock.from_minutes(a.service_end),
        travel_minutes=a.travel, waiting_minutes=a.waiting, status=AssignmentStatus.ACTIVE, locked=a.locked,
        match_score=a.match_score, score_components=a.score_components or {},
    )


def snapshot_rows(db: Session, gen: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(Assignment).where(Assignment.scenario_generation == gen,
                                               Assignment.status == AssignmentStatus.ACTIVE)).all()
    return [{
        "order_id": r.order_id, "technician_id": r.technician_id, "origin_location_id": r.origin_location_id,
        "departure": r.departure.isoformat(), "arrival": r.arrival.isoformat(),
        "service_start": r.service_start.isoformat(), "service_end": r.service_end.isoformat(),
        "travel_minutes": r.travel_minutes, "waiting_minutes": r.waiting_minutes, "locked": r.locked,
        "match_score": r.match_score,
    } for r in rows]


def new_version(db: Session, clock: Clock, reason: str, route_snapshot_id: str, plan_id: str | None = None,
                metrics: dict[str, Any] | None = None) -> ScheduleVersion:
    gen = clock.scenario_generation
    current = db.scalars(select(ScheduleVersion).where(ScheduleVersion.active.is_(True))).all()
    parent_id = None
    for v in current:
        v.active = False
        parent_id = v.id if parent_id is None or v.id > parent_id else parent_id
    version = ScheduleVersion(
        parent_id=parent_id, scenario_generation=gen, reason=reason, sim_now=clock.now, active=True,
        policy_version=get_policy().policy_version, route_snapshot_id=route_snapshot_id, plan_id=plan_id,
        snapshot=[], metrics=metrics or {},
    )
    db.add(version)
    db.flush()
    return version


def commit_plan(db: Session, clock: Clock, snap: Snapshot, plan: dict[str, Assign], *, reason: str,
                plan_id: str | None = None, metrics: dict[str, Any] | None = None) -> ScheduleVersion:
    """Apply a validated full plan atomically (caller holds the transaction). Unchanged rows are kept."""
    gen = clock.scenario_generation
    version = new_version(db, clock, reason, snap.matrix.snapshot_id, plan_id, metrics)
    active = {a.order_id: a for a in db.scalars(select(Assignment).where(
        Assignment.scenario_generation == gen, Assignment.status == AssignmentStatus.ACTIVE)).all()}
    changed_orders: list[str] = []
    for oid, a in plan.items():
        base = snap.assignments.get(oid)
        row = active.get(oid)
        unchanged = base is not None and row is not None and base.tech_id == a.tech_id and base.departure == a.departure \
            and base.service_start == a.service_start and base.origin_location_id == a.origin_location_id
        if unchanged:
            continue
        if row is not None:
            row.status = AssignmentStatus.INVALIDATED
            row.invalidated_at = utc_now()
            row.invalidated_reason = f"superseded by schedule version {version.id}"
        db.add(_assignment_row(clock, a, version.id, gen))
        changed_orders.append(oid)
        order = db.get(WorkOrder, oid)
        if order is not None:
            order.technician_id = a.tech_id
            order.scheduling_status = SchedulingStatus.ASSIGNED
            order.version += 1
            if order.lifecycle_status in (LifecycleStatus.DRAFT, LifecycleStatus.NEEDS_INFO):
                order.lifecycle_status = LifecycleStatus.OPEN
            if snap.orders[oid].recovery_target:
                order.recovery_start = clock.from_minutes(a.service_start)
    for oid, row in active.items():
        if oid not in plan:
            order = db.get(WorkOrder, oid)
            if order is not None and order.lifecycle_status not in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
                raise ValueError(f"plan drops active order {oid}")
    db.flush()
    version.snapshot = snapshot_rows(db, gen)
    version.metrics = {**(metrics or {}), "changed_orders": changed_orders}
    db.flush()
    return version


def invalidate_assignment(db: Session, clock: Clock, order_id: str, reason: str) -> Assignment | None:
    row = db.scalars(select(Assignment).where(Assignment.order_id == order_id,
                                              Assignment.status == AssignmentStatus.ACTIVE)).first()
    if row is None:
        return None
    row.status = AssignmentStatus.INVALIDATED
    row.invalidated_at = utc_now()
    row.invalidated_reason = reason
    order = db.get(WorkOrder, order_id)
    if order is not None:
        order.technician_id = None
        if order.lifecycle_status not in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
            order.scheduling_status = SchedulingStatus.UNASSIGNED
        order.version += 1
    db.flush()
    return row


def refresh_predictions(db: Session, clock: Clock, snap: Snapshot, reason: str = "prediction refresh") -> bool:
    """Re-simulate every technician's movable route from its real anchor keeping technician and pinned starts.

    Updates predicted times in place when they drift (e.g. an ongoing task runs late or a predecessor was cancelled).
    Never changes technician or moves a start earlier than pinned. Returns True if anything changed.
    """
    pins = {oid: a.service_start for oid, a in snap.assignments.items()}
    changed = False
    new_plan = dict(snap.assignments)
    for tid, tech in snap.techs.items():
        movable = snap.movable_route(tid)
        if not movable:
            continue
        rr = simulate_route(snap, tech, [a.order_id for a in movable], pins)
        if not rr.feasible:
            continue  # unreachable chain is surfaced by the risk scan, not silently rewritten
        for old, new in zip(movable, rr.assignments):
            if (old.departure, old.arrival, old.service_start, old.service_end, old.origin_location_id, old.travel) != \
               (new.departure, new.arrival, new.service_start, new.service_end, new.origin_location_id, new.travel):
                new_plan[old.order_id] = Assign(**{**new.__dict__, "match_score": old.match_score,
                                                   "score_components": old.score_components})
                changed = True
    if changed:
        commit_plan(db, clock, snap, new_plan, reason=reason)
    return changed
