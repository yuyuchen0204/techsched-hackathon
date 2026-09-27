"""API contract: error shape, discriminated events, chat flow, approvals over HTTP."""
from datetime import date

from app.services.catalog_importer import make_catalog_item_id
from app.services.timeutil import iso, local_time_utc

DAY = date(2026, 9, 15)


def test_health_and_catalog(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["catalog_configured"] and h["catalog_items"] == 46 and "llm_api_key" not in h
    cat = client.get("/api/catalog").json()
    assert cat["status"]["configured"] and len(cat["items"]) == 46
    assert len(client.get("/api/catalog", params={"q": "toilet clog"}).json()["items"]) >= 1


def test_error_shapes(client):
    r = client.post("/api/orders", json={"customer_ref": "s", "customer_name": "A", "contact_phone": "1", "location_id": "nope",
                                         "catalog_item_id": "nope", "window_start": iso(local_time_utc(DAY, 10)),
                                         "window_end": iso(local_time_utc(DAY, 11))})
    assert r.status_code == 422
    body = r.json()["error"]
    assert set(body) == {"code", "kind", "message", "details", "request_id"} and body["kind"] == "DATA_INCOMPLETE"
    r2 = client.post("/api/orders/wo_001/cancel", json={"customer_ref": "seed:wo_001"})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "already_departed"
    r3 = client.post("/api/events", json={"type": "bogus"})
    assert r3.status_code == 422 and r3.json()["error"]["code"] == "validation_error"


def test_order_create_dispatch_and_cancel(client):
    body = {"customer_ref": "s1", "customer_name": "A", "contact_phone": "+65 1", "location_id": "loc_hougang",
            "catalog_item_id": make_catalog_item_id("Refrigerator", "Unusual noise"),
            "window_start": iso(local_time_utc(DAY, 11, 30)), "window_end": iso(local_time_utc(DAY, 13, 0))}
    r = client.post("/api/orders", json=body)
    assert r.status_code == 201
    order = r.json()["order"]
    assert order["catalog_snapshot"]["repair_duration_minutes"] == 35 and order["effective_priority"] == "P3"
    assert r.json()["dispatch"]["decision"] in ("auto", "manual")
    detail = client.get(f"/api/orders/{order['id']}").json()
    assert detail["plans"] and detail["can_cancel"]
    c = client.post(f"/api/orders/{order['id']}/cancel", json={"customer_ref": "s1", "reason": "x", "idempotency_key": "c1"})
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    assert client.post(f"/api/orders/{order["id"]}/cancel", json={"customer_ref": "s1", "idempotency_key": "c1"}).json() == {**c.json(), "idempotent": True}


def test_client_cannot_override_fixed_parameters(client):
    body = {"customer_ref": "s1", "customer_name": "A", "contact_phone": "+65 1", "location_id": "loc_hougang",
            "catalog_item_id": make_catalog_item_id("Refrigerator", "Unusual noise"), "paid_expedite": False,
            "window_start": iso(local_time_utc(DAY, 11, 30)), "window_end": iso(local_time_utc(DAY, 13, 0)),
            "effective_priority": "P0", "catalog_snapshot": {"repair_duration_minutes": 1}, "match_score": 100}
    r = client.post("/api/orders", json=body)
    assert r.status_code == 201
    o = r.json()["order"]
    assert o["effective_priority"] == "P3" and o["catalog_snapshot"]["repair_duration_minutes"] == 35


def test_events_union_and_technician_unavailability(client):
    r = client.post("/api/events", json={"type": "technician_unavailable", "technician_id": "tech_06", "reason": "sick", "idempotency_key": "e1"})
    assert r.status_code == 200 and "wo_007" in [x["order_id"] for x in r.json()["released"]]
    risks = client.get("/api/risks").json()
    assert any(x["type"] == "TECHNICIAN_CANCELLED" and x["target_order_id"] == "wo_007" for x in risks)
    r2 = client.post("/api/events", json={"type": "non_scheduling_complaint", "order_id": "wo_004", "complaint_type": "attitude", "text": "rude"})
    assert r2.json()["priority_changed"] is False
    standby = client.get("/api/orders/wo_004/standby").json()
    assert "candidates" in standby


def test_chat_flow_and_scoping(client):
    s = "sess_test_1"
    r = client.post("/api/chat/messages", json={"session_id": s, "message": "my aircon is leaking water, I'm at Bedok, 3pm please"})
    d = r.json()
    assert d["draft"]["catalog_item"]["problem_name"] == "Water leakage" and d["draft"]["location"]["id"] == "loc_bedok"
    client.post("/api/chat/messages", json={"session_id": s, "action": "set_contact", "payload": {"customer_name": "Zed", "contact_phone": "+65 2"}})
    # V3: the address must be confirmed with a unit number (or marked not applicable) before submission
    d_addr = client.post("/api/chat/messages", json={"session_id": s, "action": "set_address", "payload": {
        "latitude": 1.3249, "longitude": 103.9296, "formatted_address": "Bedok Mall, 311 New Upper Changi Road", "postal_code": "467360",
        "unit_number": "#03-12", "source": "search"}}).json()
    assert d_addr["draft"]["address"]["confirmed"] and d_addr["draft"]["address"]["unit_number"] == "03-12"
    # V3.1: "send someone now?" is asked after the address and before any slot; declining keeps the typed 3pm window
    assert [o["type"] for o in d_addr["reply"]["options"]] == ["expedite_now", "expedite_now"]
    d_urg = client.post("/api/chat/messages", json={"session_id": s, "action": "set_expedite_now", "payload": {"now": False}}).json()
    assert d_urg["state"] == "confirming" and d_urg["reply"]["card"]["expedite_now"] is False and d_urg["reply"]["card"]["paid_expedite"] is False
    d2 = client.post("/api/chat/messages", json={"session_id": s, "action": "confirm"}).json()
    assert d2["state"] == "submitted" and d2["orders"] and d2["orders"][0]["problem"].startswith("Air Conditioning")
    detail = client.get(f"/api/orders/{d2['orders'][0]['order_id']}").json()
    assert detail["address"]["unit_number"] == "03-12" and detail["address"]["formatted_address"].startswith("Bedok Mall")
    oid = d2["orders"][0]["order_id"]
    # another session cannot cancel it
    other = client.post("/api/chat/messages", json={"session_id": "sess_other", "action": "cancel_order", "payload": {"order_id": oid}}).json()
    assert "cannot be cancelled" in other["reply"]["text"]
    mine = client.post("/api/chat/messages", json={"session_id": s, "action": "cancel_order", "payload": {"order_id": oid}}).json()
    assert "has been cancelled" in mine["reply"]["text"]


def test_clock_and_reset(client):
    r = client.post("/api/demo/clock/advance", json={"minutes": 15}).json()
    assert r["clock"]["now"].endswith("08:45+08:00")
    g = client.get("/api/demo/clock").json()["scenario_generation"]
    rs = client.post("/api/demo/reset", json={"scenario": "main"}).json()
    assert rs["clock"]["scenario_generation"] == g + 1 and rs["clock"]["now"].endswith("08:30+08:00")
    sched = client.get("/api/schedules/current").json()
    assert sched["kpis"]["assigned"] == 20 and sched["version"]["active"]
