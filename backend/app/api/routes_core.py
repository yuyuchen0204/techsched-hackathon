from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serializers as ser
from app.api.deps import DB, locked
from app.models.entities import (
    AgentRun,
    AgentTask,
    Assignment,
    CandidatePlan,
    HumanCase,
    Location,
    Notification,
    RiskEvent,
    ScheduleVersion,
    StandbyCandidate,
    Technician,
    WorkOrder,
)
from app.models.enums import AssignmentStatus, PlanStatus, RiskStatus
from app.schemas.api import (
    AbortExecutionRequest,
    ApproveRequest,
    CancelRequest,
    DispatchRequest,
    ExecutionEventRequest,
    InitialScheduleRequest,
    OrderCreate,
    OrderPatch,
    PointRequest,
    RejectRequest,
    UnavailabilityRequest,
)
from app.services import (
    break_service,
    catalog_service,
    event_service,
    execution_service,
    order_service,
    plan_service,
    risk_service,
)
from app.services.clock import get_clock
from app.services.order_service import OrderError
from app.services.snapshot import build_snapshot, get_matrix
from app.services.timeutil import hhmm, iso

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ catalog
@router.get("/catalog")
def get_catalog(db: Session = DB, q: str | None = None) -> dict[str, Any]:
    status = catalog_service.catalog_status(db)
    items = catalog_service.search(db, q, limit=50) if q else catalog_service.list_active(db)
    return {"status": status, "items": [ser.catalog_item(i) for i in items]}


@router.post("/catalog/reload")
@locked
def reload_catalog(db: Session) -> dict[str, Any]:
    result = catalog_service.reload_catalog(db)
    return result.summary()


@router.get("/locations")
def get_locations(db: Session = DB, include_custom: bool = True) -> list[dict[str, Any]]:
    rows = db.scalars(select(Location).order_by(Location.name)).all()
    return [{"id": l.id, "name": l.name, "lat": l.lat, "lon": l.lon, "area": l.area}
            for l in rows if include_custom or l.area != "custom"]


@router.post("/locations/resolve")
@locked
def resolve_location_point(body: PointRequest, db: Session) -> dict[str, Any]:
    """Map-picked coordinate → usable location (exact point when the route provider can price it, else nearest preset)."""
    from app.services.location_service import resolve_point
    out = resolve_point(db, body.lat, body.lon, body.name)
    out["arbitrary_points_supported"] = __import__("app.providers.route.factory", fromlist=["supports_arbitrary_points"]).supports_arbitrary_points()
    return out


# ------------------------------------------------------------------ orders
def _order_view(db: Session, o: WorkOrder) -> dict[str, Any]:
    a = db.scalars(select(Assignment).where(Assignment.order_id == o.id, Assignment.status.in_(
        [AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED])).order_by(Assignment.created_at.desc())).first()
    tech = db.get(Technician, a.technician_id) if a else None
    return ser.order(o, a, tech.name if tech else None)


@router.get("/orders")
def list_orders(db: Session = DB, customer_ref: str | None = None, status: str | None = None,
                priority: str | None = None) -> list[dict[str, Any]]:
    clock = get_clock(db)
    rows = order_service.list_orders(db, clock)
    out = []
    for o in rows:
        if customer_ref and o.customer_ref != customer_ref:
            continue
        if status and o.lifecycle_status != status and o.scheduling_status != status:
            continue
        if priority and o.effective_priority != priority:
            continue
        out.append(_order_view(db, o))
    return out


@router.post("/orders", status_code=201)
@locked
def create_order(body: OrderCreate, db: Session) -> dict[str, Any]:
    from app.orchestration.orchestrator import dispatch_order
    from app.services.timeutil import to_utc_naive
    clock = get_clock(db)
    o = order_service.create_order(
        db, clock, customer_ref=body.customer_ref, customer_name=body.customer_name, contact_phone=body.contact_phone,
        description=body.description, location_id=body.location_id, catalog_item_id=body.catalog_item_id,
        window_start=to_utc_naive(body.window_start), window_end=to_utc_naive(body.window_end), paid_expedite=body.paid_expedite,
    )
    dispatch = dispatch_order(db, o.id, trigger="order_created").as_dict() if body.dispatch else None
    return {"order": _order_view(db, o), "dispatch": dispatch}


@router.get("/orders/{order_id}")
def get_order(order_id: str, db: Session = DB) -> dict[str, Any]:
    o = order_service.get_order(db, order_id)
    view = _order_view(db, o)
    view["risks"] = [ser.risk(r) for r in risk_service.active_risks(db, order_id)]
    view["plans"] = [ser.plan(p, get_clock(db)) for p in db.scalars(select(CandidatePlan).where(
        CandidatePlan.target_order_id == order_id).order_by(CandidatePlan.generated_at.desc()).limit(6)).all()]
    return view


@router.patch("/orders/{order_id}")
@locked
def patch_order(order_id: str, body: OrderPatch, db: Session) -> dict[str, Any]:
    o = order_service.get_order(db, order_id)
    if o.lifecycle_status in ("COMPLETED", "CANCELLED"):
        raise OrderError("order_closed", "closed orders cannot be edited", status=409)
    if body.description is not None:
        o.description = body.description
    if body.contact_phone is not None:
        o.contact_phone = body.contact_phone
    o.version += 1
    return _order_view(db, o)


@router.post("/orders/{order_id}/cancel")
@locked
def cancel_order(order_id: str, body: CancelRequest, db: Session) -> dict[str, Any]:
    return event_service.customer_cancel(db, order_id, customer_ref=body.customer_ref, actor=body.actor, reason=body.reason,
                                         idempotency_key=body.idempotency_key)


@router.post("/orders/{order_id}/abort-execution")
@locked
def abort_execution(order_id: str, body: AbortExecutionRequest, db: Session) -> dict[str, Any]:
    """The only way out of a departed order that can no longer be served (technician unavailable mid-visit, no access,
    safety incident): release the execution lock, then re-dispatch or cancel. Dispatcher-only and always audited."""
    return event_service.abort_execution(db, order_id, actor=body.actor, reason=body.reason, outcome=body.outcome,
                                         idempotency_key=body.idempotency_key)


@router.post("/orders/{order_id}/execution-events")
@locked
def execution_event(order_id: str, body: ExecutionEventRequest, db: Session) -> dict[str, Any]:
    result = execution_service.execution_event(db, order_id, body.event, actor=body.actor)
    execution_service.scan(db, trigger=f"execution_{body.event}")
    return result


@router.get("/orders/{order_id}/standby")
def order_standby(order_id: str, db: Session = DB) -> dict[str, Any]:
    o = order_service.get_order(db, order_id)
    rows = db.scalars(select(StandbyCandidate).where(StandbyCandidate.order_id == order_id).order_by(StandbyCandidate.earliest_start)).all()
    a = db.scalars(select(Assignment).where(Assignment.order_id == order_id, Assignment.status == AssignmentStatus.ACTIVE)).first()
    return {"order_id": order_id, "effective_priority": o.effective_priority, "has_valid_assignment": a is not None,
            "current_assignment": ser.assignment_row(a) if a else None, "scheduling_status": o.scheduling_status,
            "candidates": [ser.standby(r) for r in rows], "note": "Standby candidates are not reservations; re-validated before use."}


# ------------------------------------------------------------------ technicians
@router.get("/technicians")
def list_technicians(db: Session = DB) -> list[dict[str, Any]]:
    clock = get_clock(db)
    snap = build_snapshot(db, clock)
    out = []
    for t in db.scalars(select(Technician).where(Technician.scenario_generation == clock.scenario_generation).order_by(Technician.id)).all():
        view = ser.technician(t)
        view["breaks"] = [{"start": iso(b.start), "end": iso(b.end), "status": b.status, "kind": b.kind,
                           "reason": b.reason, "created_by": b.created_by}
                          for b in break_service.blocks_for(db, t.id, clock.scenario_generation)]
        loc, when = snap.anchor(t.id)
        view["anchor"] = {"location_id": loc, "location_name": snap.location_names.get(loc, loc), "time": iso(clock.from_minutes(when))}
        view["route"] = [{"order_id": a.order_id, "departure": iso(clock.from_minutes(a.departure)), "arrival": iso(clock.from_minutes(a.arrival)),
                          "service_start": iso(clock.from_minutes(a.service_start)), "service_end": iso(clock.from_minutes(a.service_end)),
                          "travel_minutes": a.travel, "waiting_minutes": a.waiting, "locked": a.locked,
                          "location_id": snap.orders[a.order_id].location_id, "origin_location_id": a.origin_location_id,
                          "priority": snap.orders[a.order_id].priority.value, "lifecycle_status": snap.orders[a.order_id].lifecycle_status}
                         for a in snap.route_of(t.id)]
        out.append(view)
    return out


@router.post("/technicians/{technician_id}/unavailability")
@locked
def technician_unavailability(technician_id: str, body: UnavailabilityRequest, db: Session) -> dict[str, Any]:
    from app.services.timeutil import to_utc_naive
    return event_service.technician_unavailable(db, technician_id, start=to_utc_naive(body.start), end=to_utc_naive(body.end),
                                                reason=body.reason, idempotency_key=body.idempotency_key)


# ------------------------------------------------------------------ schedules
@router.get("/schedules/current")
def current_schedule(db: Session = DB) -> dict[str, Any]:
    clock = get_clock(db)
    snap = build_snapshot(db, clock)
    version = db.scalars(select(ScheduleVersion).where(ScheduleVersion.active.is_(True)).order_by(ScheduleVersion.id.desc())).first()
    rows = db.scalars(select(Assignment).where(Assignment.scenario_generation == clock.scenario_generation,
                                               Assignment.status == AssignmentStatus.ACTIVE)).all()
    unassigned = [o.id for o in snap.orders.values() if o.id not in snap.assignments and not o.closed]
    return {"version": ser.schedule_version(version) if version else None, "assignments": [ser.assignment_row(a) for a in rows],
            "unassigned_order_ids": unassigned, "route_snapshot": {"id": snap.matrix.snapshot_id, "provider": snap.matrix.provider,
                                                                   "degraded": snap.matrix.degraded, "reason": snap.matrix.degraded_reason},
            "kpis": _kpis(db, snap, rows)}


def _kpis(db: Session, snap, rows) -> dict[str, Any]:
    open_orders = [o for o in snap.orders.values() if not o.closed]
    assigned = [o for o in open_orders if o.id in snap.assignments]
    risks = risk_service.active_risks(db)
    urgent = [o for o in open_orders if o.priority.value in ("P0", "P1")]
    return {
        "orders_open": len(open_orders), "assigned": len(assigned), "unassigned": len(open_orders) - len(assigned),
        "active_risks": len([r for r in risks if r.status == RiskStatus.ACTIVE]), "manual_queue": len([r for r in risks if r.status == RiskStatus.MANUAL]),
        "total_travel_minutes": sum(a.travel_minutes for a in rows), "urgent_orders": len(urgent),
        "pending_review_plans": db.query(CandidatePlan).filter(CandidatePlan.status == PlanStatus.PENDING_REVIEW).count(),
        "completed": len([o for o in snap.orders.values() if o.lifecycle_status == "COMPLETED"]),
        "cancelled": len([o for o in snap.orders.values() if o.lifecycle_status == "CANCELLED"]),
    }


@router.get("/schedules/versions")
def list_versions(db: Session = DB, limit: int = Query(default=30, le=200)) -> list[dict[str, Any]]:
    return [ser.schedule_version(v) for v in db.scalars(select(ScheduleVersion).order_by(ScheduleVersion.id.desc()).limit(limit)).all()]


@router.get("/schedules/{version}")
def get_version(version: int, db: Session = DB) -> dict[str, Any]:
    v = db.get(ScheduleVersion, version)
    if v is None:
        raise OrderError("not_found", f"schedule version {version} not found", status=404)
    out = ser.schedule_version(v)
    out["snapshot"] = v.snapshot
    return out


# ------------------------------------------------------------------ scheduling
@router.post("/scheduling/initial")
@locked
def scheduling_initial(body: InitialScheduleRequest, db: Session) -> dict[str, Any]:
    from app.services.initial_service import run_initial
    return run_initial(db, body.order_ids)


@router.post("/scheduling/dispatch")
@locked
def scheduling_dispatch(body: DispatchRequest, db: Session) -> dict[str, Any]:
    from app.orchestration.orchestrator import dispatch_order
    order_service.get_order(db, body.order_id)
    return dispatch_order(db, body.order_id, trigger=body.trigger).as_dict()


# ------------------------------------------------------------------ risks / plans / runs
def _risk_handling(db: Session, clock, r: RiskEvent) -> dict[str, Any]:
    """What is currently being done about this risk.

    An active risk and an empty review queue are not a contradiction: an auto-committed recovery needs no approval, and
    OVERDUE_NOT_STARTED stays active until the technician actually starts work. Without this the board showed a red P0
    with no sign that a plan already exists, so the dispatcher could not tell "handled" from "ignored".
    """
    none = {"state": "none", "label": "no order attached", "detail": ""}
    if not r.target_order_id:
        return none
    o = db.get(WorkOrder, r.target_order_id)
    if o is None:
        return none
    if o.lifecycle_status in ("COMPLETED", "CANCELLED"):
        return {"state": "closed", "label": f"order {o.lifecycle_status.lower()}", "detail": "", "order_status": o.lifecycle_status}
    out: dict[str, Any] = {"order_status": o.lifecycle_status}
    plan = db.scalars(select(CandidatePlan).where(CandidatePlan.target_order_id == o.id,
                                                  CandidatePlan.status == PlanStatus.PENDING_REVIEW)
                      .order_by(CandidatePlan.generated_at.desc())).first()
    if plan is not None:
        return {**out, "state": "pending_review", "label": "waiting for your approval",
                "detail": f"{plan.strategy} · score {plan.decision_score:.1f} · moves {len(plan.affected_order_ids or [])} other order(s)"
                          if plan.decision_score is not None else plan.strategy,
                "plan_id": plan.id, "run_id": plan.run_id, "decision_score": plan.decision_score,
                "affected_count": len(plan.affected_order_ids or [])}
    case = db.get(HumanCase, o.human_case_id) if o.human_case_id else None
    if case is not None and case.status != "resolved":
        return {**out, "state": "with_human", "label": f"with a human · {case.status.replace('_', ' ')}"
                       + (f" · {case.assignee}" if case.assignee else " · unassigned"),
                "detail": case.reason_summary.splitlines()[0] if case.reason_summary else "",
                "human_case_id": case.id, "urgency": case.urgency}
    if o.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
        return {**out, "state": "executing", "label": f"technician {o.lifecycle_status.lower().replace('_', ' ')}",
                "detail": "execution is locked; use “Stop this visit” on the order to release it"}
    a = db.scalars(select(Assignment).where(Assignment.order_id == o.id, Assignment.status == AssignmentStatus.ACTIVE)).first()
    if a is not None:
        tech = db.get(Technician, a.technician_id)
        late = a.service_start > o.window_end
        return {**out, "state": "scheduled", "label": f"{'recovery ' if late or o.recovery_start else ''}scheduled · "
                       f"{tech.name if tech else a.technician_id} · start {hhmm(a.service_start)}",
                "detail": f"leaves {hhmm(a.departure)}, arrives {hhmm(a.arrival)}"
                          + (" — after the promised window" if late else ""),
                "technician_id": a.technician_id, "technician_name": tech.name if tech else None,
                "planned_start": iso(a.service_start)}
    task = db.scalars(select(AgentTask).where(AgentTask.order_id == o.id,
                                              AgentTask.status.in_(["pending", "running", "waiting_customer", "waiting_human"]))
                      .order_by(AgentTask.created_at.desc())).first()
    if task is not None:
        return {**out, "state": "searching", "label": f"agent working on it · {task.status.replace('_', ' ')}",
                "detail": task.goal, "task_id": task.id}
    return {**out, "state": "unresolved", "label": "no feasible plan yet",
            "detail": "no technician can take it inside the current authority; needs a human decision or a changed window"}


@router.get("/risks")
def list_risks(db: Session = DB, include_resolved: bool = False) -> list[dict[str, Any]]:
    clock = get_clock(db)
    if include_resolved:
        rows = db.scalars(select(RiskEvent).order_by(RiskEvent.first_seen.desc()).limit(200)).all()
    else:
        rows = risk_service.active_risks(db)
    return [{**ser.risk(r), "handling": _risk_handling(db, clock, r)} for r in rows]


@router.get("/plans")
def list_plans(db: Session = DB, status: str | None = None, limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    clock = get_clock(db)
    q = select(CandidatePlan).where(CandidatePlan.scenario_generation == clock.scenario_generation)
    if status:
        q = q.where(CandidatePlan.status == status)
    rows = db.scalars(q.order_by(CandidatePlan.generated_at.desc()).limit(limit)).all()
    return [ser.plan(p, clock) for p in rows]


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, db: Session = DB) -> dict[str, Any]:
    return ser.plan(plan_service.get_plan(db, plan_id), get_clock(db))


@router.post("/plans/{plan_id}/approve")
@locked
def approve(plan_id: str, body: ApproveRequest, db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    result = plan_service.approve_plan(db, clock, plan_id, actor=body.actor, reason=body.reason,
                                       idempotency_key=body.idempotency_key, expected_versions=body.expected_versions)
    execution_service.scan(db, trigger="post_approval")
    return result


@router.post("/plans/{plan_id}/reject")
@locked
def reject(plan_id: str, body: RejectRequest, db: Session) -> dict[str, Any]:
    return plan_service.reject_plan(db, get_clock(db), plan_id, actor=body.actor, reason=body.reason, idempotency_key=body.idempotency_key)


@router.post("/plans/{plan_id}/recompute")
@locked
def recompute(plan_id: str, db: Session) -> dict[str, Any]:
    from app.orchestration.orchestrator import dispatch_order
    p = plan_service.get_plan(db, plan_id)
    for sib in db.scalars(select(CandidatePlan).where(CandidatePlan.run_id == p.run_id)).all():
        if sib.status in (PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED, PlanStatus.OVER_LIMIT, PlanStatus.EXPIRED):
            sib.status = PlanStatus.SUPERSEDED
            sib.status_reason = "recomputed"
    return dispatch_order(db, p.target_order_id, trigger="recompute").as_dict()


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = DB) -> dict[str, Any]:
    r = db.get(AgentRun, run_id)
    if r is None:
        raise OrderError("not_found", f"run {run_id} not found", status=404)
    out = ser.agent_run(r)
    clock = get_clock(db)
    out["plans"] = [ser.plan(p, clock) for p in db.scalars(select(CandidatePlan).where(CandidatePlan.run_id == run_id)).all()]
    return out


@router.get("/agent-runs")
def list_runs(db: Session = DB, limit: int = Query(default=40, le=200)) -> list[dict[str, Any]]:
    clock = get_clock(db)
    rows = db.scalars(select(AgentRun).where(AgentRun.scenario_generation == clock.scenario_generation)
                      .order_by(AgentRun.started_at.desc()).limit(limit)).all()
    return [ser.agent_run(r) for r in rows]


@router.get("/notifications")
def list_notifications(db: Session = DB, recipient_ref: str | None = None, limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    clock = get_clock(db)
    q = select(Notification).where(Notification.scenario_generation == clock.scenario_generation)
    if recipient_ref:
        q = q.where(Notification.recipient_ref == recipient_ref)
    return [ser.notification(n) for n in db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()]


@router.get("/routes/geometry")
def route_geometry(from_id: str, to_id: str, db: Session = DB) -> dict[str, Any]:
    from app.providers.route.base import LocationPoint
    from app.providers.route.factory import leg_geometry
    a, b = db.get(Location, from_id), db.get(Location, to_id)
    if a is None or b is None:
        raise OrderError("not_found", "unknown location", status=404)
    g = leg_geometry(LocationPoint(a.id, a.name, a.lat, a.lon), LocationPoint(b.id, b.name, b.lat, b.lon))
    m = get_matrix(db)
    return {"from_id": a.id, "to_id": b.id, "points": g.points, "schematic": g.schematic, "provider": g.provider,
            "minutes": m.travel(a.id, b.id), "provider_minutes": g.minutes, "distance_km": g.distance_km}


@router.get("/routes/technician/{technician_id}")
def technician_route_geometry(technician_id: str, db: Session = DB) -> dict[str, Any]:
    """Road geometry of the technician's current route (anchor → each stop) for the map. Cached per leg."""
    from app.providers.route.base import LocationPoint
    from app.providers.route.factory import leg_geometry
    clock = get_clock(db)
    snap = build_snapshot(db, clock)
    if technician_id not in snap.techs:
        raise OrderError("not_found", "unknown technician", status=404)
    locs = {l.id: l for l in db.scalars(select(Location)).all()}
    legs: list[dict[str, Any]] = []
    prev_id: str | None = None
    for a in snap.route_of(technician_id):
        origin = a.origin_location_id if prev_id is None else prev_id
        dest = snap.orders[a.order_id].location_id
        prev_id = dest
        if origin == dest or origin not in locs or dest not in locs:
            continue
        la, lb = locs[origin], locs[dest]
        g = leg_geometry(LocationPoint(la.id, la.name, la.lat, la.lon), LocationPoint(lb.id, lb.name, lb.lat, lb.lon))
        legs.append({"order_id": a.order_id, "from_id": origin, "to_id": dest, "points": g.points, "schematic": g.schematic,
                     "provider": g.provider, "minutes": a.travel, "provider_minutes": g.minutes, "distance_km": g.distance_km,
                     "locked": a.locked})
    return {"technician_id": technician_id, "legs": legs, "route_provider": snap.matrix.provider, "degraded": snap.matrix.degraded}
