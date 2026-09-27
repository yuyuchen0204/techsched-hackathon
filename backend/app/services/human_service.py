"""Human takeover cases (V3 §10): three sources, dedupe/escalation, replies to the customer, agent wake-up."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import AgentTask, HumanCase, WorkOrder
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.notification_service import notify
from app.services.order_service import OrderError
from app.services.timeutil import iso

SOURCES = ("CUSTOMER_REQUEST", "POLICY_REQUIRED", "AGENT_ESCALATION")
URGENCY_RANK = {"low": 0, "normal": 1, "high": 2, "critical": 3}


def flag_for_human(db: Session, clock: Clock, *, source: str, category: str, urgency: str = "normal", reason_summary: str,
                   customer_id: str | None = None, session_id: str | None = None, order_id: str | None = None,
                   incident_id: str | None = None, evidence_refs: list[Any] | None = None, attempted_actions: list[Any] | None = None,
                   unresolved_questions: list[Any] | None = None, suggested_next_action: str = "",
                   idempotency_key: str | None = None) -> tuple[HumanCase, bool]:
    """Create or merge a human case. Returns (case, created). Same open item → evidence appended, urgency escalated."""
    if source not in SOURCES:
        raise OrderError("invalid_source", f"source must be one of {SOURCES}", kind="DATA_INCOMPLETE")
    if idempotency_key:
        prior = db.scalars(select(HumanCase).where(HumanCase.idempotency_key == idempotency_key)).first()
        if prior is not None:
            return prior, False
    open_q = select(HumanCase).where(HumanCase.status.in_(["pending", "in_progress", "waiting_customer"]),
                                     HumanCase.category == category, HumanCase.scenario_generation == clock.scenario_generation)
    for c in db.scalars(open_q).all():
        same = (order_id and c.order_id == order_id) or (incident_id and c.incident_id == incident_id) or \
               (not order_id and not incident_id and customer_id and c.customer_id == customer_id)
        if same:
            c.evidence_refs = list(c.evidence_refs or []) + [e for e in (evidence_refs or []) if e not in (c.evidence_refs or [])]
            c.attempted_actions = list(c.attempted_actions or []) + list(attempted_actions or [])
            c.unresolved_questions = list(dict.fromkeys(list(c.unresolved_questions or []) + list(unresolved_questions or [])))
            if URGENCY_RANK.get(urgency, 1) > URGENCY_RANK.get(c.urgency, 1):
                c.urgency = urgency
                c.escalations += 1
            c.reason_summary = (c.reason_summary + "\n" + reason_summary).strip() if reason_summary not in c.reason_summary else c.reason_summary
            db.flush()
            return c, False
    case = HumanCase(id=new_id("hc"), scenario_generation=clock.scenario_generation, source=source, category=category, urgency=urgency,
                     status="pending", customer_id=customer_id, session_id=session_id, order_id=order_id, incident_id=incident_id,
                     reason_summary=reason_summary, evidence_refs=evidence_refs or [], attempted_actions=attempted_actions or [],
                     unresolved_questions=unresolved_questions or [], suggested_next_action=suggested_next_action,
                     idempotency_key=idempotency_key)
    db.add(case)
    db.flush()
    if order_id:
        o = db.get(WorkOrder, order_id)
        if o is not None:
            # the order page shows one case: the customer's own request wins over a policy case, a live case over a resolved one
            current = db.get(HumanCase, o.human_case_id) if o.human_case_id else None
            if current is None or current.status == "resolved" or (source == "CUSTOMER_REQUEST" and current.source != "CUSTOMER_REQUEST"):
                o.human_case_id = case.id
    notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="human_case", order_id=order_id,
           message=f"[simulated] Human case {case.id} ({source}, {category}, {urgency}): {reason_summary[:140]}",
           dedupe_key=f"hc:{case.id}")
    return case, True


def get_case(db: Session, case_id: str) -> HumanCase:
    c = db.get(HumanCase, case_id)
    if c is None:
        raise OrderError("not_found", f"human case {case_id} not found", status=404)
    return c


def take(db: Session, clock: Clock, case_id: str, assignee: str) -> HumanCase:
    c = get_case(db, case_id)
    if c.status == "resolved":
        raise OrderError("invalid_state", "case already resolved", status=409, kind="INVALID_STATE")
    c.assignee = assignee
    c.status = "in_progress"
    db.flush()
    return c


def reply(db: Session, clock: Clock, case_id: str, *, text: str, author: str, to_customer: bool = True) -> HumanCase:
    c = get_case(db, case_id)
    if not text.strip():
        raise OrderError("empty_reply", "reply text is required", kind="DATA_INCOMPLETE")
    c.replies = list(c.replies or []) + [{"from": author, "text": text.strip(), "at": iso(clock.now)}]
    if to_customer:
        c.status = "waiting_customer" if c.status != "resolved" else c.status
        ref = c.session_id or c.customer_id
        if ref:
            notify(db, clock, recipient_ref=ref, recipient_type="customer", type="human_reply", order_id=c.order_id,
                   message=f"[simulated] Support ({author}): {text.strip()}")
    db.flush()
    return c


def customer_message(db: Session, clock: Clock, case: HumanCase, text: str) -> HumanCase:
    case.replies = list(case.replies or []) + [{"from": "customer", "text": text, "at": iso(clock.now)}]
    if case.status == "waiting_customer":
        case.status = "in_progress" if case.assignee else "pending"
    db.flush()
    return case


def resolve(db: Session, clock: Clock, case_id: str, *, resolution: str, author: str) -> HumanCase:
    c = get_case(db, case_id)
    # a reschedule case exists because a request is waiting for an answer; closing the case without answering it left
    # the request `pending` for ever and the customer with silence. Closing it here means "we kept the original time".
    if c.category == "reschedule_request" and c.status != "resolved":
        from app.models.entities import RescheduleRequest
        for req in db.scalars(select(RescheduleRequest).where(RescheduleRequest.human_case_id == case_id,
                                                              RescheduleRequest.status == "pending")).all():
            req.status = "rejected"
            order = db.get(WorkOrder, req.order_id)
            if order is not None:
                notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="reschedule_declined",
                       order_id=order.id, message=f"[simulated] We kept your original appointment for {order.id}: {resolution[:140]}")
    c.status = "resolved"
    c.resolution = resolution
    c.resolved_at = clock.now
    c.assignee = c.assignee or author
    ref = c.session_id or c.customer_id
    if ref:
        notify(db, clock, recipient_ref=ref, recipient_type="customer", type="human_resolved", order_id=c.order_id,
               message=f"[simulated] Support closed your request: {resolution[:160]}")
    # wake agent tasks that were waiting on this case (they re-read state before acting)
    for t in db.scalars(select(AgentTask).where(AgentTask.human_case_id == case_id, AgentTask.status == "waiting_human")).all():
        t.status = "pending"
        t.wakeup_reason = f"human_case_resolved:{case_id}"
    db.flush()
    return c


def open_case_for_session(db: Session, session_id: str | None, customer_id: str | None) -> HumanCase | None:
    if not session_id and not customer_id:
        return None
    q = select(HumanCase).where(HumanCase.status.in_(["pending", "in_progress", "waiting_customer"]),
                                HumanCase.source == "CUSTOMER_REQUEST")
    for c in db.scalars(q.order_by(HumanCase.created_at.desc())).all():
        if (session_id and c.session_id == session_id) or (customer_id and c.customer_id == customer_id):
            return c
    return None


def case_view(c: HumanCase) -> dict[str, Any]:
    return {"id": c.id, "source": c.source, "category": c.category, "urgency": c.urgency, "status": c.status,
            "customer_id": c.customer_id, "session_id": c.session_id, "order_id": c.order_id, "incident_id": c.incident_id,
            "reason_summary": c.reason_summary, "evidence_refs": c.evidence_refs, "attempted_actions": c.attempted_actions,
            "unresolved_questions": c.unresolved_questions, "suggested_next_action": c.suggested_next_action,
            "assignee": c.assignee, "resolution": c.resolution, "replies": c.replies, "escalations": c.escalations,
            "created_at": iso(c.created_at), "updated_at": iso(c.updated_at), "resolved_at": iso(c.resolved_at)}
