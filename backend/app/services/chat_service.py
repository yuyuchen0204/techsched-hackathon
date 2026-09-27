"""Customer chatbot backend (V3): identity, catalog-only suggestions, structured address, returning-customer confirmation,
feasible-window negotiation, human handoff, safety incidents, order detail actions. Coordinates, durations, priorities and
approvals are never produced by the model."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.understanding import understand
from app.models.entities import (
    Assignment,
    ChatSession,
    Customer,
    CustomerQuestion,
    Location,
    Technician,
    WorkOrder,
)
from app.models.enums import AssignmentStatus, LifecycleStatus
from app.orchestration.orchestrator import dispatch_order
from app.providers.llm.factory import get_llm
from app.services import (
    catalog_service,
    customer_service,
    event_service,
    expedite_service,
    human_service,
    negotiation_service,
    order_service,
    safety_service,
)
from app.services.agent_runs import start_run
from app.services.clock import Clock, get_clock
from app.services.order_service import OrderError
from app.services.timeutil import hhmm, iso, parse_iso


# ------------------------------------------------------------------ session helpers
def get_session(db: Session, session_id: str, clock: Clock) -> ChatSession:
    """Fetch the session, creating it on first sight and replacing one left over from an earlier scenario.

    Reads are deliberately lock-free (see docs/architecture.md), so two polls arriving together can both find the
    session missing and both try to insert it. The loser used to surface as a 500 in the customer app; it now simply
    reads back the row the winner committed.
    """
    s = db.get(ChatSession, session_id)
    if s is not None and s.scenario_generation == clock.scenario_generation:
        return s
    if s is not None:
        db.delete(s)
        db.flush()
    s = ChatSession(id=session_id, scenario_generation=clock.scenario_generation, draft={}, messages=[], state="collecting")
    db.add(s)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.get(ChatSession, session_id)
        if existing is None:
            raise
        return existing
    return s


def _push(s: ChatSession, role: str, text: str, clock: Clock, **extra: Any) -> None:
    msgs = list(s.messages or [])
    msgs.append({"role": role, "text": text, "at": iso(clock.now), **extra})
    s.messages = msgs[-80:]


def _draft_view(db: Session, draft: dict[str, Any]) -> dict[str, Any]:
    view = dict(draft)
    if draft.get("catalog_item_id"):
        item = catalog_service.get_item(db, draft["catalog_item_id"])
        view["catalog_item"] = catalog_service.snapshot_of(item) if item else None
    if draft.get("location_id"):
        loc = db.get(Location, draft["location_id"])
        view["location"] = {"id": loc.id, "name": loc.name, "lat": loc.lat, "lon": loc.lon} if loc else None
    return view


def _missing(draft: dict[str, Any]) -> list[str]:
    """Slot order: problem → address → expedite? → window (only when not expediting) → payment (paid slot only) → contact.

    The expedite question comes first because it decides whether a time window is a question at all: "send someone now"
    is an answer to "when", not a modifier of it.
    """
    need = []
    if not draft.get("catalog_item_id"):
        need.append("problem")
    if not draft.get("location_id") or not (draft.get("address") or {}).get("confirmed"):
        need.append("address")
    if draft.get("expedite_now") is None:
        need.append("expedite_choice")
    if not draft.get("expedite_now") and not draft.get("window_start"):
        need.append("window")
    if draft.get("window_paid_required") and not draft.get("paid_decided"):
        need.append("payment")
    if not draft.get("customer_name") or not draft.get("contact_phone"):
        need.append("contact")
    return need


def _ask_for(missing: list[str], lang: str) -> str:
    q = {"problem": "What appliance or area needs repair, and what exactly is wrong?",
         "address": "Where should the technician come? Search your address or postal code, or drop a pin, then confirm the unit number.",
         "expedite_choice": "Do you need someone now, as fast as possible (paid, simulated)? Otherwise pick a time slot that suits you.",
         "window": "When should the technician come? Slots marked with a card are already taken by other appointments — "
                   "choosing one is possible against a (simulated) fee, because we then move those appointments.",
         "contact": "Please share your name and a contact phone number."}
    if lang == "zh":
        q = {"problem": "请告诉我需要维修的设备或区域，以及具体的问题。", "address": "请搜索地址或邮编，或在地图上选点，然后确认单元号。",
             "expedite_choice": "需要师傅现在就来吗？如果不急，可以直接选一个上门时间段。",
             "window": "请选择上门时间段。带银行卡标记的时段已被其他预约占用，可付费选择（我们会为您挪动这些预约）。",
             "contact": "请提供您的姓名和联系电话。"}
    return q[missing[0]]


EXPEDITE_OPTIONS = [{"type": "expedite_now", "now": True, "label": "Send someone now (paid, simulated)"},
                    {"type": "expedite_now", "now": False, "label": "No — let me pick a time slot"}]
PAYMENT_OPTIONS = [{"type": "payment", "paid": True, "label": "Confirm payment (simulated)"}, {"type": "payment", "paid": False, "label": "No payment"}]


def _payment_question(draft: dict[str, Any], lang: str) -> str:
    """Honest explanation of what a (simulated) payment does, depending on the window the customer picked."""
    if draft.get("window_paid_required"):
        if lang == "zh":
            return ("付费的作用是把订单优先级提高到 P1：系统优先安排、必要时把少量普通订单往后挪，之后不会被其他普通订单挤开；"
                    "本演示的付款是模拟的，不产生真实扣款。是否确认付费？")
        return ("Paying raises the order to P1: it is scheduled first and, if needed, a few normal orders are moved slightly later; "
                "afterwards it cannot be bumped by other normal orders. Payment in this demo is simulated — nothing is really charged. "
                "Confirm the payment?")
    if lang == "zh":
        return "你已有空闲时段，付费不会让时间更早，只是提高优先级、避免之后被其他订单挤开。是否付费？"
    return ("You already have a free window, so paying will not make it earlier — it only raises the priority so the slot cannot be "
            "bumped later by other orders. Pay anyway (simulated)?")


def _card(db: Session, clock: Clock, draft: dict[str, Any]) -> dict[str, Any]:
    item = catalog_service.get_item(db, draft["catalog_item_id"])
    addr = draft.get("address") or {}
    assert item
    urgent, paid = bool(draft.get("urgent")), bool(draft.get("paid_expedite"))
    now = bool(draft.get("expedite_now"))
    return {
        "problem": f"{item.trade_type} – {item.problem_name}",
        "repair_duration_minutes": item.repair_duration_minutes,
        "location": addr.get("formatted_address") or (getattr(db.get(Location, draft["location_id"]), "name", "") if draft.get("location_id") else ""),
        "unit": addr.get("unit_number") or ("not applicable" if addr.get("unit_not_applicable") else "—"),
        "window": ("as soon as possible — " + (draft.get("expedite_preview") or {}).get("earliest_start", "")[11:16]
                   if now else f"{hhmm(parse_iso(draft['window_start']))}–{hhmm(parse_iso(draft['window_end']))}"),
        "customer_name": draft.get("customer_name"), "contact_phone": draft.get("contact_phone"),
        "urgent": urgent, "paid_expedite": paid, "expedite_now": now,
        "window_affected": int(draft.get("window_affected") or 0),
        "expedite": ("Someone now, as fast as possible (simulated payment)" if now
                     else "Paid slot (simulated payment)" if paid else "Standard slot"),
        "description": draft.get("description", ""), "excluded_technicians": draft.get("excluded_technician_ids", []),
    }


# ------------------------------------------------------------------ order status for the customer
def _order_status(db: Session, clock: Clock, o: WorkOrder) -> dict[str, Any]:
    a = db.scalars(select(Assignment).where(Assignment.order_id == o.id, Assignment.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED]))
                   .order_by(Assignment.created_at.desc())).first()
    tech = db.get(Technician, a.technician_id) if a else None
    if o.lifecycle_status == LifecycleStatus.CANCELLED:
        text = "Cancelled."
    elif o.lifecycle_status == LifecycleStatus.COMPLETED:
        text = f"Completed at {hhmm(o.completed_at)}."
    elif o.lifecycle_status == LifecycleStatus.IN_PROGRESS:
        text = f"{tech.name if tech else 'Technician'} is working on it (started {hhmm(o.service_started_at)})."
    elif o.lifecycle_status == LifecycleStatus.ARRIVED:
        text = f"{tech.name if tech else 'Technician'} has arrived."
    elif o.lifecycle_status == LifecycleStatus.EN_ROUTE:
        text = f"{tech.name if tech else 'Technician'} is on the way, ETA {hhmm(a.arrival) if a else 'unknown'}."
    elif a is not None:
        text = f"Scheduled: {tech.name if tech else 'technician'} plans to start at {hhmm(a.service_start)}."
    elif o.scheduling_status == "PENDING_REVIEW":
        text = "A dispatcher is reviewing the schedule; ETA not confirmed yet."
    else:
        text = "Not yet assigned; ETA unknown. We are working on it."
    return {"order_id": o.id, "lifecycle_status": o.lifecycle_status, "scheduling_status": o.scheduling_status,
            "effective_priority": o.effective_priority, "paid_expedite": o.paid_expedite, "can_cancel": o.lifecycle_status == "OPEN",
            "expedite": expedite_service.state_view(o),
            "eta": iso(a.service_start) if a and o.lifecycle_status in ("OPEN", "EN_ROUTE", "ARRIVED") else None,
            "technician_name": tech.name if tech else None, "window_start": iso(o.window_start), "window_end": iso(o.window_end),
            "problem": f"{o.catalog_snapshot.get('trade_type')} – {o.catalog_snapshot.get('problem_name')}", "text": text,
            "address": (o.address or {}).get("formatted_address") or o.location_name, "report_status": o.report_status,
            "cancel_block_reason": None if o.lifecycle_status == "OPEN" else
            ("already cancelled" if o.lifecycle_status == "CANCELLED" else "already completed" if o.lifecycle_status == "COMPLETED"
             else f"technician already {o.lifecycle_status.lower().replace('_', ' ')}; cancellation not allowed")}


def session_orders(db: Session, clock: Clock, session_id: str, customer_id: str | None = None) -> list[dict[str, Any]]:
    q = select(WorkOrder).where(WorkOrder.scenario_generation == clock.scenario_generation)
    rows = [o for o in db.scalars(q.order_by(WorkOrder.created_at.desc())).all()
            if o.customer_ref == session_id or (customer_id and o.customer_id == customer_id)]
    return [_order_status(db, clock, o) for o in rows]


def owned_order(db: Session, order_id: str, session_id: str | None, customer_id: str | None) -> WorkOrder:
    o = db.get(WorkOrder, order_id)
    if o is None:
        raise OrderError("not_found", "order not found", status=404)
    if not ((session_id and o.customer_ref == session_id) or (customer_id and o.customer_id == customer_id)):
        raise OrderError("forbidden", "this order belongs to another customer", status=403, kind="FORBIDDEN")
    return o


# ------------------------------------------------------------------ window proposals (the whole day, not just the free part)
def _slot_option(w: dict[str, Any]) -> dict[str, Any]:
    """One slot button. A busy slot is offered, not hidden: the customer decides whether it is worth the fee."""
    state = w.get("state", "available")
    label = w["label"]
    if state == "available":
        label += " · free"
    elif state == "paid":
        label += " · taken — available against a fee"
    elif state == "impossible":
        label += " · no technician today"
    else:
        label += " · not checked"
    return {"type": "window", "window_start": w["window_start"], "window_end": w["window_end"], "label": label,
            "state": state, "selectable": state in ("available", "paid"),
            "technician_id": w.get("technician_id"), "earliest_start": w.get("earliest_start"),
            "paid_required": bool(w.get("paid_required")), "affected_count": int(w.get("affected_count") or 0),
            "decision": w.get("decision"), "note": w.get("note")}


def _window_options(db: Session, clock: Clock, draft: dict[str, Any], exclude: list[tuple[str, str]] | None = None,
                    notes: list[str] | None = None) -> list[dict[str, Any]]:
    """Every remaining slot of the day, each marked free / purchasable / impossible (4th meeting, 加急逻辑 §2a).

    Showing only zero-disturbance slots made the schedule look emptier than it is and hid the paid option entirely; a
    customer who can see that 14:00 is taken but buyable can make that call themselves.
    """
    if not (draft.get("catalog_item_id") and draft.get("location_id")):
        return []
    pen = customer_service.preference_penalties(db, draft.get("customer_id"))
    grid = negotiation_service.day_grid(db, clock, catalog_item_id=draft["catalog_item_id"], location_id=draft["location_id"],
                                        excluded_technicians=draft.get("excluded_technician_ids"), penalties=pen)
    skip = {(a, b) for a, b in (exclude or [])}
    slots = [w for w in grid["data"]["slots"] if (w["window_start"], w["window_end"]) not in skip]
    if notes is not None:
        free_n, paid_n = grid["data"]["available"], grid["data"]["paid"]
        if paid_n:
            notes.append(f"{free_n} slot(s) are free; {paid_n} are already taken and can be freed against a simulated fee.")
    return [_slot_option(w) for w in slots]


# ------------------------------------------------------------------ main handler
def handle(db: Session, session_id: str, message: str, action: str | None, payload: dict[str, Any] | None,
           pre: dict[str, Any] | None = None) -> dict[str, Any]:
    """`pre` = model results computed by the caller BEFORE taking the state lock (understanding / complaint classification),
    so model latency never blocks the clock, the dispatcher or the other apps."""
    pre = pre or {}
    clock = get_clock(db)
    s = get_session(db, session_id, clock)
    draft = dict(s.draft or {})
    lang = draft.get("language", "en")
    reply: dict[str, Any] = {"text": "", "options": [], "card": None}
    payload = payload or {}
    llm_info: dict[str, Any] = {}
    customer_id = draft.get("customer_id")

    # human takeover in progress: the agent pauses business commitments, the customer can still add information
    case = human_service.open_case_for_session(db, session_id, customer_id)
    if case is not None and action not in ("status", "reset", "identify") and not (action or "").startswith("order_"):
        text = (message or "").strip() or f"[{action}]"
        _push(s, "user", text, clock)
        human_service.customer_message(db, clock, case, text)
        reply["text"] = ("A support agent has your request" + (f" ({case.assignee})" if case.assignee else " (waiting for pickup)") +
                         ". Your message was added to the case; the assistant will not make scheduling changes until they finish.")
        reply["human_case"] = human_service.case_view(case)
        _push(s, "assistant", reply["text"], clock)
        s.draft = draft
        return _response(db, clock, s, reply, llm_info)

    def finish_collect() -> None:
        missing = _missing(draft)
        if missing:
            question = _payment_question(draft, lang) if missing[0] == "payment" else _ask_for(missing, lang)
            reply["text"] = (reply["text"] + " " if reply["text"] else "") + question
            extra: list[dict[str, Any]] = []
            if missing[0] == "address":
                extra = [{"type": "address", "prefill": draft.get("address")}]  # geocoded / area point pre-fills the modal
                saved = customer_service.list_addresses(db, customer_id) if customer_id else []
                for adr in saved:
                    extra.append({"type": "saved_address", "id": adr["id"], "label": f"{adr['formatted_address']}{' #' + adr['unit_number'] if adr.get('unit_number') else ''}"})
                if saved:
                    # returning customer: confirm their own details here, where they are needed — never on a public roster
                    who = " · ".join(x for x in (draft.get("customer_name"), draft.get("contact_phone")) if x)
                    reply["text"] = (f"还是上次的地址吗？{'（' + who + '）' if who else ''}选择下面的地址，或输入一个新地址。" if lang == "zh"
                                      else f"Same address as last time?{' (' + who + ')' if who else ''} Pick one below, or enter a different address.")
            elif missing[0] == "expedite_choice":
                extra = [dict(o) for o in EXPEDITE_OPTIONS]
            elif missing[0] == "payment":
                extra = [dict(o) for o in PAYMENT_OPTIONS]
            elif missing[0] == "window":
                notes: list[str] = []
                extra = _window_options(db, clock, draft, notes=notes)
                if notes:
                    reply["text"] += " " + " ".join(notes)
                pen = customer_service.preference_penalties(db, customer_id)
                flagged = {o["technician_id"] for o in extra if o.get("technician_id") in pen}
                if extra and flagged and flagged == {o["technician_id"] for o in extra} and not draft.get("preference_notice_shown"):
                    draft["preference_notice_shown"] = True
                    names = ", ".join(getattr(db.get(Technician, t), "name", t) for t in flagged)
                    reply["text"] += (f" Note: the only technician available for these windows is {names}, whom you rated low before. "
                                      "You can proceed, or exclude them for this order (options may be later or fewer).")
                    extra = extra + [{"type": "exclude_technician", "technician_id": t, "label": f"Do not send {getattr(db.get(Technician, t), 'name', t)} this time"} for t in flagged]
                if not extra:
                    reply["text"] += " No feasible window is left today — you can ask for a callback (Talk to a human)."
                    extra = [{"type": "handoff", "label": "Talk to a human about scheduling"}]
                else:
                    extra.append({"type": "more_windows", "label": "None of these suit me"})
            elif missing[0] == "contact":
                extra = [{"type": "contact_form"}]
            reply["options"] = list(reply.get("options") or []) + extra
            s.state = "collecting"
        else:
            reply["card"] = _card(db, clock, draft)
            reply["text"] = (reply["text"] + " " if reply["text"] else "") + (
                "请核对以下工单信息，确认后提交。" if lang == "zh" else "Please check the details below and confirm to submit.")
            reply["options"] = [{"type": "confirm"}]
            if not draft.get("expedite_now"):
                reply["options"].append({"type": "restart_expedite", "label": "Actually, send someone now"})
            s.state = "confirming"

    # ---------------------------------------------------------------- actions
    if action == "reset":
        keep = {k: draft.get(k) for k in ("customer_id", "language", "customer_name", "contact_phone") if draft.get(k)}
        draft = keep
        s.state = "collecting"
        reply["text"] = "Started a new request. What needs repair?"
    elif action == "identify":
        login = str(payload.get("demo_login") or "").strip()
        if not login:
            raise OrderError("missing_login", "demo_login is required", kind="DATA_INCOMPLETE")
        cust = customer_service.get_or_create_by_login(db, clock, login, payload.get("name"), payload.get("phone"))
        draft["customer_id"] = cust.id
        customer_id = cust.id
        # A brand-new customer's `name` is their demo login (new_a1b2), which is a handle, not a name. Pre-filling it
        # satisfied the contact slot, so the order was created without ever asking who the customer is.
        if cust.name and cust.name != cust.demo_login:
            draft.setdefault("customer_name", cust.name)
        if cust.phone:
            draft.setdefault("contact_phone", cust.phone)
        addrs = customer_service.list_addresses(db, cust.id)
        hist = customer_service.customer_history(db, clock, cust.id)
        if addrs or hist["orders"]:
            reply["text"] = (f"Welcome back, {cust.name}. I have your phone {cust.phone or '—'} and {len(addrs)} saved address(es)"
                             f" from {len(hist['orders'])} earlier order(s). Use the saved details this time?")
            reply["options"] = [{"type": "use_saved", "label": "Use saved phone & default address"},
                                {"type": "new_details", "label": "I'll give new details"}]
            reply["history"] = {"orders": hist["orders"][:5], "addresses": addrs[:3], "negative_technicians": hist["negative_technicians"]}
            draft["returning"] = True
        else:
            reply["text"] = f"Hi {cust.name}! Describe the problem and I'll match it to our repair catalog."
        _push(s, "user", f"[identity] {login}", clock)
    elif action == "use_saved":
        addrs = customer_service.list_addresses(db, customer_id) if customer_id else []
        if addrs:
            a = addrs[0]
            draft["location_id"] = a["location_id"]
            draft["address"] = {**a, "confirmed": True, "this_time_only": False}
        reply["text"] = "Using your saved details." + ("" if addrs else " (No saved address found — please add one.)")
        _push(s, "user", "[use saved details]", clock)
        finish_collect()
    elif action == "new_details":
        draft.pop("location_id", None)
        draft.pop("address", None)
        reply["text"] = "Sure — tell me the new details as we go."
        _push(s, "user", "[new details]", clock)
        finish_collect()
    elif action == "select_catalog":
        item = catalog_service.get_item(db, str(payload.get("catalog_item_id", "")))
        if item is None:
            raise OrderError("invalid_catalog_item", "unknown catalog item", kind="DATA_INCOMPLETE")
        draft["catalog_item_id"] = item.id
        reply["text"] = f"Got it: {item.trade_type} – {item.problem_name} (about {item.repair_duration_minutes} min on site)."
        _push(s, "user", f"[selected] {item.trade_type} – {item.problem_name}", clock)
        _repeat_hint(db, clock, draft, reply)
        finish_collect()
    elif action == "set_address":
        addr = _confirm_address(db, clock, draft, payload)
        reply["text"] = f"Address confirmed: {addr['formatted_address']}" + (f", #{addr['unit_number']}" if addr.get("unit_number") else " (no unit)") + \
                        (f". {addr['note']}" if addr.get("snapped") else ".")
        _push(s, "user", f"[address] {addr['formatted_address']} {addr.get('unit_number') or ''}", clock)
        _repeat_hint(db, clock, draft, reply)
        finish_collect()
    elif action == "use_saved_address":
        aid = str(payload.get("address_id", ""))
        saved = customer_service.list_addresses(db, customer_id) if customer_id else []
        matches = [x for x in saved if x["id"] == aid]
        if not matches:
            raise OrderError("not_found", "saved address not found", status=404)
        a = matches[0]
        draft["location_id"] = a["location_id"]
        draft["address"] = {**a, "confirmed": True, "this_time_only": bool(payload.get("this_time_only"))}
        reply["text"] = f"Using {a['formatted_address']}{' #' + a['unit_number'] if a.get('unit_number') else ''}."
        _push(s, "user", "[saved address]", clock)
        finish_collect()
    elif action == "select_location":  # legacy preset / map pin without unit — unit is then still pending
        if payload.get("lat") is not None and payload.get("lon") is not None:
            from app.services.location_service import resolve_point
            res = resolve_point(db, float(payload["lat"]), float(payload["lon"]), payload.get("name"))
            draft["location_id"] = res["location"]["id"]
            draft["address"] = {"formatted_address": res["location"]["name"], "latitude": res["location"]["lat"], "longitude": res["location"]["lon"],
                                "source": "map_pin", "confirmed": False}
            reply["text"] = f"Point set to {res['location']['name']}. Please confirm the unit / floor (or mark it not applicable)."
            reply["options"] = [{"type": "address", "prefill": draft["address"]}]
            _push(s, "user", f"[map pin] {float(payload['lat']):.4f}, {float(payload['lon']):.4f}", clock)
        else:
            loc = db.get(Location, str(payload.get("location_id", "")))
            if loc is None:
                raise OrderError("invalid_location", "unknown location", kind="DATA_INCOMPLETE")
            draft["location_id"] = loc.id
            draft["address"] = {"formatted_address": loc.name, "latitude": loc.lat, "longitude": loc.lon, "source": "preset", "confirmed": False}
            reply["text"] = f"Area set to {loc.name}. Please confirm the exact address and unit."
            reply["options"] = [{"type": "address", "prefill": draft["address"]}]
            _push(s, "user", f"[location] {loc.name}", clock)
    elif action == "set_window":
        ws, we = parse_iso(str(payload["window_start"])), parse_iso(str(payload["window_end"]))
        if we <= ws or we < clock.now:
            raise OrderError("invalid_window", "window must end in the future and after its start", kind="DATA_INCOMPLETE")
        draft["window_start"], draft["window_end"] = ws.isoformat() + "Z", we.isoformat() + "Z"
        if payload.get("technician_id"):
            draft["proposed_technician_id"] = payload["technician_id"]
        draft["window_paid_required"] = bool(payload.get("paid_required", False))
        draft["window_affected"] = int(payload.get("affected_count") or 0)
        reply["text"] = (f"Appointment window {hhmm(ws)}–{hhmm(we)}" + (" (expedited: payment required)" if draft["window_paid_required"] else "")
                         + " (will be validated again on submit).")
        _push(s, "user", f"[window] {hhmm(ws)}–{hhmm(we)}", clock)
        finish_collect()
    elif action == "set_expedite_now":
        want_now = bool(payload.get("now"))
        _push(s, "user", "[expedite] " + ("send someone now" if want_now else "pick a time slot"), clock)
        if not want_now:
            draft["expedite_now"] = False
            draft["urgent"] = False
            reply["text"] = "好的，请选择上门时间段。" if lang == "zh" else "Okay — pick a time slot below."
            finish_collect()
        else:
            # "as fast as possible" is only an honest offer once we know somebody can actually come
            now_view = negotiation_service.earliest_now(db, clock, catalog_item_id=draft["catalog_item_id"],
                                                        location_id=draft["location_id"],
                                                        excluded_technicians=draft.get("excluded_technician_ids"))
            draft["expedite_preview"] = now_view
            if not now_view.get("possible"):
                draft["expedite_now"] = False
                draft["urgent"] = True
                reply["text"] = (("很抱歉，接下来的几小时内没有具备该技能的师傅能到达。" if lang == "zh"
                                  else "I'm sorry — nobody with the right skill can reach you in the next few hours. ")
                                 + ("请选择一个时间段，或转人工协调。" if lang == "zh" else "Pick a slot below, or ask a human to coordinate."))
                finish_collect()
                reply["options"] = list(reply.get("options") or []) + [{"type": "handoff", "label": "Ask a human to coordinate"}]
            else:
                draft["expedite_now"] = True
                draft["urgent"] = True
                draft["paid_expedite"], draft["paid_decided"] = True, True
                draft["window_start"], draft["window_end"] = now_view["window_start"], now_view["window_end"]
                draft["window_paid_required"] = True
                draft["window_affected"] = int(now_view.get("affected_count") or 0)
                draft["proposed_technician_id"] = now_view.get("technician_id")
                moved = int(now_view.get("affected_count") or 0)
                reply["text"] = (f"可以。{now_view['technician_name']} 预计 {now_view['earliest_start'][11:16]} 到达"
                                 + (f"，我们会为此挪动 {moved} 个其他预约。" if moved else "，不影响其他预约。")
                                 + "（模拟付款，不产生真实扣款。）"
                                 if lang == "zh" else
                                 f"Yes — {now_view['technician_name']} can start around {now_view['earliest_start'][11:16]}"
                                 + (f", and we move {moved} other appointment(s) to do it." if moved else ", without moving anyone else.")
                                 + " Payment is simulated; nothing is charged.")
                finish_collect()
    elif action == "confirm_payment":
        paid = bool(payload.get("paid"))
        draft["paid_expedite"], draft["paid_decided"] = paid, True
        if paid:
            reply["text"] = "模拟付款已记录：订单将以 P1 创建。" if lang == "zh" else "Simulated payment recorded: your order will be created as P1."
        elif draft.get("window_paid_required"):
            for k in ("window_start", "window_end", "window_paid_required", "window_affected", "proposed_technician_id"):
                draft.pop(k, None)
            draft["expedite_now"] = False
            reply["text"] = "不付费则无法占用该时段，请另选一个时段。" if lang == "zh" else "Without payment that slot cannot be taken; please pick another one."
        else:
            reply["text"] = "好的，不付费，保持普通优先级（P3）。" if lang == "zh" else "Okay, no payment — normal priority (P3)."
        _push(s, "user", "[payment] " + ("confirmed (simulated)" if paid else "declined"), clock)
        finish_collect()
    elif action == "more_windows":
        shown = draft.get("rejected_windows", [])
        last = [(o["window_start"], o["window_end"]) for o in (s.messages[-1].get("options") or []) if o.get("type") == "window"] if s.messages else []
        shown = shown + last
        draft["rejected_windows"] = shown
        notes: list[str] = []
        opts = _window_options(db, clock, draft, exclude=[tuple(x) for x in shown], notes=notes)
        _push(s, "user", "[none of these windows]", clock)
        if opts:
            reply["text"] = "Here are other feasible options (a different technician or later start):" + (" " + " ".join(notes) if notes else "")
            reply["options"] = opts + [{"type": "more_windows", "label": "None of these suit me"}, {"type": "handoff", "label": "Ask a human to coordinate"}]
        else:
            reply["text"] = "I have no further feasible window today without disturbing other customers. A human coordinator can look at options (e.g. tomorrow or a reschedule)."
            reply["options"] = [{"type": "handoff", "label": "Talk to a human about scheduling"}]
    elif action == "set_contact":
        name, phone = str(payload.get("customer_name", "")).strip(), str(payload.get("contact_phone", "")).strip()
        if not name or not phone:
            raise OrderError("missing_contact", "name and phone are required", kind="DATA_INCOMPLETE")
        draft["customer_name"], draft["contact_phone"] = name, phone
        if customer_id and payload.get("save", True):
            c = db.get(Customer, customer_id)
            if c:
                c.name, c.phone = name, phone
        reply["text"] = f"Thanks {name}."
        _push(s, "user", f"[contact] {name}, {phone}", clock)
        finish_collect()
    elif action == "restart_expedite":
        # the customer changed their mind on the confirm card: re-ask the expedite question rather than bolting a
        # paid flag onto a slot they already chose
        for k in ("expedite_now", "window_start", "window_end", "window_paid_required", "window_affected",
                  "proposed_technician_id", "paid_expedite", "paid_decided", "expedite_preview"):
            draft.pop(k, None)
        _push(s, "user", "[expedite] reconsider", clock)
        finish_collect()
    elif action == "exclude_technician":
        tid = str(payload.get("technician_id", ""))
        if db.get(Technician, tid) is None:
            raise OrderError("not_found", "technician not found", status=404)
        ex = list(draft.get("excluded_technician_ids", []))
        if tid not in ex:
            ex.append(tid)
        draft["excluded_technician_ids"] = ex
        draft.pop("window_start", None)
        draft.pop("window_end", None)
        reply["text"] = "Noted for this order only: that technician will not be proposed. Let me re-check the windows."
        _push(s, "user", f"[exclude technician] {tid}", clock)
        finish_collect()
    elif action == "confirm":
        missing = _missing(draft)
        if missing:
            finish_collect()
        else:
            addr = draft.get("address") or {}
            order = order_service.create_order(
                db, clock, customer_ref=session_id, customer_name=draft["customer_name"], contact_phone=draft["contact_phone"],
                description=draft.get("description", ""), location_id=draft["location_id"], catalog_item_id=draft["catalog_item_id"],
                window_start=parse_iso(draft["window_start"]), window_end=parse_iso(draft["window_end"]), paid_expedite=bool(draft.get("paid_expedite")),
                expedite_now=bool(draft.get("expedite_now")),
                customer_id=customer_id, address={k: addr.get(k) for k in ("formatted_address", "postal_code", "building_name", "street_address",
                                                                             "unit_number", "unit_not_applicable", "latitude", "longitude", "source", "confirmed_at")},
                excluded_technician_ids=draft.get("excluded_technician_ids"),
            )
            outcome = dispatch_order(db, order.id, trigger="chatbot_submit")
            st = _order_status(db, clock, db.get(WorkOrder, order.id))  # type: ignore[arg-type]
            reply["text"] = f"Order {order.id} created. {st['text']}"
            if outcome.decision == "manual":
                reply["text"] += " 调度员确认中。" if lang == "zh" else " A dispatcher is confirming the technician."
            elif outcome.decision == "unresolved":
                reply["text"] += " No technician could be confirmed for that window yet; we keep looking and will update you."
            reply["order"] = st
            _push(s, "user", "[confirm]", clock)
            keep = {k: draft.get(k) for k in ("customer_id", "language", "customer_name", "contact_phone") if draft.get(k)}
            draft = keep
            s.state = "submitted"
    elif action == "cancel_order":
        oid = str(payload.get("order_id", ""))
        try:
            owned_order(db, oid, session_id, customer_id)
            res = event_service.customer_cancel(db, oid, customer_ref=None, actor="customer_chat", reason=str(payload.get("reason") or "customer request"))
            reply["text"] = f"Order {oid} has been cancelled." + (" (Paid expedite recorded; no real refund is processed.)" if res.get("paid_expedite") else "")
        except OrderError as exc:
            reply["text"] = f"Sorry, order {oid} cannot be cancelled: {exc.message}"
        _push(s, "user", f"[cancel] {oid}", clock)
    elif action == "expedite_order":
        oid = str(payload.get("order_id", ""))
        _push(s, "user", f"[expedite now] {oid}", clock)
        try:
            o = owned_order(db, oid, session_id, customer_id)
            res = expedite_service.expedite(db, clock, o, actor="customer_chat", idempotency_key=f"expedite:{oid}")
            reply["text"] = str(res.get("message") or "Your order is expedited.")
            reply["order"] = _order_status(db, clock, o)
        except OrderError as exc:
            reply["text"] = exc.message
    elif action == "keep_order":
        reply["text"] = "Okay — your order stays as it is."
        _push(s, "user", "[keep as is]", clock)
    elif action == "complaint":
        oid = str(payload.get("order_id", ""))
        text = str(payload.get("text") or message or "")
        owned_order(db, oid, session_id, customer_id)
        cls = pre.get("classification") or get_llm().classify_complaint(text)
        llm_info = dict(pre.get("llm") or get_llm().last)
        ctype = "lateness" if cls.complaint_type == "lateness" else cls.complaint_type
        res = event_service.complaint(db, oid, complaint_type=ctype, text=text, customer_ref=None, classification=cls.model_dump())
        if ctype == "lateness":
            reply["text"] = ("We checked the schedule: the deadline has passed and no technician has started, so your order is now top priority (P0)."
                             if res.get("verified") else "We received your complaint. The appointment deadline has not passed yet; we are monitoring the technician's ETA.")
        else:
            reply["text"] = f"Thank you for the feedback ({ctype}). A service manager will follow up; your appointment is unchanged."
            human_service.flag_for_human(db, clock, source="CUSTOMER_REQUEST", category=f"complaint_{ctype}", urgency="normal",
                                         reason_summary=f"{ctype} complaint on {oid}: {text[:200]}", customer_id=customer_id, session_id=session_id,
                                         order_id=oid, evidence_refs=[f"order:{oid}"], idempotency_key=f"complaint:{oid}:{ctype}")
        reply["classification"] = cls.model_dump()
        _push(s, "user", f"[complaint on {oid}] {text}", clock)
    elif action == "handoff":
        text = str(payload.get("reason") or message or "customer asked for a human")
        case, _created = human_service.flag_for_human(
            db, clock, source="CUSTOMER_REQUEST", category=str(payload.get("category") or "customer_request"), urgency="normal",
            reason_summary=text, customer_id=customer_id, session_id=session_id, order_id=payload.get("order_id"),
            evidence_refs=[f"chat:{session_id}"], attempted_actions=[{"context": _context_summary(db, draft, s)}],
            suggested_next_action="Read the conversation summary and reply in the case; the assistant is paused for this customer.")
        reply["text"] = ("Connecting you to a human agent. Everything you told me is attached to the case — you will not need to repeat it. "
                         f"Case {case.id} is {case.status}.")
        reply["human_case"] = human_service.case_view(case)
        _push(s, "user", "[talk to a human] " + text, clock)
    elif action == "answer_question":
        q = db.get(CustomerQuestion, str(payload.get("question_id", "")))
        if q is None or q.session_id != session_id:
            raise OrderError("not_found", "question not found", status=404)
        q.status, q.answer, q.answered_at = "answered", {"value": payload.get("answer"), "text": message}, clock.now
        reply["text"] = "Thanks, noted."
        _push(s, "user", f"[answer] {payload.get('answer')}", clock)
        from app.agents.runtime import wake_task_for_question
        wake_task_for_question(db, q)
        finish_collect() if s.state == "collecting" else None
    elif action == "status":
        orders = session_orders(db, clock, session_id, customer_id)
        reply["text"] = "\n".join(f"{o['order_id']}: {o['text']}" for o in orders) if orders else "You have no orders in this session yet."
    else:
        _free_text(db, clock, s, draft, message, reply, finish_collect, pre.get("understanding"))
        llm_info = reply.pop("_llm", {}) or llm_info
        lang = draft.get("language", lang)
    s.draft = draft
    _push(s, "assistant", reply["text"], clock, options=reply.get("options"), card=reply.get("card"))
    return _response(db, clock, s, reply, llm_info)


def _context_summary(db: Session, draft: dict[str, Any], s: ChatSession) -> dict[str, Any]:
    return {"draft": {k: v for k, v in _draft_view(db, draft).items() if k not in ("rejected_windows",)},
            "last_messages": [{"role": m["role"], "text": m["text"][:200]} for m in (s.messages or [])[-8:]]}


def _repeat_hint(db: Session, clock: Clock, draft: dict[str, Any], reply: dict[str, Any]) -> None:
    if not (draft.get("catalog_item_id") and (draft.get("customer_id") or draft.get("location_id"))):
        return
    item = catalog_service.get_item(db, draft["catalog_item_id"])
    if item is None:
        return
    hint = customer_service.repeat_fault_hint(db, clock, draft.get("customer_id"), draft.get("location_id"), item.trade_type)
    if hint and hint["count"] > 0 and not draft.get("repeat_hint_shown"):
        draft["repeat_hint_shown"] = True
        reply["text"] += f" I see {hint['count']} earlier {item.trade_type} repair(s) here in the last {hint['window_days']} days — I'll pass that history to the technician."
        reply["history_hint"] = hint


def _confirm_address(db: Session, clock: Clock, draft: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    lat, lon = payload.get("latitude"), payload.get("longitude")
    if lat is None or lon is None:
        raise OrderError("invalid_location", "latitude/longitude are required (from a search result or map pin)", kind="DATA_INCOMPLETE")
    unit = str(payload.get("unit_number") or "").strip().lstrip("#").strip()  # stored without the leading "#"
    na = bool(payload.get("unit_not_applicable"))
    if not unit and not na:
        raise OrderError("unit_required", "unit / floor number is required, or mark it as not applicable", {"field": "unit_number"}, kind="DATA_INCOMPLETE")
    formatted = str(payload.get("formatted_address") or "").strip()
    if not formatted:
        raise OrderError("invalid_location", "formatted_address is required", kind="DATA_INCOMPLETE")
    save = bool(payload.get("save", True))
    if draft.get("customer_id"):
        cust = customer_service.get_customer(db, draft["customer_id"])
        addr = customer_service.upsert_address(db, clock, cust, lat=float(lat), lon=float(lon), formatted_address=formatted,
                                               postal_code=payload.get("postal_code"), building_name=payload.get("building_name"),
                                               street_address=payload.get("street_address"), unit_number=unit or None, unit_not_applicable=na,
                                               source=str(payload.get("source") or "search"), save=save)
    else:
        from app.services.location_service import resolve_point
        point = resolve_point(db, float(lat), float(lon), formatted)
        addr = {"location_id": point["location"]["id"], "formatted_address": formatted, "postal_code": payload.get("postal_code"),
                "building_name": payload.get("building_name"), "street_address": payload.get("street_address"), "unit_number": unit or None,
                "unit_not_applicable": na, "latitude": float(lat), "longitude": float(lon), "source": str(payload.get("source") or "search"),
                "snapped": point["snapped"], "note": point["note"], "confirmed_at": iso(clock.now)}
    draft["location_id"] = addr["location_id"]
    draft["address"] = {**addr, "confirmed": True, "this_time_only": not save}
    return addr


def _free_text(db: Session, clock: Clock, s: ChatSession, draft: dict[str, Any], message: str, reply: dict[str, Any], finish_collect,
               pre_understanding: Any = None) -> None:
    text = (message or "").strip()
    if not text:
        reply["text"] = "How can I help with your repair today?"
        return
    _push(s, "user", text, clock)
    session_id = s.id
    customer_id = draft.get("customer_id")
    # 1. safety first (deterministic triggers + model flag), before any sales / form logic
    det = safety_service.detect(text)
    tracer = start_run(db, agent="UnderstandingAgent", trigger="chat_message", scenario_generation=clock.scenario_generation,
                       input_refs={"session_id": session_id})
    with tracer.step("interpret") as st:
        u = pre_understanding or understand(db, clock, text, known=_known_slots(db, draft))
        for tc in u.tool_calls:
            st["tool_calls"].append(tc)
        st["summary"] = u.interpreted.summary
    reply["_llm"] = u.llm
    model_flag = u.interpreted.safety_concern.model_dump() if u.interpreted.safety_concern else {}
    danger = det["danger_type"] if (det["danger_type"] and not det["guarded"]) else (model_flag.get("type") if float(model_flag.get("confidence") or 0) >= 0.7 else None)
    if danger:
        loc = draft.get("address") or {}
        inc = safety_service.create_incident(db, clock, danger_type=danger, description=text, customer_id=customer_id, session_id=session_id,
                                             order_id=None, known_location={"formatted_address": loc.get("formatted_address"), "lat": loc.get("latitude"), "lon": loc.get("longitude")},
                                             detection={"deterministic": det, "model": model_flag}, status="open" if loc else "draft")
        tracer.tool(st, "create_safety_incident", incident_id=inc.id, danger_type=danger)
        # The product does not put emergency numbers in front of the customer and does not record whether they called:
        # dialling 995/999 is the customer's own decision through their phone, and a repair app claiming to handle that
        # would be making a promise it cannot keep. What it does do is stop assisting and put a person on it immediately.
        reply["text"] = SAFETY_TEMPLATES.get(danger, SAFETY_TEMPLATES["other"]) + " I have alerted a coordinator, who is taking this over now."
        reply["options"] = [{"type": "handoff", "label": "Talk to a human now", "category": "safety_incident"}]
        reply["incident"] = safety_service.incident_view(inc)
        draft["description"] = text if not draft.get("description") else draft["description"]
        tracer.finish("completed", f"safety incident {inc.id} ({danger})", {"incident_id": inc.id})
        return
    tracer.finish("completed", u.interpreted.summary, {"intent": u.interpreted.intent, "catalog_item_id": u.interpreted.catalog_item_id, "llm": u.llm})
    lang = u.interpreted.language or draft.get("language", "en")
    draft["language"] = lang
    intent = u.interpreted.intent
    if intent == "human" or _wants_human(text):
        case, _ = human_service.flag_for_human(db, clock, source="CUSTOMER_REQUEST", category="customer_request", urgency="normal",
                                               reason_summary=text, customer_id=customer_id, session_id=session_id,
                                               evidence_refs=[f"chat:{session_id}"], attempted_actions=[{"context": _context_summary(db, draft, s)}],
                                               suggested_next_action="Reply in the case; the assistant is paused for this customer.")
        reply["text"] = f"Connecting you to a human agent (case {case.id}). Everything so far is attached; you will not need to repeat it."
        reply["human_case"] = human_service.case_view(case)
        return
    # "please hurry my order" about an EXISTING order (not a new problem being described, not a booking in progress)
    if (intent == "expedite" or _wants_expedite(text)) and not draft.get("catalog_item_id") and not u.catalog_item \
            and session_orders(db, clock, session_id, customer_id):
        _expedite_offer(db, clock, session_orders(db, clock, session_id, customer_id), reply)
        return
    if intent == "cancel" and session_orders(db, clock, session_id, customer_id):
        orders = [o for o in session_orders(db, clock, session_id, customer_id) if o["can_cancel"]]
        reply["text"] = "Which order would you like to cancel?" if orders else "There is no order that can still be cancelled (departed or completed orders cannot be cancelled)."
        reply["options"] = [{"type": "cancel_order", "order_id": o["order_id"], "label": f"Cancel {o['order_id']} ({o['problem']})"} for o in orders]
        return
    if intent == "status" and session_orders(db, clock, session_id, customer_id):
        reply["text"] = "\n".join(f"{o['order_id']}: {o['text']}" for o in session_orders(db, clock, session_id, customer_id))
        return
    if intent == "complaint" and session_orders(db, clock, session_id, customer_id):
        orders = session_orders(db, clock, session_id, customer_id)
        reply["text"] = "I'm sorry to hear that. Which order is this about? I'll record the complaint."
        reply["options"] = [{"type": "complaint", "order_id": o["order_id"], "label": f"{o['order_id']} ({o['problem']})", "text": text} for o in orders]
        return
    acks: list[str] = []
    if u.interpreted.payment_claimed:
        acks.append("Note: payment claims in chat are not verified; the payment step in this chat records a simulated payment.")
    if u.interpreted.urgency_mentioned:
        # noted, but never answered on the customer's behalf: paying to be served now is an explicit decision
        if draft.get("urgent") is None:
            acks.append("I understand it is urgent — I'll ask whether you want someone sent now.")
        draft["urgent"] = True
    if u.catalog_item and u.catalog_item["catalog_item_id"] != draft.get("catalog_item_id"):
        draft["catalog_item_id"] = u.catalog_item["catalog_item_id"]
        draft["description"] = text if not draft.get("description") else draft["description"] + " | " + text
        acks.append(f"I understand: {u.catalog_item['trade_type']} – {u.catalog_item['problem_name']} (about {u.catalog_item['repair_duration_minutes']} min on site).")
        if u.alternatives:
            reply["options"] = [{"type": "catalog", "id": a["catalog_item_id"], "label": f"{a['trade_type']} – {a['problem_name']}"} for a in u.alternatives]
            acks.append("If that is not right, pick one of the alternatives.")
        _repeat_hint(db, clock, draft, reply)
    if u.interpreted.customer_name and not draft.get("customer_name"):
        draft["customer_name"] = u.interpreted.customer_name
        acks.append(f"Name: {u.interpreted.customer_name}.")
    if u.interpreted.contact_phone and not draft.get("contact_phone"):
        draft["contact_phone"] = u.interpreted.contact_phone
        acks.append(f"Phone: {u.interpreted.contact_phone}.")
    lr = u.location_resolution
    if u.location and u.location["id"] != draft.get("location_id"):
        draft["location_id"] = u.location["id"]
        draft["address"] = {"formatted_address": u.location["name"], "latitude": u.location["lat"], "longitude": u.location["lon"],
                            "source": lr.provider if lr else "preset", "confirmed": False}
        src = f" (found via {lr.provider})" if lr and lr.provider in ("onemap", "nominatim") else ""
        acks.append(f"Address: {u.location['name']}{src}." + (f" {lr.note}" if lr and lr.note else ""))
    elif lr and lr.candidates:
        reply["options"] = [{"type": "geocode", "label": c["address"], "lat": c["lat"], "lon": c["lon"], "name": c["address"], "source": c["source"]} for c in lr.candidates]
        acks.append(f"I found several matches for “{u.interpreted.location_hint}” — which one is it?")
    elif lr and u.interpreted.location_hint and not u.location:
        acks.append(f"I could not locate “{u.interpreted.location_hint}”" + (f" ({lr.note})" if lr.note else "") + "; please use the address search or drop a pin.")
    if u.window:
        draft["window_start"], draft["window_end"] = u.window[0].isoformat() + "Z", u.window[1].isoformat() + "Z"
        acks.append(f"Requested window: {hhmm(u.window[0])}–{hhmm(u.window[1])} (I'll check it is feasible).")
    elif u.time_note == "past":
        acks.append(f"“{u.interpreted.time_hint}” has already passed — it is {hhmm(clock.now)} now on the service day; please choose a later window.")
        draft.pop("window_start", None)
        draft.pop("window_end", None)
    elif u.time_note == "unparsed":
        acks.append(f"I could not turn “{u.interpreted.time_hint}” into a time window; please pick one below.")
    if not draft.get("catalog_item_id"):
        opts = [{"type": "catalog", "id": it.id, "label": f"{it.trade_type} – {it.problem_name}"} for it in
                [catalog_service.get_item(db, cid) for cid in u.interpreted.alternatives] if it]
        if not opts:
            opts = [{"type": "catalog", "id": c.id, "label": f"{c.trade_type} – {c.problem_name}"} for c in catalog_service.search(db, text, 4)]
        reply["options"] = opts  # problem suggestions contain catalog problems only
        question = u.interpreted.clarifying_question or ("Which of these matches your problem?" if opts else _ask_for(["problem"], lang))
        reply["text"] = " ".join(acks + [question])
    elif reply.get("options") and any(o.get("type") == "geocode" for o in reply["options"]):
        reply["text"] = " ".join(acks)
    else:
        reply["text"] = " ".join(acks)
        if draft.get("window_start") and draft.get("location_id") and draft.get("catalog_item_id") and (draft.get("address") or {}).get("confirmed"):
            _validate_requested_window(db, clock, draft, reply)
        finish_collect()


def _validate_requested_window(db: Session, clock: Clock, draft: dict[str, Any], reply: dict[str, Any]) -> None:
    """A window the customer typed is only kept if a zero-disturbance trial succeeds; otherwise feasible ones are offered."""
    opts = _window_options(db, clock, draft)
    ok = any(o["window_start"] == iso(parse_iso(draft["window_start"])) for o in opts)
    if not ok:
        # also accept if the requested window is exactly feasible even when not in the top 4
        pen = customer_service.preference_penalties(db, draft.get("customer_id"))
        res = negotiation_service.propose_windows(db, clock, catalog_item_id=draft["catalog_item_id"], location_id=draft["location_id"],
                                                  customer_id=draft.get("customer_id"), excluded_technicians=draft.get("excluded_technician_ids"),
                                                  after=parse_iso(draft["window_start"]), count=1, paid=bool(draft.get("paid_expedite")), penalties=pen)
        w = res["data"]["windows"][:1]
        ok = bool(w) and w[0]["window_start"] == iso(parse_iso(draft["window_start"]))
    if not ok:
        reply["text"] += " That exact window is not feasible right now; here are windows that are."
        draft.pop("window_start", None)
        draft.pop("window_end", None)


def _wants_human(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in ["human", "real person", "agent please", "talk to someone", "customer service", "人工", "转人工", "真人", "客服"])


EXPEDITE_WORDS = ["expedite", "hurry", "faster", "sooner", "speed up", "speed it up", "quicker", "earlier", "as soon as possible", "asap",
                  "加急", "快一点", "快点", "提前", "尽快"]


def _wants_expedite(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in EXPEDITE_WORDS)


def _expedite_offer(db: Session, clock: Clock, orders: list[dict[str, Any]], reply: dict[str, Any]) -> None:
    """Explain what expediting an existing order does (read-only trial), then let the customer decide."""
    open_orders = [o for o in orders if o["lifecycle_status"] == "OPEN" and not o["paid_expedite"]]
    if not open_orders:
        if any(o["lifecycle_status"] == "OPEN" for o in orders):
            reply["text"] = expedite_service.ALREADY_MESSAGE + " Your order already has P1 priority."
        else:
            reply["text"] = expedite_service.DEPARTED_MESSAGE
        return
    parts: list[str] = []
    options: list[dict[str, Any]] = []
    for o in open_orders[:3]:
        order = db.get(WorkOrder, o["order_id"])
        if order is None:
            continue
        pv = expedite_service.preview(db, clock, order)
        current = f"currently planned {hhmm(parse_iso(pv['current_start']))}" if pv.get("current_start") else "no technician confirmed yet"
        if pv.get("earliest_start"):
            n = int(pv.get("affected_count") or 0)
            effect = (f"earliest possible start {hhmm(parse_iso(pv['earliest_start']))} (moves {n} other appointment{'s' if n != 1 else ''}"
                      + (", dispatcher confirmation needed" if pv.get("decision") != "auto" else "") + ")")
        else:
            effect = "no earlier slot is available today, so the time would stay the same"
        parts.append(f"Expediting {o['order_id']} ({o['problem']}) records a simulated payment and raises it to P1 so it cannot be displaced "
                     f"by regular orders — {current}; {effect}.")
        options.append({"type": "expedite_order", "order_id": o["order_id"],
                        "label": "Expedite now (simulated payment)" + (f" · {o['order_id']}" if len(open_orders) > 1 else "")})
    options.append({"type": "keep_order", "label": "Keep as is"})
    reply["text"] = " ".join(parts)
    reply["options"] = options


SAFETY_TEMPLATES = {
    "gas_leak": "This sounds like a possible gas leak. Do not switch lights or appliances on or off, do not use open flames, open windows and leave the area.",
    "fire": "This sounds like a fire risk. Leave the area, do not try to fight a spreading fire, and alert people nearby.",
    "electric_shock": "This sounds like an electrical hazard. Do not touch the appliance or anyone in contact with it; switch off the mains only if it is safe to reach.",
    "flooding": "This sounds like an active water emergency. If it is safe, close the main water valve and keep away from electrical outlets.",
    "injury": "If someone is injured, medical help comes first.",
    "other": "This may be an emergency.",
}


def _known_slots(db: Session, draft: dict[str, Any]) -> dict[str, Any]:
    known: dict[str, Any] = {}
    if draft.get("catalog_item_id"):
        item = catalog_service.get_item(db, draft["catalog_item_id"])
        if item:
            known["problem"] = f"{item.trade_type} – {item.problem_name}"
    if (draft.get("address") or {}).get("formatted_address"):
        known["address"] = draft["address"]["formatted_address"]
    if draft.get("window_start"):
        known["appointment_window"] = f"{hhmm(parse_iso(draft['window_start']))}–{hhmm(parse_iso(draft['window_end']))}"
    if draft.get("customer_name"):
        known["customer_name"] = draft["customer_name"]
    if draft.get("contact_phone"):
        known["contact_phone"] = draft["contact_phone"]
    return known


def _response(db: Session, clock: Clock, s: ChatSession, reply: dict[str, Any], llm_info: dict[str, Any]) -> dict[str, Any]:
    db.flush()
    customer_id = (s.draft or {}).get("customer_id")
    case = human_service.open_case_for_session(db, s.id, customer_id)
    questions = [{"id": q.id, "kind": q.kind, "question": q.question, "options": q.options} for q in db.scalars(
        select(CustomerQuestion).where(CustomerQuestion.session_id == s.id, CustomerQuestion.status == "open")).all()]
    return {"session_id": s.id, "state": s.state, "draft": _draft_view(db, s.draft or {}), "reply": reply,
            "messages": (s.messages or [])[-30:], "orders": session_orders(db, clock, s.id, customer_id),
            "llm": llm_info or dict(get_llm().last), "sim_now": iso(clock.now), "mode": "simulated",
            "human_case": human_service.case_view(case) if case else None, "open_questions": questions,
            "customer": _customer_view(db, customer_id)}


def _customer_view(db: Session, customer_id: str | None) -> dict[str, Any] | None:
    if not customer_id:
        return None
    c = db.get(Customer, customer_id)
    if c is None:
        return None
    return {"id": c.id, "name": c.name, "phone": c.phone, "demo_login": c.demo_login,
            "addresses": customer_service.list_addresses(db, c.id)}


def slot_options(clock: Clock) -> list[dict[str, Any]]:  # legacy helper kept for the session endpoint
    return []


def pre_model_work(db: Session, session_id: str, message: str, action: str | None, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Read-only preparation for `handle`, meant to run WITHOUT the state lock: the model interpretation of a free-text
    message (catalog + address lookup included) or the complaint classification. Returns {} when no model call is needed."""
    text = (message or "").strip()
    clock = get_clock(db)
    if action == "complaint":
        t = str((payload or {}).get("text") or text)
        if not t:
            return {}
        cls = get_llm().classify_complaint(t)
        return {"classification": cls, "llm": dict(get_llm().last)}
    if action or not text:
        return {}
    s = db.get(ChatSession, session_id)
    draft = dict(s.draft or {}) if s is not None and s.scenario_generation == clock.scenario_generation else {}
    if human_service.open_case_for_session(db, session_id, draft.get("customer_id")) is not None:
        return {}  # human takeover: the message is only attached to the case, no interpretation
    return {"understanding": understand(db, clock, text, known=_known_slots(db, draft))}
