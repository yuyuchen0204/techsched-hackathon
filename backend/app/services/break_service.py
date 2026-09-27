"""Dynamic rest (V3 §7): work-accumulation facts, zero-disturbance rest trial, protected submit.

Replaces the uniform fixed lunch. Parameters are demo defaults from config/policy.yaml (not labour law / fatigue science).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import Assignment, BreakBlock, Technician
from app.models.enums import AssignmentStatus
from app.scheduling.domain import Snapshot, TechSpec
from app.scheduling.simulator import simulate_route
from app.scheduling.solver import pinned_starts
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.notification_service import notify
from app.services.order_service import OrderError
from app.services.schedule_service import new_version
from app.services.snapshot import build_snapshot
from app.services.timeutil import hhmm, iso


def blocks_for(db: Session, tech_id: str, gen: int, statuses: tuple[str, ...] = ("planned", "in_progress", "done")) -> list[BreakBlock]:
    return list(db.scalars(select(BreakBlock).where(BreakBlock.technician_id == tech_id, BreakBlock.scenario_generation == gen,
                                                    BreakBlock.status.in_(statuses)).order_by(BreakBlock.start)).all())


def work_facts(db: Session, clock: Clock, tech: Technician) -> dict[str, Any]:
    """Cumulative work (travel + service, actual or planned) since the last recorded rest; idle gaps do not count."""
    gen = clock.scenario_generation
    now = clock.now
    blocks = blocks_for(db, tech.id, gen)
    last_break_end: datetime = tech.shift_start
    for b in blocks:
        if b.end <= now and b.end > last_break_end:
            last_break_end = b.end
    current = next((b for b in blocks if b.start <= now < b.end), None)
    rows = db.scalars(select(Assignment).where(Assignment.technician_id == tech.id, Assignment.scenario_generation == gen,
                                               Assignment.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED]))).all()

    def occupied(a: Assignment, lo: datetime, hi: datetime) -> int:
        s, e = max(a.departure, lo), min(a.service_end, hi)
        return max(0, int((e - s).total_seconds() // 60))

    worked = sum(occupied(a, last_break_end, now) for a in rows)
    # projection: continue through planned work until the threshold is reached (or the day ends)
    pol = get_policy().breaks
    future = sorted((a for a in rows if a.service_end > now), key=lambda a: a.departure)
    acc = worked
    projected_at: datetime | None = None
    for a in future:
        seg = occupied(a, now, a.service_end)
        if acc + seg >= pol.evaluate_after_work_minutes:
            projected_at = max(a.departure, now) + (a.service_end - max(a.departure, now)) * (
                (pol.evaluate_after_work_minutes - acc) / seg if seg else 0)
            break
        acc += seg
    level = "none"
    if worked >= pol.escalate_after_work_minutes:
        level = "escalate"
    elif worked >= pol.evaluate_after_work_minutes:
        level = "evaluate"
    elif projected_at is not None and (projected_at - now).total_seconds() / 60 <= pol.pre_evaluate_minutes:
        level = "pre_evaluate"
    planned_ahead = [b for b in blocks if b.start >= now and b.status == "planned"]
    return {
        "technician_id": tech.id, "now": iso(now), "last_break_end": iso(last_break_end), "in_break": current.id if current else None,
        "work_minutes_since_break": worked, "projected_threshold_at": iso(projected_at) if projected_at else None,
        "level": level, "thresholds": pol.model_dump(), "planned_break_ahead": [_view(b) for b in planned_ahead],
        "search_window": [iso(now), iso(now + timedelta(minutes=pol.lookahead_minutes))] if level != "none" else None,
    }


def _tech_with_extra_block(snap: Snapshot, tech_id: str, start_m: int, end_m: int) -> TechSpec:
    t = snap.techs[tech_id]
    return TechSpec(**{**t.__dict__, "unavailable": tuple(sorted(t.unavailable + ((start_m, end_m),)))})


def simulate_break(db: Session, clock: Clock, tech_id: str, start: datetime, minutes: int, snap: Snapshot | None = None) -> dict[str, Any]:
    """Zero-disturbance trial: every pinned start on this technician's route must stay identical."""
    snap = snap or build_snapshot(db, clock)
    if tech_id not in snap.techs:
        raise OrderError("not_found", "technician not found", status=404)
    start_m, end_m = clock.to_minutes(start), clock.to_minutes(start) + minutes
    codes: list[str] = []
    if start_m < snap.now:
        codes.append("BREAK_IN_PAST")
    tech = snap.techs[tech_id]
    if start_m < tech.shift_start or end_m > tech.shift_end:
        codes.append("OUTSIDE_SHIFT")
    for a in snap.route_of(tech_id):
        if a.locked and a.departure < end_m and start_m < a.service_end:
            codes.append("OVERLAPS_EXECUTING_TASK")
    if codes:
        return {"status": "infeasible", "snapshot_version": snap.schedule_version, "reason_codes": codes, "data": {"start": iso(start), "minutes": minutes}}
    movable = snap.movable_route(tech_id)
    pins = pinned_starts(snap)
    rr = simulate_route(snap, _tech_with_extra_block(snap, tech_id, start_m, end_m), [a.order_id for a in movable], pins)
    if not rr.feasible:
        return {"status": "infeasible", "snapshot_version": snap.schedule_version, "reason_codes": ["ROUTE_INFEASIBLE"],
                "data": {"violations": rr.violations}}
    shifted = [(a.order_id, pins[a.order_id], a.service_start) for a in rr.assignments if a.service_start != pins[a.order_id]]
    late = [(a.order_id, a.service_start, snap.orders[a.order_id].window_end) for a in rr.assignments
            if a.service_start > snap.orders[a.order_id].window_end and not snap.orders[a.order_id].recovery_target]
    if shifted or late:
        return {"status": "infeasible", "snapshot_version": snap.schedule_version,
                "reason_codes": ["SUCCESSOR_START_SHIFT" if shifted else "SUCCESSOR_WINDOW_VIOLATION"],
                "data": {"shifted": [{"order_id": o, "planned_start": iso(clock.from_minutes(p)), "new_start": iso(clock.from_minutes(n))} for o, p, n in shifted],
                         "late": [{"order_id": o, "predicted_start": iso(clock.from_minutes(s)), "latest_allowed_start": iso(clock.from_minutes(w))} for o, s, w in late]}}
    return {"status": "feasible", "snapshot_version": snap.schedule_version, "reason_codes": [],
            "data": {"start": iso(start), "end": iso(clock.from_minutes(end_m)), "minutes": minutes, "affected": 0}}


def find_break_slots(db: Session, clock: Clock, tech_id: str, *, minutes: int | None = None, window_start: datetime | None = None,
                     window_end: datetime | None = None, limit: int = 4) -> dict[str, Any]:
    pol = get_policy().breaks
    minutes = minutes or pol.default_break_minutes
    snap = build_snapshot(db, clock)
    tech = snap.techs.get(tech_id)
    if tech is None:
        raise OrderError("not_found", "technician not found", status=404)
    lo = clock.to_minutes(window_start) if window_start else snap.now
    hi = clock.to_minutes(window_end) if window_end else snap.now + pol.lookahead_minutes
    lo = max(lo, snap.now, tech.shift_start)
    hi = min(hi, tech.shift_end - minutes)
    step = pol.slot_step_minutes
    first = ((lo + step - 1) // step) * step
    feasible: list[dict[str, Any]] = []
    rejected: dict[str, int] = {}
    for m in range(first, hi + 1, step):
        r = simulate_break(db, clock, tech_id, clock.from_minutes(m), minutes, snap=snap)
        if r["status"] == "feasible":
            feasible.append(r["data"])
            if len(feasible) >= limit:
                break
        else:
            for c in r["reason_codes"]:
                rejected[c] = rejected.get(c, 0) + 1
    return {"status": "ok" if feasible else "no_slot", "snapshot_version": snap.schedule_version, "reason_codes": list(rejected) if not feasible else [],
            "data": {"slots": feasible, "rejections": rejected, "minutes": minutes, "window": [iso(clock.from_minutes(lo)), iso(clock.from_minutes(hi))]}}


def submit_break(db: Session, clock: Clock, tech_id: str, start: datetime, minutes: int, *, expected_version: int | None,
                 created_by: str, idempotency_key: str | None, reason: str = "dynamic rest") -> dict[str, Any]:
    """Protected write: re-validate zero disturbance against the CURRENT schedule version inside the transaction."""
    if idempotency_key:
        prior = db.scalars(select(BreakBlock).where(BreakBlock.idempotency_key == idempotency_key)).first()
        if prior is not None:
            return {"status": "ok", "idempotent": True, "data": _view(prior)}
    snap = build_snapshot(db, clock)
    if expected_version is not None and expected_version != snap.schedule_version:
        raise OrderError("version_conflict", f"schedule moved from v{expected_version} to v{snap.schedule_version}", status=409, kind="VERSION_CONFLICT")
    trial = simulate_break(db, clock, tech_id, start, minutes, snap=snap)
    if trial["status"] != "feasible":
        raise OrderError("policy_violation", "break is not zero-disturbance at submit time", trial, status=409, kind="POLICY_VIOLATION")
    end = clock.from_minutes(clock.to_minutes(start) + minutes)
    for b in blocks_for(db, tech_id, clock.scenario_generation, ("planned", "in_progress")):
        if b.start < end and start < b.end:
            raise OrderError("invalid_state", "overlaps an existing rest block", status=409, kind="INVALID_STATE")
    version = new_version(db, clock, reason=f"break {tech_id} {iso(start)} ({created_by})", route_snapshot_id=snap.matrix.snapshot_id)
    block = BreakBlock(id=new_id("brk"), scenario_generation=clock.scenario_generation, technician_id=tech_id, start=start, end=end,
                       kind="dynamic", status="planned", reason=reason, schedule_version=version.id, created_by=created_by,
                       idempotency_key=idempotency_key)
    db.add(block)
    tech = db.get(Technician, tech_id)
    if tech is not None:
        tech.version += 1
    db.flush()
    from app.services.schedule_service import snapshot_rows
    version.snapshot = snapshot_rows(db, clock.scenario_generation)
    notify(db, clock, recipient_ref=tech_id, recipient_type="technician", type="break_scheduled",
           message=f"[simulated] Rest scheduled {hhmm(start)}–{hhmm(end)} ({minutes} min). Your later tasks are unchanged.",
           dedupe_key=f"break:{block.id}")
    return {"status": "ok", "idempotent": False, "schedule_version": version.id, "data": _view(block)}


def declare_break(db: Session, clock: Clock, tech_id: str, start: datetime, end: datetime, reason: str) -> BreakBlock:
    """Technician-declared rest fact (already taken / taking now). Not validated as zero-disturbance: it is a fact;
    the risk scan will surface any consequences."""
    b = BreakBlock(id=new_id("brk"), scenario_generation=clock.scenario_generation, technician_id=tech_id, start=start, end=end,
                   kind="declared", status="in_progress" if start <= clock.now < end else ("done" if end <= clock.now else "planned"),
                   reason=reason, created_by="technician")
    db.add(b)
    tech = db.get(Technician, tech_id)
    if tech is not None:
        tech.version += 1
    db.flush()
    return b


def progress_blocks(db: Session, clock: Clock) -> None:
    """planned → in_progress → done as the clock passes; technician status mirrors it."""
    for b in db.scalars(select(BreakBlock).where(BreakBlock.scenario_generation == clock.scenario_generation,
                                                 BreakBlock.status.in_(["planned", "in_progress"]))).all():
        tech = db.get(Technician, b.technician_id)
        if b.status == "planned" and b.start <= clock.now < b.end:
            b.status = "in_progress"
            if tech is not None and tech.status == "AVAILABLE":
                tech.status = "BREAK"
        elif b.end <= clock.now:
            b.status = "done"
            if tech is not None and tech.status == "BREAK":
                tech.status = "AVAILABLE"
    db.flush()


def _view(b: BreakBlock) -> dict[str, Any]:
    return {"id": b.id, "technician_id": b.technician_id, "start": iso(b.start), "end": iso(b.end), "kind": b.kind, "status": b.status,
            "reason": b.reason, "created_by": b.created_by, "schedule_version": b.schedule_version}
