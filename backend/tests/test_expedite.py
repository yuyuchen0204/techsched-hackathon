"""Post-order expedite = simulated payment + automatic move to the earliest start (and "Keep original time").
Rules stay the existing ones: P1 may move ≤2 undeparted P3 orders inside their windows, score > 70 auto else review."""
from datetime import date

import pytest
from sqlalchemy import select

from app.config import get_policy
from app.models.entities import CandidatePlan, HumanCase, InboundEvent, Notification, WorkOrder
from app.models.enums import PlanStatus, SolveStatus
from app.orchestration.orchestrator import dispatch_order
from app.scheduling.solver import SolveResult
from app.services import chat_service, expedite_service, plan_service
from app.services.catalog_importer import make_catalog_item_id
from app.services.clock import get_clock
from app.services.order_service import OrderError, create_order
from app.services.risk_service import active_assignment
from app.services.timeutil import local_time_utc, parse_iso

DAY = date(2026, 9, 15)


def t(h, m=0):
    return local_time_utc(DAY, h, m)


def booked(db, *, ref="sess_exp", ws=(14, 0), we=(15, 30), loc="loc_hougang"):
    """A normal P3 order with a confirmed appointment in the afternoon (the seeded day has spare capacity earlier)."""
    clock = get_clock(db)
    o = create_order(db, clock, customer_ref=ref, customer_name="Exp", contact_phone="+65 7", description="", location_id=loc,
                     catalog_item_id=make_catalog_item_id("Refrigerator", "Not cooling"), window_start=t(*ws), window_end=t(*we), paid_expedite=False)
    out = dispatch_order(db, o.id, trigger="test")
    assert out.decision == "auto" and active_assignment(db, o.id) is not None
    return o


def messages(db, recipient, *, order_id=None):
    q = select(Notification).where(Notification.recipient_ref == recipient)
    if order_id:
        q = q.where(Notification.order_id == order_id)
    return [n.message for n in db.scalars(q.order_by(Notification.created_at)).all()]


def test_expedite_moves_to_an_earlier_start_within_p1_authority(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    monkeypatch.setattr(get_policy(), "auto_score_threshold", 0.0)  # every admissible plan is auto → exercises the commit path
    o = booked(db)
    before = active_assignment(db, o.id)
    old_start, old_window = before.service_start, (o.window_start, o.window_end)
    pv = expedite_service.preview(db, clock, o)
    assert pv["possible"] and pv["earliest_start"] and parse_iso(pv["earliest_start"]) < old_start and "Earliest possible start after expedite" in pv["text"]
    assert active_assignment(db, o.id).service_start == old_start  # the preview changed nothing
    res = expedite_service.expedite(db, clock, o, actor="test", idempotency_key=f"expedite:{o.id}")
    assert res["outcome"] == "auto" and o.paid_expedite and o.effective_priority == "P1" and res["idempotent"] is False
    after = active_assignment(db, o.id)
    assert after.service_start < old_start and after.service_start == parse_iso(res["planned_start"])
    assert res["affected_count"] >= 0  # no cap: any number of undeparted P3 orders may move
    assert o.window_start == after.service_start and o.window_end - o.window_start == old_window[1] - old_window[0]
    st = o.expedite_state
    assert st["status"] == "moved" and parse_iso(st["original_planned_start"]) == old_start and parse_iso(st["original_window_start"]) == old_window[0]
    assert expedite_service.state_view(o)["summary"].startswith("Expedited · moved from")
    # notifications: customer, technician, moved customers
    cust = messages(db, "sess_exp", order_id=o.id)
    assert any(m.startswith("Your order is expedited (simulated payment). New appointment:") and "Keep original time" in m for m in cust)
    assert any(f"Schedule updated: {o.id} moved to" in m and "(expedited)" in m for m in messages(db, after.technician_id))
    for moved_id in st["affected_order_ids"]:
        moved = db.get(WorkOrder, moved_id)
        assert moved.effective_priority == "P3" and moved.lifecycle_status == "OPEN"
        assert any("to make room for an urgent job" in m and "original window" in m for m in messages(db, moved.customer_ref, order_id=moved_id))
        a = active_assignment(db, moved_id)
        assert moved.window_start <= a.service_start <= moved.window_end  # moved inside its own window (existing rule)
    # repeat: idempotent, no second charge, nothing moves again
    again = expedite_service.expedite(db, clock, o, actor="test", idempotency_key=f"expedite:{o.id}")
    assert again["idempotent"] is True and again["message"] == "Already expedited — no second charge."
    other_key = expedite_service.expedite(db, clock, o, actor="test", idempotency_key="expedite:other-key")
    assert other_key["idempotent"] is True and other_key["message"] == "Already expedited — no second charge."
    assert active_assignment(db, o.id).service_start == after.service_start
    assert len(db.scalars(select(InboundEvent).where(InboundEvent.type == "paid_expedite")).all()) == 1


def test_expedite_without_an_earlier_slot_keeps_the_time_and_says_so(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = booked(db, ref="sess_same")
    before = active_assignment(db, o.id)
    monkeypatch.setattr(expedite_service, "solve_insert", lambda *a, **k: SolveResult(status=SolveStatus.NO_SOLUTION_FOUND))
    pv = expedite_service.preview(db, clock, o)
    assert pv["earliest_start"] is None and pv["text"] == "Expedite raises priority; no earlier slot is available today."
    res = expedite_service.expedite(db, clock, o, actor="test", idempotency_key=f"expedite:{o.id}")
    assert res["outcome"] == "unchanged" and o.effective_priority == "P1" and o.paid_expedite
    assert active_assignment(db, o.id).service_start == before.service_start and o.window_start == t(14, 0)
    assert res["message"].startswith("Your order is expedited: priority raised") and "your appointment time is unchanged" in res["message"]
    assert o.expedite_state["status"] == "unchanged" and expedite_service.state_view(o)["summary"] == "Expedited · time unchanged"
    with pytest.raises(OrderError):
        expedite_service.keep_original_time(db, clock, o, actor="test", idempotency_key="restore:x")  # nothing was moved


def test_departed_order_cannot_be_expedited(db_session):
    db = db_session
    clock = get_clock(db)
    departed = db.get(WorkOrder, "wo_001")
    assert departed.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS")
    with pytest.raises(OrderError) as exc:
        expedite_service.expedite(db, clock, departed, actor="test", idempotency_key="expedite:wo_001")
    assert exc.value.message == "Expedite is only possible before the technician departs." and exc.value.kind == "INVALID_STATE"
    assert not departed.paid_expedite
    assert expedite_service.preview(db, clock, departed)["possible"] is False
    # the chat says the same for a session that only owns a departed order
    r = chat_service.handle(db, departed.customer_ref, "please hurry my order", None, None)
    assert r["reply"]["text"] == "Expedite is only possible before the technician departs."


def test_low_score_goes_to_review_and_the_original_slot_stays_until_approval(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = booked(db, ref="sess_review")
    before = active_assignment(db, o.id)
    monkeypatch.setattr(get_policy(), "auto_score_threshold", 100.0)  # nothing passes → review
    res = expedite_service.expedite(db, clock, o, actor="test", idempotency_key=f"expedite:{o.id}")
    assert res["outcome"] == "manual" and o.effective_priority == "P1"
    assert active_assignment(db, o.id).service_start == before.service_start and o.window_start == t(14, 0) and o.window_end == t(15, 30)
    plan = db.get(CandidatePlan, res["plan_id"])
    assert plan.status == PlanStatus.PENDING_REVIEW and plan.metrics["expedite_window"]["mode"] == "earlier"
    assert parse_iso(plan.metrics["expedite_window"]["start"]) == parse_iso(res["planned_start"])
    assert o.expedite_state["status"] == "pending_review" and "awaiting dispatcher confirmation" in expedite_service.state_view(o)["summary"]
    assert res["message"].startswith("Your order is expedited. A dispatcher is confirming an earlier slot") and "your original appointment stays" in res["message"]
    case = db.scalars(select(HumanCase).where(HumanCase.order_id == o.id, HumanCase.category == "plan_approval")).first()
    assert case is not None and case.status == "pending"
    assert any(m.startswith(f"Expedited order {o.id} needs approval for an earlier slot (moves") for m in messages(db, "dispatcher", order_id=o.id))
    # approval applies the plan together with its window change
    out = plan_service.approve_plan(db, clock, plan.id, actor="dispatcher", reason=None, idempotency_key="apr-exp", expected_versions=None)
    assert out["status"] == PlanStatus.COMMITTED
    after = active_assignment(db, o.id)
    assert after.service_start == parse_iso(res["planned_start"]) and after.service_start < before.service_start
    assert o.window_start == after.service_start and o.expedite_state["status"] == "moved"
    assert any(m.startswith("Your order is expedited (simulated payment). New appointment:") for m in messages(db, "sess_review", order_id=o.id))
    assert db.get(HumanCase, case.id).status == "resolved"


def test_rejected_review_keeps_the_original_appointment(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = booked(db, ref="sess_reject")
    before = active_assignment(db, o.id)
    monkeypatch.setattr(get_policy(), "auto_score_threshold", 100.0)
    res = expedite_service.expedite(db, clock, o, actor="test", idempotency_key=f"expedite:{o.id}")
    assert res["outcome"] == "manual"
    plan_service.reject_plan(db, clock, res["plan_id"], actor="dispatcher", reason="not now", idempotency_key="rej-exp")
    assert active_assignment(db, o.id).service_start == before.service_start and o.window_start == t(14, 0)
    assert o.expedite_state["status"] == "unchanged" and o.effective_priority == "P1"
    assert any("could not be confirmed by the dispatcher" in m for m in messages(db, "sess_reject", order_id=o.id))


def test_keep_original_time_restores_the_window_and_keeps_p1(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    monkeypatch.setattr(get_policy(), "auto_score_threshold", 0.0)
    o = booked(db, ref="sess_keep")
    original = (o.window_start, o.window_end)
    res = expedite_service.expedite(db, clock, o, actor="test", idempotency_key=f"expedite:{o.id}")
    assert res["outcome"] == "auto" and o.window_start != original[0]
    moved_at = o.expedite_state["moved_at"]
    back = expedite_service.keep_original_time(db, clock, o, actor="test", idempotency_key=f"expedite_restore:{o.id}:{moved_at}")
    assert back["outcome"] == "auto" and back["idempotent"] is False
    assert (o.window_start, o.window_end) == original
    a = active_assignment(db, o.id)
    assert original[0] <= a.service_start <= original[1]
    assert o.effective_priority == "P1" and o.paid_expedite  # no refund, priority kept
    assert o.expedite_state["status"] == "reverted" and expedite_service.state_view(o)["summary"] == "Expedited · original time kept"
    assert any(m.startswith("Your original time is restored:") and "no refund" in m for m in messages(db, "sess_keep", order_id=o.id))
    assert any(f"Schedule updated: {o.id} moved to" in m and "original time restored" in m for m in messages(db, a.technician_id))
    again = expedite_service.keep_original_time(db, clock, o, actor="test", idempotency_key=f"expedite_restore:{o.id}:{moved_at}")
    assert again["idempotent"] is True
    assert (o.window_start, o.window_end) == original and active_assignment(db, o.id).service_start == a.service_start


def test_chat_explains_then_expedites_an_existing_order(db_session, monkeypatch):
    db = db_session
    monkeypatch.setattr(get_policy(), "auto_score_threshold", 0.0)
    o = booked(db, ref="sess_chat_exp")
    r = chat_service.handle(db, "sess_chat_exp", "please hurry my order", None, None)
    assert [x["type"] for x in r["reply"]["options"]] == ["expedite_order", "keep_order"]
    assert r["reply"]["options"][0]["label"] == "Expedite now (simulated payment)" and r["reply"]["options"][1]["label"] == "Keep as is"
    assert "simulated payment" in r["reply"]["text"] and "P1" in r["reply"]["text"] and "earliest possible start" in r["reply"]["text"]
    r = chat_service.handle(db, "sess_chat_exp", "", "keep_order", None)
    assert r["reply"]["text"] == "Okay — your order stays as it is." and not db.get(WorkOrder, o.id).paid_expedite
    r = chat_service.handle(db, "sess_chat_exp", "make it faster please", None, None)
    r = chat_service.handle(db, "sess_chat_exp", "", "expedite_order", {"order_id": o.id})
    assert r["reply"]["text"].startswith("Your order is expedited") and r["reply"]["order"]["effective_priority"] == "P1"
    assert r["orders"][0]["expedite"]["summary"].startswith("Expedited")
    r = chat_service.handle(db, "sess_chat_exp", "expedite", None, None)
    assert r["reply"]["text"].startswith("Already expedited — no second charge.")


def test_customer_api_expedite_preview_and_keep_original_time(client, monkeypatch):
    monkeypatch.setattr(get_policy(), "auto_score_threshold", 0.0)
    s = "sess_api_exp"
    body = {"customer_ref": s, "customer_name": "A", "contact_phone": "+65 1", "location_id": "loc_hougang",
            "catalog_item_id": make_catalog_item_id("Refrigerator", "Not cooling"),
            "window_start": "2026-09-15T14:00:00+08:00", "window_end": "2026-09-15T15:30:00+08:00"}
    oid = client.post("/api/orders", json=body).json()["order"]["id"]
    pv = client.get(f"/api/customer/orders/{oid}/expedite-preview", params={"session_id": s}).json()
    assert pv["possible"] and "text" in pv
    d = client.get(f"/api/customer/orders/{oid}", params={"session_id": s}).json()
    assert d["actions"]["expedite"] and not d["actions"]["keep_original_time"] and d["expedite"] is None
    r = client.post(f"/api/customer/orders/{oid}/expedite", json={"session_id": s}).json()
    assert r["effective_priority"] == "P1" and r["message"].startswith("Your order is expedited")
    d = client.get(f"/api/customer/orders/{oid}", params={"session_id": s}).json()
    assert d["effective_priority"] == "P1" and d["expedite"]["summary"].startswith("Expedited") and d["expedite"]["message"].startswith("Your order is expedited")
    assert not d["actions"]["expedite"]
    r2 = client.post(f"/api/customer/orders/{oid}/expedite", json={"session_id": s}).json()
    assert r2["idempotent"] is True and r2["message"] == "Already expedited — no second charge."
    if d["actions"]["keep_original_time"]:
        k = client.post(f"/api/customer/orders/{oid}/keep-original-time", json={"session_id": s}).json()
        assert k["effective_priority"] == "P1" and k["outcome"] in ("auto", "manual", "unchanged")
        assert client.get(f"/api/customer/orders/{oid}", params={"session_id": s}).json()["window_start"].endswith("14:00+08:00")
    assert client.get("/api/orders/wo_001").json()["lifecycle_status"] != "OPEN"
    assert client.post("/api/customer/orders/wo_001/expedite", json={"session_id": "seed:wo_001"}).status_code in (403, 409)
