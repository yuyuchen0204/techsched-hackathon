"""Simulated technician app backend (V3 §5): today view, actions with real timestamps, leave, service report, sim mode."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Assignment, BreakBlock, CustomerFeedback, ServiceReport, Technician, WorkOrder
from app.models.enums import AssignmentStatus
from app.services import break_service, catalog_service, customer_service, event_service, execution_service
from app.services.clock import Clock
from app.services.order_service import OrderError
from app.services.timeutil import hhmm, iso


def get_tech(db: Session, tech_id: str) -> Technician:
    t = db.get(Technician, tech_id)
    if t is None:
        raise OrderError("not_found", f"technician {tech_id} not found", status=404)
    return t


def today(db: Session, clock: Clock, tech_id: str) -> dict[str, Any]:
    """Timeline (travel / service / rest / unavailable), next job with full address + unit + history summary."""
    t = get_tech(db, tech_id)
    gen = clock.scenario_generation
    rows = db.scalars(select(Assignment).where(Assignment.technician_id == tech_id, Assignment.scenario_generation == gen,
                                               Assignment.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED]))
                      .order_by(Assignment.departure)).all()
    orders = {o.id: o for o in db.scalars(select(WorkOrder).where(WorkOrder.id.in_([r.order_id for r in rows] or ["-"]))).all()}
    reports = {r.order_id: r for r in db.scalars(select(ServiceReport).where(ServiceReport.order_id.in_([r.order_id for r in rows] or ["-"]))).all()}
    timeline: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for a in rows:
        o = orders.get(a.order_id)
        if o is None:
            continue
        timeline.append({"kind": "travel", "start": iso(a.departure), "end": iso(a.arrival), "order_id": o.id})
        if a.waiting_minutes:
            timeline.append({"kind": "wait", "start": iso(a.arrival), "end": iso(a.service_start), "order_id": o.id})
        timeline.append({"kind": "service", "start": iso(a.service_start), "end": iso(a.service_end), "order_id": o.id,
                         "priority": o.effective_priority, "status": o.lifecycle_status})
        addr = o.address or {}
        jobs.append({
            "order_id": o.id, "lifecycle_status": o.lifecycle_status, "priority": o.effective_priority,
            "problem": f"{o.catalog_snapshot.get('trade_type')} – {o.catalog_snapshot.get('problem_name')}",
            "complexity": o.catalog_snapshot.get("complexity_level"), "duration_minutes": o.catalog_snapshot.get("repair_duration_minutes"),
            "description": o.description, "window": [iso(o.window_start), iso(o.window_end)],
            "planned": {"departure": iso(a.departure), "arrival": iso(a.arrival), "service_start": iso(a.service_start), "service_end": iso(a.service_end),
                        "travel_minutes": a.travel_minutes, "waiting_minutes": a.waiting_minutes},
            "actual": {"departed_at": iso(o.departed_at), "arrived_at": iso(o.arrived_at), "service_started_at": iso(o.service_started_at), "completed_at": iso(o.completed_at)},
            "address": {"formatted_address": addr.get("formatted_address") or o.location_name, "unit_number": addr.get("unit_number"),
                        "unit_not_applicable": addr.get("unit_not_applicable"), "postal_code": addr.get("postal_code"),
                        "building_name": addr.get("building_name"), "lat": o.lat, "lon": o.lon, "location_id": o.location_id},
            "customer_name": o.customer_name, "contact_phone": o.contact_phone, "locked": o.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"),
            "report_status": o.report_status, "report": _report_view(reports.get(o.id)),
            "history_hint": customer_service.repeat_fault_hint(db, clock, o.customer_id, o.location_id, str(o.catalog_snapshot.get("trade_type", "")), exclude_order_id=o.id),
            "next_action": _next_action(o),
        })
    for b in break_service.blocks_for(db, tech_id, gen, ("planned", "in_progress", "done")):
        timeline.append({"kind": "break", "start": iso(b.start), "end": iso(b.end), "status": b.status, "id": b.id})
    for u in t.unavailable_intervals or []:
        timeline.append({"kind": "unavailable", "start": u["start"], "end": u["end"], "reason": u.get("reason")})
    timeline.sort(key=lambda x: x["start"] or "")
    current = next((j for j in jobs if j["locked"]), None)
    nxt = current or next((j for j in jobs if j["lifecycle_status"] == "OPEN"), None)
    facts = break_service.work_facts(db, clock, t)
    from app.models.entities import Notification
    notices = [{"id": n.id, "type": n.type, "order_id": n.order_id, "message": n.message, "at": iso(n.sim_time)} for n in db.scalars(
        select(Notification).where(Notification.scenario_generation == gen, Notification.recipient_type == "technician",
                                   Notification.recipient_ref == tech_id, Notification.type == "schedule_update")
        .order_by(Notification.created_at.desc()).limit(3)).all()]
    return {"technician": {"id": t.id, "name": t.name, "status": t.status, "skills": t.skills, "sim_mode": t.sim_mode,
                           "shift": [iso(t.shift_start), iso(t.shift_end)], "home_location_id": t.home_location_id},
            "sim_now": iso(clock.now), "timeline": timeline, "jobs": jobs, "current_job": current, "next_job": nxt, "notices": notices,
            "break_facts": {k: facts[k] for k in ("work_minutes_since_break", "last_break_end", "level", "in_break", "planned_break_ahead")},
            "mode_note": "manual: only this app moves your status · auto: the simulator follows the plan"}


def _next_action(o: WorkOrder) -> str | None:
    return {"OPEN": "depart", "EN_ROUTE": "arrive", "ARRIVED": "start", "IN_PROGRESS": "complete"}.get(o.lifecycle_status)


def _report_view(r: ServiceReport | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {"id": r.id, "status": r.status, "actual_problem_text": r.actual_problem_text, "matched_catalog_item_id": r.matched_catalog_item_id,
            "resolution": r.resolution, "notes": r.notes, "actual_start_at": iso(r.actual_start_at), "actual_end_at": iso(r.actual_end_at),
            "interruption_minutes": r.interruption_minutes, "anomaly_flags": r.anomaly_flags}


def action(db: Session, clock: Clock, tech_id: str, order_id: str, event: str) -> dict[str, Any]:
    t = get_tech(db, tech_id)
    if t.sim_mode != "manual":
        raise OrderError("invalid_state", "switch this technician to manual mode first (one driver per technician)", status=409, kind="INVALID_STATE")
    res = execution_service.execution_event(db, order_id, event, actor="technician_app", technician_id=tech_id)
    execution_service.scan(db, trigger=f"technician_app_{event}")
    return res


def set_sim_mode(db: Session, tech_id: str, mode: str) -> dict[str, Any]:
    if mode not in ("auto", "manual"):
        raise OrderError("invalid_mode", "mode must be auto or manual", kind="DATA_INCOMPLETE")
    t = get_tech(db, tech_id)
    t.sim_mode = mode  # facts already recorded are kept; only the driver of future transitions changes
    t.version += 1
    db.flush()
    return {"technician_id": tech_id, "sim_mode": mode}


def leave(db: Session, clock: Clock, tech_id: str, *, start: datetime, end: datetime, reason: str, idempotency_key: str | None) -> dict[str, Any]:
    """Technician-declared unavailability = fact (no approval gate). Undeparted tasks are released and recovered; an
    overlap with the executing task becomes a human exception."""
    get_tech(db, tech_id)
    res = event_service.technician_unavailable(db, tech_id, start=start, end=end, reason=reason or "leave", idempotency_key=idempotency_key)
    affected = []
    for r in res.get("released", []):
        o = db.get(WorkOrder, r["order_id"])
        affected.append({**r, "current_status": o.scheduling_status if o else None, "technician": o.technician_id if o else None,
                         "effective_priority": o.effective_priority if o else None})
    return {"technician_id": tech_id, "affected": affected, "interrupted": res.get("interrupted", []), "dispatch": res.get("dispatch", []),
            "note": "your unavailability is recorded immediately; the customers' needs stay open and are being recovered"}


def submit_report(db: Session, clock: Clock, tech_id: str, order_id: str, *, actual_problem_text: str, matched_catalog_item_id: str | None,
                  resolution: str, notes: str, interruption_minutes: int, anomaly_flags: list[str] | None,
                  actual_start_at: datetime | None = None, actual_end_at: datetime | None = None) -> dict[str, Any]:
    o = db.get(WorkOrder, order_id)
    if o is None:
        raise OrderError("not_found", "order not found", status=404)
    if o.technician_id != tech_id:
        raise OrderError("forbidden", "this order belongs to another technician", status=403, kind="FORBIDDEN")
    if o.lifecycle_status not in ("IN_PROGRESS", "COMPLETED"):
        raise OrderError("invalid_state", "report is possible once the service has started", status=409, kind="INVALID_STATE")
    if matched_catalog_item_id and catalog_service.get_item(db, matched_catalog_item_id) is None:
        raise OrderError("invalid_catalog_item", "unknown catalog item", kind="DATA_INCOMPLETE")
    if interruption_minutes < 0:
        raise OrderError("invalid_value", "interruption minutes cannot be negative", kind="DATA_INCOMPLETE")
    r = db.scalars(select(ServiceReport).where(ServiceReport.order_id == order_id)).first()
    if r is None:
        from app.services.ids import new_id
        r = ServiceReport(id=new_id("rep"), order_id=order_id, technician_id=tech_id)
        db.add(r)
    r.actual_problem_text = actual_problem_text.strip()
    r.matched_catalog_item_id = matched_catalog_item_id or None
    r.resolution = resolution
    r.notes = notes
    r.interruption_minutes = interruption_minutes
    flags = list(anomaly_flags or [])
    if actual_start_at is not None:
        r.actual_start_at = actual_start_at
        if o.service_started_at and abs((actual_start_at - o.service_started_at).total_seconds()) > 300:
            flags.append("manual_start_time_differs_from_event")
    if actual_end_at is not None:
        r.actual_end_at = actual_end_at
    r.anomaly_flags = sorted(set(flags))
    r.status = "complete" if actual_problem_text.strip() and resolution else "pending"
    o.report_status = r.status
    db.flush()
    from app.services import duration_service
    duration_service.apply_report(db, o, r)
    return {"order_id": order_id, "report": _report_view(r), "report_status": o.report_status}


def technician_feedback_summary(db: Session, tech_id: str) -> dict[str, Any]:
    rows = db.scalars(select(CustomerFeedback).where(CustomerFeedback.technician_id == tech_id)).all()
    return {"count": len(rows), "avg_rating": round(sum(r.rating for r in rows) / len(rows), 2) if rows else None}


def declare_break(db: Session, clock: Clock, tech_id: str, minutes: int, reason: str) -> dict[str, Any]:
    if minutes <= 0 or minutes > 120:
        raise OrderError("invalid_value", "break minutes must be 1..120", kind="DATA_INCOMPLETE")
    t = get_tech(db, tech_id)
    if t.status in ("EN_ROUTE", "ARRIVED", "BUSY"):
        raise OrderError("invalid_state", "cannot start a rest while travelling or serving", status=409, kind="INVALID_STATE")
    b = break_service.declare_break(db, clock, tech_id, clock.now, clock.from_minutes(clock.now_minutes + minutes), reason)
    execution_service.scan(db, trigger="declared_break")
    return {"break": break_service._view(b), "note": f"rest recorded {hhmm(b.start)}–{hhmm(b.end)}; the risk scan checks later tasks"}


def list_break_blocks(db: Session, clock: Clock, tech_id: str) -> list[dict[str, Any]]:
    return [break_service._view(b) for b in db.scalars(select(BreakBlock).where(BreakBlock.technician_id == tech_id,
                                                                               BreakBlock.scenario_generation == clock.scenario_generation)).all()]
