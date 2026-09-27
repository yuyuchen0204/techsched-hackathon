"""V3 endpoints: customer app, technician app, human cases, safety incidents, agent tasks, positions, dev views."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import runtime
from app.api.deps import DB, locked
from app.models.entities import (
    AgentTask,
    Customer,
    CustomerFeedback,
    HumanCase,
    RescheduleRequest,
    SafetyIncident,
    ServiceReport,
    Technician,
    ToolTrace,
    WorkOrder,
)
from app.services import (
    break_service,
    chat_service,
    customer_service,
    duration_service,
    event_service,
    expedite_service,
    human_service,
    negotiation_service,
    position_service,
    safety_service,
    technician_service,
)
from app.services.clock import get_clock
from app.services.order_service import OrderError
from app.services.timeutil import iso, to_utc_naive

router = APIRouter(prefix="/api")


# ================================================================== customer
class IdentifyRequest(BaseModel):
    session_id: str
    demo_login: str = Field(min_length=1, max_length=40)
    name: str | None = None
    phone: str | None = None


class AddressConfirm(BaseModel):
    session_id: str
    latitude: float
    longitude: float
    formatted_address: str
    postal_code: str | None = None
    building_name: str | None = None
    street_address: str | None = None
    unit_number: str | None = None
    unit_not_applicable: bool = False
    source: str = "search"
    save: bool = True


class FeedbackRequest(BaseModel):
    session_id: str
    rating: int = Field(ge=1, le=5)
    target: Literal["technician", "dispatch", "platform"] = "technician"
    reasons: list[str] = []
    comment: str = ""


class HandoffRequest(BaseModel):
    session_id: str
    reason: str = "customer asked for a human"
    category: str = "customer_request"


class RescheduleReq(BaseModel):
    session_id: str
    window_start: datetime
    window_end: datetime
    reason: str = ""


class CustomerActionRequest(BaseModel):
    session_id: str
    reason: str | None = None
    text: str | None = None
    complaint_type: str | None = None


@router.get("/customer/address/search")
def address_search(q: str, db: Session = DB) -> dict[str, Any]:
    from app.providers.geocode.factory import enabled, geocode, status
    if not enabled():
        return {"results": [], "provider": None, "note": "address lookup disabled (GEOCODE_MODE=none) — pick on the map or choose a service area", "status": status()}
    out = geocode(q, limit=6)
    return {"results": [r.as_dict() for r in out.results], "provider": out.provider, "degraded": out.degraded, "reason": out.reason, "cached": out.cached}


@router.post("/customer/address/confirm")
@locked
def address_confirm(body: AddressConfirm, db: Session) -> dict[str, Any]:
    return chat_service.handle(db, body.session_id, "", "set_address", body.model_dump(exclude={"session_id"}))


@router.get("/customer/session/{session_id}/orders")
def customer_orders(session_id: str, db: Session = DB) -> list[dict[str, Any]]:
    clock = get_clock(db)
    s = chat_service.get_session(db, session_id, clock)
    return chat_service.session_orders(db, clock, session_id, (s.draft or {}).get("customer_id"))


@router.get("/customer/orders/{order_id}")
def customer_order_detail(order_id: str, session_id: str, db: Session = DB) -> dict[str, Any]:
    clock = get_clock(db)
    s = chat_service.get_session(db, session_id, clock)
    cid = (s.draft or {}).get("customer_id")
    o = chat_service.owned_order(db, order_id, session_id, cid)
    st = chat_service._order_status(db, clock, o)
    case = db.get(HumanCase, o.human_case_id) if o.human_case_id else None
    fb = db.scalars(select(CustomerFeedback).where(CustomerFeedback.order_id == o.id)).first()
    reqs = db.scalars(select(RescheduleRequest).where(RescheduleRequest.order_id == o.id).order_by(RescheduleRequest.created_at.desc())).all()
    from app.services import risk_service
    progress = []
    for e in db.scalars(select(__import__("app.models.entities", fromlist=["ExecutionEvent"]).ExecutionEvent).where(
            __import__("app.models.entities", fromlist=["ExecutionEvent"]).ExecutionEvent.order_id == o.id)).all():
        progress.append({"event": e.event, "at": iso(e.sim_time), "source": e.source})
    return {**st, "description": o.description, "address_full": o.address, "customer_name": o.customer_name, "contact_phone": o.contact_phone,
            "catalog_snapshot": o.catalog_snapshot, "created_at": iso(o.created_at), "progress": sorted(progress, key=lambda x: x["at"] or ""),
            "risks": [{"type": r.type, "severity": r.severity} for r in risk_service.active_risks(db, o.id)],
            "human_case": human_service.case_view(case) if case else None,
            "feedback": {"rating": fb.rating, "target": fb.target, "reasons": fb.reasons, "comment": fb.comment} if fb else None,
            "reschedule_requests": [{"id": r.id, "status": r.status, "window": [iso(r.requested_window_start), iso(r.requested_window_end)]} for r in reqs],
            "tracking": position_service.tracking_for_order(db, clock, o),
            "actions": {"cancel": o.lifecycle_status == "OPEN", "expedite": o.lifecycle_status == "OPEN" and not o.paid_expedite and o.effective_priority not in ("P0", "P1"),
                        "keep_original_time": o.lifecycle_status == "OPEN" and (o.expedite_state or {}).get("status") == "moved",
                        "complain": o.lifecycle_status != "CANCELLED", "rate": o.lifecycle_status == "COMPLETED" and fb is None,
                        "reschedule": o.lifecycle_status == "OPEN", "handoff": True}}


@router.get("/customer/orders/{order_id}/expedite-preview")
def customer_expedite_preview(order_id: str, session_id: str, db: Session = DB) -> dict[str, Any]:
    """Read-only trial: the earliest start an expedite would reach right now (nothing is reserved or changed)."""
    clock = get_clock(db)
    s = chat_service.get_session(db, session_id, clock)
    o = chat_service.owned_order(db, order_id, session_id, (s.draft or {}).get("customer_id"))
    return expedite_service.preview(db, clock, o)


@router.post("/customer/orders/{order_id}/cancel")
@locked
def customer_cancel(order_id: str, body: CustomerActionRequest, db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    s = chat_service.get_session(db, body.session_id, clock)
    chat_service.owned_order(db, order_id, body.session_id, (s.draft or {}).get("customer_id"))
    return event_service.customer_cancel(db, order_id, customer_ref=None, actor="customer_app", reason=body.reason, idempotency_key=f"cancel:{order_id}")


@router.post("/customer/orders/{order_id}/expedite")
@locked
def customer_expedite(order_id: str, body: CustomerActionRequest, db: Session) -> dict[str, Any]:
    """Expedite = simulated payment (P1) + automatic move to the earliest start, in one atomic operation. Idempotent."""
    clock = get_clock(db)
    s = chat_service.get_session(db, body.session_id, clock)
    o = chat_service.owned_order(db, order_id, body.session_id, (s.draft or {}).get("customer_id"))
    return expedite_service.expedite(db, clock, o, actor="customer_app", idempotency_key=f"expedite:{order_id}")


@router.post("/customer/orders/{order_id}/keep-original-time")
@locked
def customer_keep_original_time(order_id: str, body: CustomerActionRequest, db: Session) -> dict[str, Any]:
    """Undo the expedite move: original window back (P1 kept, no refund — simulated). Idempotent."""
    clock = get_clock(db)
    s = chat_service.get_session(db, body.session_id, clock)
    o = chat_service.owned_order(db, order_id, body.session_id, (s.draft or {}).get("customer_id"))
    key = f"expedite_restore:{order_id}:{(o.expedite_state or {}).get('moved_at') or ''}"
    return expedite_service.keep_original_time(db, clock, o, actor="customer_app", idempotency_key=key)


@router.post("/customer/orders/{order_id}/complaint")
def customer_complaint(order_id: str, body: CustomerActionRequest) -> dict[str, Any]:
    from app.db.session import session_scope
    payload = {"order_id": order_id, "text": body.text or ""}
    try:
        with session_scope() as ro:  # model classification without the state lock
            pre = chat_service.pre_model_work(ro, body.session_id, body.text or "", "complaint", payload)
    except Exception:  # noqa: BLE001
        pre = {}
    return _customer_complaint_locked(body, payload, pre)


@locked
def _customer_complaint_locked(body: CustomerActionRequest, payload: dict[str, Any], pre: dict[str, Any], db: Session) -> dict[str, Any]:
    return chat_service.handle(db, body.session_id, body.text or "", "complaint", payload, pre=pre)


@router.post("/customer/orders/{order_id}/feedback")
@locked
def customer_feedback(order_id: str, body: FeedbackRequest, db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    s = chat_service.get_session(db, body.session_id, clock)
    o = chat_service.owned_order(db, order_id, body.session_id, (s.draft or {}).get("customer_id"))
    fb = customer_service.add_feedback(db, clock, o, rating=body.rating, target=body.target, reasons=body.reasons, comment=body.comment)
    if body.rating <= 2 and body.target in ("dispatch", "platform"):
        human_service.flag_for_human(db, clock, source="CUSTOMER_REQUEST", category=f"low_rating_{body.target}", urgency="normal",
                                     reason_summary=f"{body.rating}/5 on {order_id} ({body.target}): {body.comment[:160]}", customer_id=o.customer_id,
                                     session_id=body.session_id, order_id=order_id, idempotency_key=f"rating:{order_id}")
    return {"order_id": order_id, "rating": fb.rating, "target": fb.target, "reasons": fb.reasons, "note": "technician-targeted low ratings become a soft preference for this customer's future orders"}


@router.post("/customer/orders/{order_id}/handoff")
@locked
def customer_handoff(order_id: str, body: HandoffRequest, db: Session) -> dict[str, Any]:
    return chat_service.handle(db, body.session_id, body.reason, "handoff", {"order_id": order_id, "reason": body.reason, "category": body.category})


@router.post("/customer/orders/{order_id}/reschedule")
@locked
def customer_reschedule(order_id: str, body: RescheduleReq, db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    s = chat_service.get_session(db, body.session_id, clock)
    cid = (s.draft or {}).get("customer_id")
    o = chat_service.owned_order(db, order_id, body.session_id, cid)
    return negotiation_service.request_reschedule(db, clock, o, window_start=to_utc_naive(body.window_start), window_end=to_utc_naive(body.window_end),
                                                  reason=body.reason, customer_id=cid, session_id=body.session_id)


@router.get("/customer/orders/{order_id}/tracking")
def customer_tracking(order_id: str, session_id: str, db: Session = DB) -> dict[str, Any]:
    clock = get_clock(db)
    s = chat_service.get_session(db, session_id, clock)
    o = chat_service.owned_order(db, order_id, session_id, (s.draft or {}).get("customer_id"))
    return position_service.tracking_for_order(db, clock, o)


@router.get("/customer/logins")
def customer_logins(db: Session = DB) -> list[dict[str, Any]]:
    """Demo device switcher only — which simulated person is holding the phone. Deliberately NOT a customer directory:
    no phone numbers and no order counts, because this endpoint is reachable by anyone who opens the customer app.
    Contact details and history are returned only after identifying as that person (POST /api/chat, action=identify)."""
    clock = get_clock(db)
    rows = db.scalars(select(Customer).where(Customer.scenario_generation == clock.scenario_generation, Customer.demo_login.isnot(None))).all()
    return [{"demo_login": c.demo_login, "name": c.name,
             "returning": db.scalars(select(WorkOrder.id).where(WorkOrder.customer_id == c.id).limit(1)).first() is not None}
            for c in rows]


# ================================================================== technician app
class LeaveRequest(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    reason: str = "leave"
    idempotency_key: str | None = None


class ReportRequest(BaseModel):
    actual_problem_text: str = ""
    matched_catalog_item_id: str | None = None
    resolution: Literal["fixed", "partial", "needs_parts", "not_fixed", ""] = ""
    notes: str = ""
    interruption_minutes: int = Field(default=0, ge=0, le=600)
    anomaly_flags: list[str] = []
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None


class ModeRequest(BaseModel):
    mode: Literal["auto", "manual"]


class DeclareBreakRequest(BaseModel):
    minutes: int = Field(default=30, ge=5, le=120)
    reason: str = "rest"


@router.get("/technician/{technician_id}/today")
def technician_today(technician_id: str, db: Session = DB) -> dict[str, Any]:
    return technician_service.today(db, get_clock(db), technician_id)


@router.post("/technician/{technician_id}/orders/{order_id}/{event}")
@locked
def technician_action(technician_id: str, order_id: str, event: Literal["depart", "arrive", "start", "complete"], db: Session) -> dict[str, Any]:
    return technician_service.action(db, get_clock(db), technician_id, order_id, event)


@router.post("/technician/{technician_id}/mode")
@locked
def technician_mode(technician_id: str, body: ModeRequest, db: Session) -> dict[str, Any]:
    return technician_service.set_sim_mode(db, technician_id, body.mode)


@router.post("/technician/{technician_id}/leave")
@locked
def technician_leave(technician_id: str, body: LeaveRequest, db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    start = to_utc_naive(body.start) if body.start else clock.now
    end = to_utc_naive(body.end) if body.end else clock.from_minutes(clock.now_minutes + 12 * 60)
    res = technician_service.leave(db, clock, technician_id, start=start, end=end, reason=body.reason, idempotency_key=body.idempotency_key)
    runtime.run_pending(db)
    return res


@router.post("/technician/{technician_id}/orders/{order_id}/report")
@locked
def technician_report(technician_id: str, order_id: str, body: ReportRequest, db: Session) -> dict[str, Any]:
    return technician_service.submit_report(db, get_clock(db), technician_id, order_id, actual_problem_text=body.actual_problem_text,
                                            matched_catalog_item_id=body.matched_catalog_item_id, resolution=body.resolution, notes=body.notes,
                                            interruption_minutes=body.interruption_minutes, anomaly_flags=body.anomaly_flags,
                                            actual_start_at=to_utc_naive(body.actual_start_at) if body.actual_start_at else None,
                                            actual_end_at=to_utc_naive(body.actual_end_at) if body.actual_end_at else None)


@router.post("/technician/{technician_id}/break")
@locked
def technician_break(technician_id: str, body: DeclareBreakRequest, db: Session) -> dict[str, Any]:
    return technician_service.declare_break(db, get_clock(db), technician_id, body.minutes, body.reason)


@router.get("/technician/{technician_id}/break-facts")
def technician_break_facts(technician_id: str, db: Session = DB) -> dict[str, Any]:
    t = technician_service.get_tech(db, technician_id)
    return break_service.work_facts(db, get_clock(db), t)


@router.get("/technician/logins")
def technician_logins(db: Session = DB) -> list[dict[str, Any]]:
    clock = get_clock(db)
    return [{"id": t.id, "name": t.name, "status": t.status, "sim_mode": t.sim_mode}
            for t in db.scalars(select(Technician).where(Technician.scenario_generation == clock.scenario_generation).order_by(Technician.id)).all()]


# ================================================================== positions
@router.get("/positions")
def positions(db: Session = DB) -> dict[str, Any]:
    clock = get_clock(db)
    return {"sim_now": iso(clock.now), "running": clock.running, "technicians": position_service.positions(db, clock)}


# ================================================================== human cases & safety
class CaseReply(BaseModel):
    text: str
    author: str = "dispatcher"


class CaseResolve(BaseModel):
    resolution: str
    author: str = "dispatcher"


@router.get("/human-cases")
def list_cases(db: Session = DB, status: str | None = None, limit: int = Query(default=50, le=200)) -> list[dict[str, Any]]:
    clock = get_clock(db)
    q = select(HumanCase).where(HumanCase.scenario_generation == clock.scenario_generation)
    if status:
        q = q.where(HumanCase.status == status)
    return [human_service.case_view(c) for c in db.scalars(q.order_by(HumanCase.created_at.desc()).limit(limit)).all()]


@router.get("/human-cases/{case_id}")
def get_case(case_id: str, db: Session = DB) -> dict[str, Any]:
    c = human_service.get_case(db, case_id)
    view = human_service.case_view(c)
    if c.session_id:
        s = db.get(__import__("app.models.entities", fromlist=["ChatSession"]).ChatSession, c.session_id)
        view["conversation"] = [{"role": m["role"], "text": m["text"], "at": m.get("at")} for m in (s.messages or [])[-12:]] if s else []
    if c.order_id:
        o = db.get(WorkOrder, c.order_id)
        view["order"] = {"id": o.id, "status": o.lifecycle_status, "scheduling": o.scheduling_status, "priority": o.effective_priority,
                         "problem": o.catalog_snapshot.get("problem_name"), "window": [iso(o.window_start), iso(o.window_end)]} if o else None
    if c.category == "reschedule_request":
        from app.models.entities import RescheduleRequest
        req = db.scalars(select(RescheduleRequest).where(RescheduleRequest.human_case_id == case_id)
                         .order_by(RescheduleRequest.created_at.desc())).first()
        if req is not None:
            view["reschedule_request"] = {"id": req.id, "status": req.status, "reason": req.reason,
                                          "window": [iso(req.requested_window_start), iso(req.requested_window_end)]}
            if req.status == "pending":
                try:
                    view["reschedule_preview"] = negotiation_service.preview_reschedule(db, get_clock(db), req.id)
                except OrderError as exc:       # a stale request must not break the whole case view
                    view["reschedule_preview"] = {"feasible": False, "reason": exc.message}
    tasks = db.scalars(select(AgentTask).where(AgentTask.human_case_id == case_id)).all()
    view["agent_tasks"] = [runtime.task_view(db, t, with_traces=True) for t in tasks]
    return view


@router.post("/human-cases/{case_id}/take")
@locked
def take_case(case_id: str, body: CaseReply, db: Session) -> dict[str, Any]:
    return human_service.case_view(human_service.take(db, get_clock(db), case_id, body.author))


@router.post("/human-cases/{case_id}/reply")
@locked
def reply_case(case_id: str, body: CaseReply, db: Session) -> dict[str, Any]:
    return human_service.case_view(human_service.reply(db, get_clock(db), case_id, text=body.text, author=body.author))


@router.post("/human-cases/{case_id}/resolve")
@locked
def resolve_case(case_id: str, body: CaseResolve, db: Session) -> dict[str, Any]:
    c = human_service.resolve(db, get_clock(db), case_id, resolution=body.resolution, author=body.author)
    runtime.run_pending(db)
    return human_service.case_view(c)


@router.get("/reschedule-requests/{request_id}/preview")
def preview_reschedule(request_id: str, db: Session = DB) -> dict[str, Any]:
    """Read-only trial of the requested window, so the dispatcher can see the answer before committing to it."""
    return negotiation_service.preview_reschedule(db, get_clock(db), request_id)


@router.post("/reschedule-requests/{request_id}/decline")
@locked
def decline_reschedule(request_id: str, body: CaseResolve, db: Session) -> dict[str, Any]:
    """Keep the original appointment and tell the customer why."""
    return negotiation_service.decline_reschedule(db, get_clock(db), request_id, author=body.author, reason=body.resolution)


@router.post("/reschedule-requests/{request_id}/approve")
@locked
def approve_reschedule(request_id: str, body: CaseResolve, db: Session) -> dict[str, Any]:
    return negotiation_service.approve_reschedule(db, get_clock(db), request_id, author=body.author)


@router.get("/safety-incidents")
def list_incidents(db: Session = DB) -> list[dict[str, Any]]:
    clock = get_clock(db)
    return [safety_service.incident_view(i) for i in db.scalars(select(SafetyIncident).where(SafetyIncident.scenario_generation == clock.scenario_generation)
                                                                .order_by(SafetyIncident.created_at.desc())).all()]


@router.post("/safety-incidents/{incident_id}/resolve")
@locked
def resolve_incident(incident_id: str, body: CaseResolve, db: Session) -> dict[str, Any]:
    return safety_service.incident_view(safety_service.resolve_incident(db, get_clock(db), incident_id, resolution=body.resolution, author=body.author))


# ================================================================== agent tasks (activity panel)
@router.get("/agent-tasks")
def list_tasks(db: Session = DB, limit: int = Query(default=40, le=200), status: str | None = None) -> list[dict[str, Any]]:
    clock = get_clock(db)
    q = select(AgentTask).where(AgentTask.scenario_generation == clock.scenario_generation)
    if status:
        q = q.where(AgentTask.status == status)
    return [runtime.task_view(db, t, with_traces=False) for t in db.scalars(q.order_by(AgentTask.created_at.desc()).limit(limit)).all()]


@router.get("/agent-tasks/{task_id}")
def get_task(task_id: str, db: Session = DB) -> dict[str, Any]:
    t = db.get(AgentTask, task_id)
    if t is None:
        raise OrderError("not_found", "task not found", status=404)
    return runtime.task_view(db, t, with_traces=True)


@router.post("/agent-tasks/{task_id}/run")
@locked
def run_task_now(task_id: str, db: Session) -> dict[str, Any]:
    t = db.get(AgentTask, task_id)
    if t is None:
        raise OrderError("not_found", "task not found", status=404)
    if t.status not in ("pending",):
        raise OrderError("invalid_state", f"task is {t.status}", status=409, kind="INVALID_STATE")
    runtime.run_task(db, t)
    return runtime.task_view(db, t)


# ================================================================== agent skills + scorecard
@router.get("/agent-skills")
def agent_skills() -> dict[str, Any]:
    """What each agent role was actually told, and which tools its skill withholds from it."""
    from app.agents.skills import load_skills
    from app.agents.tools import TOOLS
    out = []
    for skill in load_skills().values():
        by_role = {t.name for t in TOOLS.values() if skill.name in t.roles}
        allowed = set(skill.tools) or by_role
        out.append({**skill.as_dict(), "tools": sorted(allowed),
                    "withheld_by_skill": sorted(by_role - allowed),
                    "declared_but_not_permitted": sorted(allowed - by_role)})
    return {"skills": sorted(out, key=lambda s: s["name"]), "directory": "config/agent_skills"}


@router.post("/agent-skills/reload")
def agent_skills_reload() -> dict[str, Any]:
    """Re-read the skill files so a playbook can be edited and demonstrated without restarting the backend."""
    from app.agents.skills import load_skills, reset_skills_cache
    reset_skills_cache()
    return {"reloaded": sorted(load_skills())}


@router.get("/agent-scorecard")
def agent_scorecard(db: Session = DB) -> dict[str, Any]:
    from app.services import agent_metrics
    return agent_metrics.scorecard(db)


class SuperviseIn(BaseModel):
    order_ids: list[str] = Field(default_factory=list)
    trigger: str = "manual"


@router.post("/agent-tasks/supervise")
@locked
def supervise(body: SuperviseIn, db: Session) -> dict[str, Any]:
    """Open a supervisor task over several broken orders: it delegates one recovery agent per order and reports once."""
    clock = get_clock(db)
    ids = body.order_ids or [o.id for o in db.scalars(
        select(WorkOrder).where(WorkOrder.scheduling_status.in_(["UNRESOLVED", "UNASSIGNED"]),
                                WorkOrder.lifecycle_status == "OPEN",
                                WorkOrder.scenario_generation == clock.scenario_generation)).all()]
    if not ids:
        raise OrderError("invalid_state", "no orders to supervise", status=409, kind="NO_TARGETS")
    task = runtime.create_task(db, clock, role="dispatcher",
                               goal=f"supervise the recovery of {len(ids)} order(s) after {body.trigger}",
                               facts={"order_ids": ids, "trigger": body.trigger},
                               dedupe_key=f"supervise:{body.trigger}:{','.join(sorted(ids))}")
    if task is None:
        raise OrderError("invalid_state", "a supervisor task is already live for these orders", status=409, kind="ALREADY_LIVE")
    if task.execution_mode == "mock":
        runtime.run_task(db, task)
        runtime.run_pending(db, inline_only=True)
    return runtime.task_view(db, task)


@router.get("/dev/duration-report")
def duration_report(db: Session = DB) -> dict[str, Any]:
    from app.models.entities import DurationObservation
    rep = duration_service.latest_report(db)
    obs = db.scalars(select(DurationObservation).order_by(DurationObservation.observed_at.desc()).limit(50)).all()
    return {"report": rep, "recent_observations": [{"order_id": o.order_id, "problem": o.problem_name, "baseline": o.baseline_minutes, "actual": o.actual_minutes,
                                                   "interruptions": o.interruption_minutes, "quality": o.quality, "source": o.source, "prediction": o.prediction_minutes,
                                                   "prediction_version": o.prediction_version, "observed_at": iso(o.observed_at)} for o in obs]}


@router.post("/dev/duration-report/build")
@locked
def duration_report_build(db: Session) -> dict[str, Any]:
    mv = duration_service.build_model_version(db, get_clock(db))
    return duration_service.latest_report(db) or {"id": mv.id}


@router.get("/dev/tool-traces")
def tool_traces(db: Session = DB, limit: int = Query(default=100, le=500)) -> list[dict[str, Any]]:
    rows = db.scalars(select(ToolTrace).order_by(ToolTrace.created_at.desc()).limit(limit)).all()
    return [{"task_id": x.task_id, "seq": x.seq, "phase": x.phase, "tool": x.tool, "status": x.result_status, "reason_codes": x.reason_codes,
             "duration_ms": x.duration_ms, "decided_by": x.decided_by, "at": iso(x.created_at)} for x in rows]


@router.get("/dev/service-reports")
def service_reports(db: Session = DB) -> list[dict[str, Any]]:
    return [technician_service._report_view(r) | {"order_id": r.order_id, "technician_id": r.technician_id} for r in db.scalars(select(ServiceReport)).all()]  # type: ignore[operator]
