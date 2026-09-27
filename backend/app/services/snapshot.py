"""Build the pure scheduling Snapshot from persisted state (the only bridge DB -> solver)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Assignment, Location, Technician, WorkOrder
from app.models.enums import AssignmentStatus, LifecycleStatus, Priority
from app.providers.route.base import LocationPoint, TravelMatrix
from app.providers.route.factory import compute_matrix
from app.scheduling.domain import Assign, Interval, OrderSpec, Snapshot, TechSpec
from app.services.clock import Clock
from app.services.timeutil import parse_iso

_matrix_cache: dict[str, TravelMatrix] = {}


def load_locations(db: Session) -> list[LocationPoint]:
    return [LocationPoint(l.id, l.name, l.lat, l.lon) for l in db.scalars(select(Location).order_by(Location.id)).all()]


def get_matrix(db: Session, force: bool = False) -> TravelMatrix:
    locs = load_locations(db)
    key = ",".join(sorted(l.id for l in locs))
    if force or key not in _matrix_cache:
        _matrix_cache.clear()
        _matrix_cache[key] = compute_matrix(locs)
    return _matrix_cache[key]


def _intervals(clock: Clock, raw: list[Any]) -> tuple[Interval, ...]:
    out: list[Interval] = []
    for item in raw or []:
        s = clock.to_minutes(parse_iso(item["start"]))
        e = clock.to_minutes(parse_iso(item["end"]))
        if e > s:
            out.append((s, e))
    return tuple(out)


def order_spec(clock: Clock, o: WorkOrder) -> OrderSpec:
    snap = o.catalog_snapshot or {}
    overdue = (clock.now > o.window_end and o.service_started_at is None
               and o.lifecycle_status not in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED))
    return OrderSpec(
        id=o.id,
        trade_type=snap.get("trade_type", ""),
        required_level=int(snap.get("complexity_level", 1)),
        duration=int(snap.get("repair_duration_minutes", 30)),
        window_start=clock.to_minutes(o.window_start),
        window_end=clock.to_minutes(o.window_end),
        location_id=o.location_id,
        priority=Priority(o.effective_priority),
        lifecycle_status=o.lifecycle_status,
        version=o.version,
        paid_expedite=o.paid_expedite,
        recovery_target=overdue,
        customer_ref=o.customer_ref,
        excluded_technicians=tuple(o.excluded_technician_ids or ()),
    )


def tech_spec(clock: Clock, t: Technician, actual_worked: int, break_blocks: list[Interval] | None = None) -> TechSpec:
    return TechSpec(
        id=t.id, name=t.name, skills=dict(t.skills or {}),
        shift_start=clock.to_minutes(t.shift_start), shift_end=clock.to_minutes(t.shift_end),
        breaks=tuple(sorted(_intervals(clock, t.breaks) + tuple(break_blocks or ()))), unavailable=_intervals(clock, t.unavailable_intervals),
        home_location_id=t.home_location_id, current_location_id=t.current_location_id,
        status=t.status, version=t.version, actual_worked_minutes=actual_worked,
    )


def assign_spec(clock: Clock, a: Assignment) -> Assign:
    return Assign(
        order_id=a.order_id, tech_id=a.technician_id, origin_location_id=a.origin_location_id,
        departure=clock.to_minutes(a.departure), arrival=clock.to_minutes(a.arrival),
        service_start=clock.to_minutes(a.service_start), service_end=clock.to_minutes(a.service_end),
        travel=a.travel_minutes, waiting=a.waiting_minutes, locked=a.locked,
        match_score=a.match_score, score_components=dict(a.score_components or {}),
    )


def active_schedule_version(db: Session) -> int:
    from app.models.entities import ScheduleVersion
    v = db.scalars(select(ScheduleVersion).where(ScheduleVersion.active.is_(True)).order_by(ScheduleVersion.id.desc())).first()
    return v.id if v else 0


def build_snapshot(db: Session, clock: Clock, matrix: TravelMatrix | None = None) -> Snapshot:
    gen = clock.scenario_generation
    # only today's work: closed orders from earlier days (customer history) never enter the schedule
    orders = [o for o in db.scalars(select(WorkOrder).where(WorkOrder.scenario_generation == gen)).all()
              if not (o.lifecycle_status in ("COMPLETED", "CANCELLED") and o.window_end < clock.day_origin)]
    techs = db.scalars(select(Technician).where(Technician.scenario_generation == gen)).all()
    assignments = db.scalars(select(Assignment).where(Assignment.scenario_generation == gen,
                                                      Assignment.status == AssignmentStatus.ACTIVE)).all()
    completed = db.scalars(select(Assignment).where(Assignment.scenario_generation == gen,
                                                    Assignment.status == AssignmentStatus.COMPLETED)).all()
    worked: dict[str, int] = {}
    order_by_id = {o.id: o for o in orders}
    for a in completed:
        o = order_by_id.get(a.order_id)
        if o and o.departed_at and o.completed_at:
            worked[a.technician_id] = worked.get(a.technician_id, 0) + max(0, int((o.completed_at - o.departed_at).total_seconds() // 60))
        else:
            worked[a.technician_id] = worked.get(a.technician_id, 0) + (clock.to_minutes(a.service_end) - clock.to_minutes(a.departure))
    matrix = matrix or get_matrix(db)
    from app.models.entities import BreakBlock
    blocks: dict[str, list[Interval]] = {}
    for b in db.scalars(select(BreakBlock).where(BreakBlock.scenario_generation == gen, BreakBlock.status.in_(["planned", "in_progress"]))).all():
        blocks.setdefault(b.technician_id, []).append((clock.to_minutes(b.start), clock.to_minutes(b.end)))
    snap = Snapshot(
        now=clock.now_minutes,
        orders={o.id: order_spec(clock, o) for o in orders},
        techs={t.id: tech_spec(clock, t, worked.get(t.id, 0), blocks.get(t.id)) for t in techs},
        assignments={},
        matrix=matrix,
        schedule_version=active_schedule_version(db),
        scenario_generation=gen,
        location_names={l.id: l.name for l in db.scalars(select(Location)).all()},
    )
    from app.services.customer_service import preference_penalties
    seen_customers: dict[str, dict[str, float]] = {}
    for o in orders:
        if o.customer_id and o.lifecycle_status not in ("COMPLETED", "CANCELLED"):
            if o.customer_id not in seen_customers:
                seen_customers[o.customer_id] = preference_penalties(db, o.customer_id)
            if seen_customers[o.customer_id]:
                snap.preference_penalties[o.id] = seen_customers[o.customer_id]
    for a in assignments:
        if a.order_id in snap.orders and a.technician_id in snap.techs:
            spec = assign_spec(clock, a)
            # locked = order already departed (EN_ROUTE/ARRIVED/IN_PROGRESS) — execution facts override stored flag
            o = order_by_id[a.order_id]
            if o.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
                spec = Assign(**{**spec.__dict__, "locked": True})
            snap.assignments[a.order_id] = spec
    return snap


def minutes_to_dt(clock: Clock, m: int) -> datetime:
    return clock.from_minutes(m)
