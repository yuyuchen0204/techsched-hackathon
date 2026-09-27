from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import CatalogItem, Location, WorkOrder
from app.models.enums import LifecycleStatus, Priority, SchedulingStatus
from app.scheduling.priority import base_priority
from app.services import catalog_service
from app.services.clock import Clock
from app.services.ids import next_order_id

# V3 error vocabulary (§15). Legacy codes remain valid; `kind` maps them to the canonical set.
ERROR_KINDS = {
    "not_found": "NOT_FOUND", "forbidden": "FORBIDDEN", "already_departed": "INVALID_STATE", "order_closed": "INVALID_STATE",
    "order_completed": "INVALID_STATE", "invalid_transition": "INVALID_STATE", "plan_not_pending": "INVALID_STATE",
    "target_closed": "INVALID_STATE", "target_departed": "INVALID_STATE", "no_assignment": "INVALID_STATE",
    "version_conflict": "VERSION_CONFLICT", "schedule_changed": "VERSION_CONFLICT", "facts_changed": "VERSION_CONFLICT",
    "plan_expired": "VERSION_CONFLICT", "scenario_reset": "VERSION_CONFLICT", "revalidation_failed": "POLICY_VIOLATION",
    "over_limit": "POLICY_VIOLATION", "forbidden_plan": "POLICY_VIOLATION", "missing_contact": "DATA_INCOMPLETE",
    "invalid_window": "DATA_INCOMPLETE", "invalid_catalog_item": "DATA_INCOMPLETE", "invalid_location": "DATA_INCOMPLETE",
    "outside_service_area": "DATA_INCOMPLETE", "tool_unavailable": "TOOL_UNAVAILABLE", "search_budget_exhausted": "SEARCH_BUDGET_EXHAUSTED",
}


class OrderError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, status: int = 422, kind: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.status = status
        self.kind = kind or ERROR_KINDS.get(code, "INVALID_STATE" if status == 409 else "DATA_INCOMPLETE" if status == 422 else "NOT_FOUND" if status == 404 else "FORBIDDEN" if status == 403 else "TOOL_UNAVAILABLE")


def create_order(
    db: Session, clock: Clock, *, customer_ref: str, customer_name: str, contact_phone: str, description: str,
    location_id: str, catalog_item_id: str, window_start: datetime, window_end: datetime, paid_expedite: bool,
    order_id: str | None = None, lifecycle_status: LifecycleStatus = LifecycleStatus.OPEN,
    customer_id: str | None = None, address: dict[str, Any] | None = None, excluded_technician_ids: list[str] | None = None,
    expedite_now: bool = False,
) -> WorkOrder:
    item: CatalogItem | None = catalog_service.get_item(db, catalog_item_id)
    if item is None:
        raise OrderError("invalid_catalog_item", f"catalog item {catalog_item_id} does not exist or is inactive")
    loc = db.get(Location, location_id)
    if loc is None:
        raise OrderError("invalid_location", f"location {location_id} is not a known preset location")
    if window_end <= window_start:
        raise OrderError("invalid_window", "window_end must be after window_start")
    if window_end < clock.now:
        raise OrderError("invalid_window", "window_end is already in the past")
    if not customer_name.strip() or not contact_phone.strip():
        raise OrderError("missing_contact", "customer name and phone are required")
    policy = get_policy()
    base = base_priority(paid_expedite, policy, expedite_now=expedite_now)
    order = WorkOrder(
        id=order_id or next_order_id(db), scenario_generation=clock.scenario_generation, customer_ref=customer_ref,
        customer_name=customer_name.strip(), contact_phone=contact_phone.strip(), description=description or "",
        location_id=loc.id, location_name=loc.name, lat=loc.lat, lon=loc.lon, catalog_item_id=item.id,
        catalog_snapshot=catalog_service.snapshot_of(item), window_start=window_start, window_end=window_end,
        paid_expedite=paid_expedite, expedite_now=expedite_now, base_priority=base.value, risk_priority=Priority.P3.value,
        effective_priority=base.value, priority_reasons=[{"type": "BASE", "priority": base.value,
                                                          "detail": "paid: send someone now (simulated)" if expedite_now
                                                          else "paid expedite (simulated)" if paid_expedite else "normal order"}],
        lifecycle_status=lifecycle_status, scheduling_status=SchedulingStatus.UNASSIGNED, version=1,
        customer_id=customer_id, excluded_technician_ids=list(excluded_technician_ids or []),
        address=address or {"formatted_address": loc.name, "latitude": loc.lat, "longitude": loc.lon, "source": "preset",
                            "unit_number": None, "unit_pending": True},
    )
    db.add(order)
    db.flush()
    return order


def get_order(db: Session, order_id: str) -> WorkOrder:
    o = db.get(WorkOrder, order_id)
    if o is None:
        raise OrderError("not_found", f"order {order_id} not found", status=404)
    return o


def list_orders(db: Session, clock: Clock) -> list[WorkOrder]:
    """Today's orders (earlier-day history rows belong to customer history, not the dispatcher board)."""
    return list(db.scalars(select(WorkOrder).where(WorkOrder.scenario_generation == clock.scenario_generation,
                                                   WorkOrder.window_end >= clock.day_origin)
                           .order_by(WorkOrder.window_start, WorkOrder.id)).all())


def set_paid_expedite(db: Session, order: WorkOrder) -> WorkOrder:
    """Explicit simulated payment: base becomes P1 (never P0 by payment alone)."""
    if order.lifecycle_status in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
        raise OrderError("order_closed", f"order {order.id} is {order.lifecycle_status}", status=409)
    if order.paid_expedite:
        return order
    order.paid_expedite = True
    order.base_priority = get_policy().paid_base
    order.effective_priority = Priority.most_urgent(Priority(order.base_priority), Priority(order.risk_priority)).value
    reasons = [r for r in (order.priority_reasons or []) if r.get("type") != "BASE"]
    reasons.insert(0, {"type": "BASE", "priority": order.base_priority, "detail": "paid expedite (simulated payment confirmed)"})
    order.priority_reasons = reasons
    order.version += 1
    db.flush()
    return order
