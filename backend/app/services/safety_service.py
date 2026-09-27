"""Safety incidents (V3 §9, trimmed in V3.1): separate from dispatch priority. Deterministic triggers with
negation/past-tense guards, optional model assessment, and an immediate critical human case.

The customer-facing "contact emergency services" panel (995 / 999 dial links, "I have contacted them") was removed on
request: a repair app should not present itself as a route to emergency services, and recording whether the customer
called is a claim it cannot verify. Detection and escalation stay — they are what makes the system take it seriously.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import SafetyIncident
from app.services import human_service
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.order_service import OrderError
from app.services.timeutil import iso

TRIGGERS: dict[str, list[str]] = {
    "gas_leak": ["smell gas", "gas leak", "gas smell", "leaking gas", "煤气味", "漏气", "燃气泄漏", "煤气泄漏"],
    "fire": ["on fire", "fire ", "smoke", "burning smell", "flames", "着火", "起火", "冒烟", "烧起来"],
    "electric_shock": ["sparks", "sparking", "electric shock", "electrocut", "shocked me", "触电", "火花", "电到"],
    "flooding": ["flooding", "flooded", "water everywhere", "burst pipe", "gushing", "淹水", "爆管", "水漫"],
    "injury": ["injured", "hurt", "bleeding", "unconscious", "受伤", "流血", "晕倒"],
}
NEGATION = ["no ", "not ", "n't ", "without", "没有", "不是", "并没", "无"]
PAST = ["last year", "last month", "last week", "previously", "before,", "used to", "去年", "上个月", "上周", "以前", "之前", "上次"]


def detect(text: str) -> dict[str, Any]:
    """Return {danger_type, matched, guarded} — guarded=True when a negation/past marker sits close to the match."""
    low = (text or "").lower()
    for dtype, words in TRIGGERS.items():
        for w in words:
            idx = low.find(w)
            if idx < 0:
                continue
            window = low[max(0, idx - 30): idx]
            guarded = any(n in window for n in NEGATION) or any(p in low for p in PAST)
            return {"danger_type": dtype, "matched": w.strip(), "guarded": guarded}
    return {"danger_type": None, "matched": None, "guarded": False}


def create_incident(db: Session, clock: Clock, *, danger_type: str, description: str, customer_id: str | None,
                    session_id: str | None, order_id: str | None, known_location: dict[str, Any] | None,
                    detection: dict[str, Any] | None, status: str = "open") -> SafetyIncident:
    if danger_type not in TRIGGERS and danger_type != "other":
        raise OrderError("invalid_danger_type", f"unknown danger type {danger_type}", kind="DATA_INCOMPLETE")
    # dedupe: an open incident for the same session/customer and type is updated, not duplicated
    for inc in db.scalars(select(SafetyIncident).where(SafetyIncident.status.in_(["draft", "open", "contact_opened", "contacted"]),
                                                       SafetyIncident.scenario_generation == clock.scenario_generation)).all():
        if inc.danger_type == danger_type and ((session_id and inc.session_id == session_id) or (customer_id and inc.customer_id == customer_id)):
            if description and description not in inc.description:
                inc.description = (inc.description + "\n" + description).strip()
            if known_location:
                inc.known_location = {**(inc.known_location or {}), **known_location}
            db.flush()
            return inc
    inc = SafetyIncident(id=new_id("inc"), scenario_generation=clock.scenario_generation, customer_id=customer_id, session_id=session_id,
                         order_id=order_id, danger_type=danger_type, description=description, known_location=known_location or {},
                         status=status, detection=detection or {}, contact_events=[])
    db.add(inc)
    db.flush()
    case, _ = human_service.flag_for_human(
        db, clock, source="AGENT_ESCALATION", category="safety_incident", urgency="critical",
        reason_summary=f"Possible {danger_type.replace('_', ' ')} reported: {description[:160]}", customer_id=customer_id,
        session_id=session_id, order_id=order_id, incident_id=inc.id, evidence_refs=[f"incident:{inc.id}"],
        suggested_next_action="Call the customer, make sure they are safe, and close the incident only after speaking to them.",
        idempotency_key=f"safety:{inc.id}")
    inc.human_case_id = case.id
    db.flush()
    return inc


def resolve_incident(db: Session, clock: Clock, incident_id: str, *, resolution: str, author: str) -> SafetyIncident:
    inc = db.get(SafetyIncident, incident_id)
    if inc is None:
        raise OrderError("not_found", "incident not found", status=404)
    inc.status = "resolved"
    inc.contact_events = list(inc.contact_events or []) + [{"kind": "resolved_by_human", "channel": None, "at": iso(clock.now), "note": resolution}]
    if inc.human_case_id:
        human_service.resolve(db, clock, inc.human_case_id, resolution=resolution, author=author)
    db.flush()
    return inc


def incident_view(inc: SafetyIncident) -> dict[str, Any]:
    return {"id": inc.id, "danger_type": inc.danger_type, "description": inc.description, "status": inc.status,
            "known_location": inc.known_location, "human_case_id": inc.human_case_id,
            "order_id": inc.order_id, "customer_id": inc.customer_id, "session_id": inc.session_id, "detection": inc.detection,
            "created_at": iso(inc.created_at), "updated_at": iso(inc.updated_at),
            "note": "Detected and escalated to a human coordinator. This product does not contact emergency services."}
