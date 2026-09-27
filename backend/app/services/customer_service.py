"""Customers, structured addresses, history, feedback and preferences (V3 §4.2, §8)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    Customer,
    CustomerAddress,
    CustomerFeedback,
    HumanCase,
    ServiceReport,
    Technician,
    WorkOrder,
)
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.location_service import resolve_point
from app.services.order_service import OrderError
from app.services.timeutil import iso

HISTORY_WINDOW_DAYS = 90  # configurable default for repeat-fault lookups (§8.4)


# ------------------------------------------------------------------ identity
def get_or_create_by_login(db: Session, clock: Clock, demo_login: str, name: str | None = None, phone: str | None = None) -> Customer:
    row = db.scalars(select(Customer).where(Customer.demo_login == demo_login,
                                            Customer.scenario_generation == clock.scenario_generation)).first()
    if row is None:
        row = Customer(id=new_id("cus"), scenario_generation=clock.scenario_generation, name=(name or demo_login).strip(),
                       phone=(phone or "").strip(), demo_login=demo_login)
        db.add(row)
        db.flush()
    else:
        if name and name.strip() and row.name != name.strip():
            row.name = name.strip()
        if phone and phone.strip():
            row.phone = phone.strip()
    return row


def get_customer(db: Session, customer_id: str) -> Customer:
    c = db.get(Customer, customer_id)
    if c is None:
        raise OrderError("not_found", f"customer {customer_id} not found", status=404)
    return c


# ------------------------------------------------------------------ addresses
def _addr_dict(a: CustomerAddress) -> dict[str, Any]:
    return {"id": a.id, "location_id": a.location_id, "formatted_address": a.formatted_address, "postal_code": a.postal_code,
            "building_name": a.building_name, "street_address": a.street_address, "unit_number": a.unit_number,
            "unit_not_applicable": a.unit_not_applicable, "latitude": a.latitude, "longitude": a.longitude, "source": a.source,
            "confirmed_at": iso(a.confirmed_at), "last_used_at": iso(a.last_used_at), "is_default": a.is_default}


def upsert_address(db: Session, clock: Clock, customer: Customer, *, lat: float, lon: float, formatted_address: str,
                   postal_code: str | None, building_name: str | None, street_address: str | None, unit_number: str | None,
                   unit_not_applicable: bool, source: str, save: bool = True) -> dict[str, Any]:
    """Confirm an address for this customer. Coordinates come from a geocoder/preset/map pin, never from the model."""
    if not formatted_address.strip():
        raise OrderError("invalid_location", "formatted_address is required", kind="DATA_INCOMPLETE")
    unit_number = (unit_number or "").strip().lstrip("#").strip() or None
    if not unit_number and not unit_not_applicable:
        raise OrderError("unit_required", "unit / floor number is required, or mark it as not applicable",
                         {"field": "unit_number"}, kind="DATA_INCOMPLETE")
    point = resolve_point(db, lat, lon, formatted_address)  # snaps to a preset in fixture mode (told to the caller)
    existing = None
    for a in db.scalars(select(CustomerAddress).where(CustomerAddress.customer_id == customer.id)).all():
        if abs(a.latitude - lat) < 1e-4 and abs(a.longitude - lon) < 1e-4 and (a.unit_number or "") == (unit_number or ""):
            existing = a
            break
    if existing is None:
        existing = CustomerAddress(id=new_id("adr"), customer_id=customer.id, location_id=point["location"]["id"],
                                   formatted_address=formatted_address.strip(), postal_code=postal_code or None,
                                   building_name=building_name or None, street_address=street_address or None,
                                   unit_number=(unit_number or "").strip() or None, unit_not_applicable=unit_not_applicable,
                                   latitude=lat, longitude=lon, source=source, confirmed_at=clock.now, last_used_at=clock.now,
                                   is_default=save)
        db.add(existing)
    else:
        existing.confirmed_at = clock.now
        existing.last_used_at = clock.now
        existing.unit_number = (unit_number or "").strip() or None
        existing.unit_not_applicable = unit_not_applicable
        existing.source = source
    if save:
        for other in db.scalars(select(CustomerAddress).where(CustomerAddress.customer_id == customer.id)).all():
            other.is_default = other.id == existing.id
    db.flush()
    out = _addr_dict(existing)
    out["snapped"] = point["snapped"]
    out["note"] = point["note"]
    return out


def list_addresses(db: Session, customer_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(select(CustomerAddress).where(CustomerAddress.customer_id == customer_id)
                      .order_by(CustomerAddress.is_default.desc(), CustomerAddress.last_used_at.desc())).all()
    return [_addr_dict(a) for a in rows]


# ------------------------------------------------------------------ history (authorized, scoped)
def customer_history(db: Session, clock: Clock, customer_id: str, *, role: str = "customer", days: int | None = None) -> dict[str, Any]:
    """Structured history for one customer. role=customer → own data; technician → task-relevant summary only."""
    window_days = days or HISTORY_WINDOW_DAYS
    since = clock.now - timedelta(days=window_days)
    customer = get_customer(db, customer_id)
    orders = db.scalars(select(WorkOrder).where(WorkOrder.customer_id == customer_id).order_by(WorkOrder.window_start.desc())).all()
    reports = {r.order_id: r for r in db.scalars(select(ServiceReport).where(ServiceReport.order_id.in_([o.id for o in orders] or ["-"]))).all()}
    feedback = db.scalars(select(CustomerFeedback).where(CustomerFeedback.customer_id == customer_id)).all()
    techs = {t.id: t.name for t in db.scalars(select(Technician)).all()}
    cases = db.scalars(select(HumanCase).where(HumanCase.customer_id == customer_id, HumanCase.status == "resolved")).all()
    hist_orders = []
    for o in orders:
        r = reports.get(o.id)
        hist_orders.append({
            "order_id": o.id, "date": iso(o.window_start), "status": o.lifecycle_status,
            "reported_problem": f"{o.catalog_snapshot.get('trade_type')} – {o.catalog_snapshot.get('problem_name')}",
            "description": o.description, "address": (o.address or {}).get("formatted_address") or o.location_name,
            "technician": techs.get(o.technician_id or "", o.technician_id),
            "actual_problem": r.actual_problem_text if r else None, "resolution": r.resolution if r else None,
            "technician_notes": (r.notes if r and role != "customer" else None),
            "in_window": o.window_start >= since,
        })
    neg = [f for f in feedback if f.rating <= 2 and f.target == "technician" and f.technician_id]
    return {
        "customer": {"id": customer.id, "name": customer.name, "phone": customer.phone},
        "addresses": list_addresses(db, customer_id) if role != "technician" else [],
        "orders": hist_orders if role != "technician" else [o for o in hist_orders if o["in_window"]][:5],
        "feedback": [{"order_id": f.order_id, "technician": techs.get(f.technician_id or "", f.technician_id), "rating": f.rating,
                      "target": f.target, "reasons": f.reasons, "comment": f.comment if role != "technician" else None,
                      "at": iso(f.created_at)} for f in feedback],
        "negative_technicians": sorted({f.technician_id for f in neg if f.technician_id}),  # soft preference input (§8.3)
        "human_conclusions": [{"case_id": c.id, "category": c.category, "resolution": c.resolution, "at": iso(c.resolved_at)} for c in cases] if role != "technician" else [],
        "history_window_days": window_days,
    }


def repeat_fault_hint(db: Session, clock: Clock, customer_id: str | None, location_id: str | None, trade_type: str,
                      exclude_order_id: str | None = None, days: int | None = None) -> dict[str, Any] | None:
    """Recent records of the same customer/address and the same trade. Worded as 'similar repair record', never as
    'same device failed again' — we have no device identity (§8.4)."""
    if not customer_id and not location_id:
        return None
    since = clock.now - timedelta(days=days or HISTORY_WINDOW_DAYS)
    q = select(WorkOrder).where(WorkOrder.window_start >= since)
    # only closed records count as history; a location match needs a precise point (pt_*), never a shared preset area
    precise = bool(location_id and location_id.startswith("pt_"))
    rows = [o for o in db.scalars(q).all() if o.id != exclude_order_id and o.lifecycle_status == "COMPLETED"
            and ((customer_id and o.customer_id == customer_id) or (precise and o.location_id == location_id))
            and (o.catalog_snapshot or {}).get("trade_type") == trade_type]
    if not rows:
        return None
    reports = {r.order_id: r for r in db.scalars(select(ServiceReport).where(ServiceReport.order_id.in_([o.id for o in rows]))).all()}
    return {
        "count": len(rows), "trade_type": trade_type, "window_days": days or HISTORY_WINDOW_DAYS,
        "records": [{"order_id": o.id, "date": iso(o.window_start), "problem": (o.catalog_snapshot or {}).get("problem_name"),
                     "actual_problem": reports[o.id].actual_problem_text if o.id in reports else None,
                     "resolution": reports[o.id].resolution if o.id in reports else None} for o in rows[:5]],
        "wording": f"{len(rows)} similar {trade_type} repair record(s) in the last {days or HISTORY_WINDOW_DAYS} days at this customer/address "
                   "(no device identity — not a confirmed repeat failure of the same unit)",
    }


# ------------------------------------------------------------------ feedback
def add_feedback(db: Session, clock: Clock, order: WorkOrder, *, rating: int, target: str, reasons: list[str], comment: str) -> CustomerFeedback:
    if order.lifecycle_status != "COMPLETED":
        raise OrderError("invalid_state", "feedback is only possible after the service is completed", status=409, kind="INVALID_STATE")
    if not 1 <= rating <= 5:
        raise OrderError("invalid_rating", "rating must be 1..5", kind="DATA_INCOMPLETE")
    existing = db.scalars(select(CustomerFeedback).where(CustomerFeedback.order_id == order.id)).first()
    if existing:
        existing.rating, existing.target, existing.reasons, existing.comment = rating, target, reasons, comment
        db.flush()
        return existing
    fb = CustomerFeedback(id=new_id("fb"), scenario_generation=clock.scenario_generation, order_id=order.id,
                          customer_id=order.customer_id or order.customer_ref, technician_id=order.technician_id,
                          rating=rating, target=target, reasons=reasons, comment=comment, sim_time=clock.now)
    db.add(fb)
    db.flush()
    return fb


def preference_penalties(db: Session, customer_id: str | None) -> dict[str, float]:
    """Technician → penalty (0..1) from this customer's negative technician-targeted ratings. Soft: used as a
    ranking term, never as a hard filter (explicit order-level exclusions are hard and live on the order)."""
    if not customer_id:
        return {}
    out: dict[str, float] = {}
    for f in db.scalars(select(CustomerFeedback).where(CustomerFeedback.customer_id == customer_id,
                                                        CustomerFeedback.target == "technician")).all():
        if f.technician_id and f.rating <= 2:
            out[f.technician_id] = max(out.get(f.technician_id, 0.0), 0.6 if f.rating == 1 else 0.4)
    return out
