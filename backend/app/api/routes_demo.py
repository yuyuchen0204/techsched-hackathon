from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.api.deps import DB, locked
from app.schemas.api import (
    ClockAdvanceRequest,
    ClockControlRequest,
    CustomerCancelEvent,
    EventRequest,
    LatenessComplaintEvent,
    NonSchedulingComplaintEvent,
    PaidExpediteEvent,
    ResetRequest,
    TechnicianUnavailableEvent,
)
from app.services import demo_service, event_service, execution_service
from app.services.clock import get_clock, set_running
from app.services.order_service import OrderError
from app.services.timeutil import iso, to_utc_naive

router = APIRouter(prefix="/api")


def clock_view(db: Session) -> dict[str, Any]:
    c = get_clock(db)
    return {"now": iso(c.now), "timezone": c.timezone, "running": c.running, "scenario_generation": c.scenario_generation,
            "scenario_name": c.scenario_name, "seed": c.seed, "day_origin": iso(c.day_origin), "now_minutes": c.now_minutes}


@router.get("/demo/clock")
def get_demo_clock(db: Session = DB) -> dict[str, Any]:
    return clock_view(db)


@router.post("/demo/clock/advance")
@locked
def advance(body: ClockAdvanceRequest, db: Session) -> dict[str, Any]:
    result = execution_service.advance_clock(db, body.minutes)
    return {**result, "clock": clock_view(db)}


@router.post("/demo/clock/control")
@locked
def control(body: ClockControlRequest, db: Session) -> dict[str, Any]:
    set_running(db, body.running)
    return clock_view(db)


@router.post("/demo/reset")
@locked
def reset(body: ResetRequest, db: Session) -> dict[str, Any]:
    from app.services.snapshot import _matrix_cache
    _matrix_cache.clear()
    summary = demo_service.seed_scenario(db, body.scenario, seed=body.seed)
    return {"summary": summary, "clock": clock_view(db)}


@router.post("/demo/scan")
@locked
def manual_scan(db: Session) -> dict[str, Any]:
    return execution_service.scan(db, trigger="manual_scan")


@router.post("/events")
@locked
def post_event(body: EventRequest, db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    if isinstance(body, PaidExpediteEvent):
        return event_service.paid_expedite(db, body.order_id, customer_ref=body.customer_ref, idempotency_key=body.idempotency_key)
    if isinstance(body, TechnicianUnavailableEvent):
        start = to_utc_naive(body.start) if body.start else clock.now
        end = to_utc_naive(body.end) if body.end else clock.from_minutes(clock.now_minutes + 12 * 60)
        return event_service.technician_unavailable(db, body.technician_id, start=start, end=end, reason=body.reason,
                                                    idempotency_key=body.idempotency_key)
    if isinstance(body, LatenessComplaintEvent):
        return event_service.complaint(db, body.order_id, complaint_type="lateness", text=body.text, customer_ref=body.customer_ref,
                                       idempotency_key=body.idempotency_key)
    if isinstance(body, NonSchedulingComplaintEvent):
        return event_service.complaint(db, body.order_id, complaint_type=body.complaint_type, text=body.text,
                                       customer_ref=body.customer_ref, idempotency_key=body.idempotency_key)
    if isinstance(body, CustomerCancelEvent):
        return event_service.customer_cancel(db, body.order_id, customer_ref=body.customer_ref, actor="dispatcher",
                                             reason=body.reason, idempotency_key=body.idempotency_key)
    raise OrderError("unknown_event", "unsupported event type")


@router.get("/demo/scenarios")
def scenarios(db: Session = DB) -> dict[str, Any]:
    """Scenario catalogue for the demo-controls drawer (three load profiles + current selection)."""
    return {"scenarios": demo_service.list_scenarios(), "current": demo_service.current_summary(db)}


@router.get("/demo/load-summary")
def load_summary(db: Session = DB) -> dict[str, Any]:
    return demo_service.load_summary(db, get_clock(db))
