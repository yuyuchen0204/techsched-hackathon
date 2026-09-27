"""V3 (§18): execution facts vs plan, shadow prediction, idempotency, breaks vs schedule versions, agent branches,
tool authority, safety detection, human takeover, history scoping. Everything runs on the seeded main scenario."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.agents import runtime
from app.agents.policies import MockPolicy
from app.agents.runtime import Action
from app.agents.tools import TOOLS, ToolContext, ToolResult, invoke
from app.models.entities import (
    AgentTask,
    Assignment,
    BreakBlock,
    DurationObservation,
    HumanCase,
    Notification,
    RescheduleRequest,
    SafetyIncident,
    Technician,
    WorkOrder,
)
from app.models.enums import AssignmentStatus
from app.services import (
    break_service,
    chat_service,
    customer_service,
    duration_service,
    event_service,
    execution_service,
    human_service,
    negotiation_service,
    position_service,
    safety_service,
    technician_service,
)
from app.services.catalog_importer import make_catalog_item_id
from app.services.clock import get_clock, set_now
from app.services.order_service import OrderError, create_order
from app.services.snapshot import active_schedule_version, build_snapshot
from app.services.timeutil import local_time_utc

DAY = date(2026, 9, 15)


def t(h, m=0):
    return local_time_utc(DAY, h, m)


def order(db, *, trade, problem, loc="loc_hougang", ws=(11, 0), we=(12, 30), ref="sess_v3", customer_id=None, address=None):
    clock = get_clock(db)
    return create_order(db, clock, customer_ref=ref, customer_name="V3", contact_phone="+65 9", description="", location_id=loc,
                        catalog_item_id=make_catalog_item_id(trade, problem), window_start=t(*ws), window_end=t(*we), paid_expedite=False,
                        customer_id=customer_id, address=address)


def customer(db, login):
    from app.models.entities import Customer
    return db.scalars(select(Customer).where(Customer.demo_login == login)).first()


# ---------------------------------------------------------------- §18.1 execution facts
def test_actual_service_time_is_start_to_complete_not_departure(db_session):
    """Travel and early waiting never count as service time; the observation is quality-flagged, never guessed."""
    db = db_session
    tech = db.get(Technician, "tech_02")
    technician_service.set_sim_mode(db, tech.id, "manual")
    oid = "wo_003"  # Bala's first planned job (service 09:00)
    planned_departure = db.scalars(select(Assignment).where(Assignment.order_id == oid,
                                                            Assignment.status == AssignmentStatus.ACTIVE)).one().departure
    set_now(db, planned_departure)   # leaving before this is refused (policy execution.depart_grace_minutes)
    technician_service.action(db, get_clock(db), tech.id, oid, "depart")
    set_now(db, t(8, 50))
    technician_service.action(db, get_clock(db), tech.id, oid, "arrive")
    set_now(db, t(9, 5))
    technician_service.action(db, get_clock(db), tech.id, oid, "start")
    set_now(db, t(9, 47))
    technician_service.action(db, get_clock(db), tech.id, oid, "complete")
    obs = db.scalars(select(DurationObservation).where(DurationObservation.order_id == oid)).one()
    assert obs.actual_minutes == 42 and obs.quality == "ok"      # 09:05 → 09:47, not 08:40 → 09:47 (67)
    o = db.get(WorkOrder, oid)
    assert o.lifecycle_status == "COMPLETED" and o.report_status == "pending"


def test_technician_cannot_depart_before_the_planned_time(db_session):
    """An early departure overwrites the plan with the actual time and locks the task, so it is refused outright
    (policy execution.depart_grace_minutes, default 0). The simulator is unaffected: it only fires a due departure."""
    db = db_session
    technician_service.set_sim_mode(db, "tech_02", "manual")
    a = db.scalars(select(Assignment).where(Assignment.order_id == "wo_003",
                                            Assignment.status == AssignmentStatus.ACTIVE)).one()
    set_now(db, a.departure - timedelta(minutes=1))
    with pytest.raises(OrderError) as exc:
        technician_service.action(db, get_clock(db), "tech_02", "wo_003", "depart")
    assert exc.value.code == "too_early_to_depart" and exc.value.details["minutes_early"] == 1 and exc.value.kind == "INVALID_STATE"
    assert db.get(WorkOrder, "wo_003").lifecycle_status == "OPEN"   # nothing was locked
    set_now(db, a.departure)
    assert technician_service.action(db, get_clock(db), "tech_02", "wo_003", "depart")["lifecycle_status"] == "EN_ROUTE"


def test_simulator_cannot_drive_a_manual_technician(db_session):
    db = db_session
    technician_service.set_sim_mode(db, "tech_02", "manual")
    with pytest.raises(OrderError) as exc:
        execution_service.execution_event(db, "wo_003", "depart", actor="simulation")
    assert exc.value.kind in ("FORBIDDEN", "INVALID_STATE")
    with pytest.raises(OrderError) as exc2:  # and the app cannot drive an auto technician (single driver)
        technician_service.action(db, get_clock(db), "tech_01", "wo_004", "depart")
    assert exc2.value.kind == "INVALID_STATE"


def test_departed_order_is_locked_and_cancel_then_depart_is_rejected(db_session):
    db = db_session
    set_now(db, t(8, 40))
    execution_service.execution_event(db, "wo_003", "depart", actor="simulation")
    with pytest.raises(OrderError) as exc:
        event_service.customer_cancel(db, "wo_003", customer_ref=None, actor="customer_app", reason="changed my mind")
    assert exc.value.code == "already_departed"
    # the other order of the race: cancelled first → a late 'depart' fact cannot revive it
    event_service.customer_cancel(db, "wo_004", customer_ref=None, actor="customer_app", reason="no longer needed")
    with pytest.raises(OrderError):
        execution_service.execution_event(db, "wo_004", "depart", actor="simulation")
    assert db.get(WorkOrder, "wo_004").lifecycle_status == "CANCELLED"


# ---------------------------------------------------------------- §18.1 shadow prediction
def test_shadow_prediction_never_changes_the_schedule(db_session):
    db = db_session
    clock = get_clock(db)
    v0 = active_schedule_version(db)
    item = make_catalog_item_id("Refrigerator", "Not cooling")
    minutes, label = duration_service.predict(db, clock, item, 45, before=clock.now)
    assert label != "csv_baseline" and minutes != 45  # 6 synthetic samples → median used in shadow
    mv = duration_service.build_model_version(db, clock)
    assert mv.metrics["mode"] == "shadow" and mv.metrics["mae_csv_baseline"] is not None
    snap = build_snapshot(db, clock)
    assert active_schedule_version(db) == v0
    assert all(o.duration == db.get(WorkOrder, o.id).catalog_snapshot["repair_duration_minutes"] for o in snap.orders.values())


def test_prediction_falls_back_below_min_samples(db_session):
    db = db_session
    clock = get_clock(db)
    item = make_catalog_item_id("Electrical & Lighting", "Outlet no power")  # one sample only
    minutes, label = duration_service.predict(db, clock, item, 30, before=clock.now)
    assert minutes == 30 and label.startswith("csv_baseline")


# ---------------------------------------------------------------- §18.1 idempotency
def test_leave_complaint_and_break_are_idempotent(db_session):
    db = db_session
    clock = get_clock(db)
    a = technician_service.leave(db, clock, "tech_05", start=clock.now, end=t(18, 0), reason="unwell", idempotency_key="leave-1")
    b = technician_service.leave(db, clock, "tech_05", start=clock.now, end=t(18, 0), reason="unwell", idempotency_key="leave-1")
    assert len(a["affected"]) > 0 and len(b["affected"]) == len(a["affected"])  # second call replays the first result
    tech = db.get(Technician, "tech_05")
    assert len(tech.unavailable_intervals) == 1
    c1 = event_service.complaint(db, "wo_006", complaint_type="attitude", text="rude", customer_ref=None, idempotency_key="cmp-1")
    c2 = event_service.complaint(db, "wo_006", complaint_type="attitude", text="rude", customer_ref=None, idempotency_key="cmp-1")
    assert c2.get("idempotent") and c1["order_id"] == c2["order_id"]
    r1 = break_service.submit_break(db, clock, "tech_08", t(12, 0), 30, expected_version=None, created_by="test", idempotency_key="brk-1")
    r2 = break_service.submit_break(db, clock, "tech_08", t(12, 0), 30, expected_version=None, created_by="test", idempotency_key="brk-1")
    assert r2.get("idempotent") and r1["data"]["id"] == r2["data"]["id"]
    assert len(db.scalars(select(BreakBlock).where(BreakBlock.technician_id == "tech_08")).all()) == 1


# ---------------------------------------------------------------- §18.1 breaks vs schedule versions
def test_break_submit_rejects_stale_version_and_disturbance(db_session):
    db = db_session
    clock = get_clock(db)
    v = active_schedule_version(db)
    with pytest.raises(OrderError) as exc:
        break_service.submit_break(db, clock, "tech_08", t(12, 0), 30, expected_version=v - 1, created_by="agent", idempotency_key=None)
    assert exc.value.kind == "VERSION_CONFLICT"
    # a rest overlapping Hana's 10:00 job cannot be zero-disturbance
    with pytest.raises(OrderError) as exc2:
        break_service.submit_break(db, clock, "tech_08", t(10, 0), 30, expected_version=v, created_by="agent", idempotency_key=None)
    assert exc2.value.kind == "POLICY_VIOLATION"
    ok = break_service.submit_break(db, clock, "tech_08", t(12, 30), 30, expected_version=v, created_by="agent", idempotency_key=None)
    assert ok["status"] == "ok" and active_schedule_version(db) == v + 1
    snap = build_snapshot(db, clock)
    assert (clock.to_minutes(t(12, 30)), clock.to_minutes(t(13, 0))) in snap.techs["tech_08"].breaks


def test_new_order_after_break_respects_the_rest_block(db_session):
    db = db_session
    clock = get_clock(db)
    break_service.submit_break(db, clock, "tech_08", t(12, 30), 30, expected_version=None, created_by="agent", idempotency_key=None)
    from app.orchestration.orchestrator import dispatch_order
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(12, 30), we=(14, 0))
    out = dispatch_order(db, o.id, trigger="test")
    snap = build_snapshot(db, get_clock(db))
    a = snap.assignments.get(o.id)
    if a and a.tech_id == "tech_08":
        assert not (a.service_start < clock.to_minutes(t(13, 0)) and a.service_end > clock.to_minutes(t(12, 30)))
    assert out.decision in ("auto", "manual", "unresolved")


def test_break_scan_creates_one_task_per_stretch(db_session):
    db = db_session
    set_now(db, t(16, 0))  # by now several technicians have > 180 planned work minutes and no recorded rest
    a = execution_service.break_scan(db, get_clock(db))
    b = execution_service.break_scan(db, get_clock(db))
    tasks = db.scalars(select(AgentTask).where(AgentTask.role == "break")).all()
    assert len(tasks) >= 1 and len(tasks) == len({x.dedupe_key for x in tasks})
    assert len(b) == 0 or set(b).isdisjoint(set(a))


# ---------------------------------------------------------------- §18.3 agent branches
def test_agent_success_path_stops_after_submit(db_session):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(14, 0), we=(15, 30))
    task = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, task)
    assert task.status == "succeeded"
    view = runtime.task_view(db, task)
    tools = [x["tool"] for x in view["traces"] if x["phase"] in ("returned", "submitted")]
    assert tools.count("submit_plan") == 1 and "submitted" in {x["phase"] for x in view["traces"]}
    assert db.get(WorkOrder, o.id).scheduling_status == "ASSIGNED"


def test_agent_changes_search_after_zero_disturbance_fails(db_session, monkeypatch):
    """P1 (authority: ≤2 P3 moves): when the zero-disturbance trial finds nothing, the policy moves to bounded repair
    instead of retrying the same search."""
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(11, 0), we=(12, 30))
    from app.services.order_service import set_paid_expedite
    set_paid_expedite(db, o)
    spec = TOOLS["simulate_insertion"]
    monkeypatch.setattr(spec, "handler", lambda ctx, a: ToolResult("no_solution", {"candidates": []}, ["NO_ZERO_DISTURBANCE_SLOT"], snapshot_version=active_schedule_version(db)))
    task = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, task)
    seq = [x["tool"] for x in runtime.task_view(db, task)["traces"] if x["phase"] == "planned"]
    assert seq.count("simulate_insertion") == 1 and "search_local_repair" in seq
    assert seq.index("search_local_repair") > seq.index("simulate_insertion")
    assert task.status in ("succeeded", "waiting_human", "no_solution")


def test_agent_budget_exhaustion_escalates_to_human(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(14, 0), we=(15, 30))
    monkeypatch.setattr(MockPolicy, "decide", lambda self, task, history, ctx: Action("call_tool", "get_order_context", {}, summary="loop"))
    task = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, task, max_calls=4)
    assert task.status == "waiting_human" and task.tool_budget_used == 4
    case = db.get(HumanCase, task.human_case_id)
    assert case is not None and case.source == "AGENT_ESCALATION" and case.category == "agent_budget_exhausted"
    assert "get_order_context" in case.attempted_actions


def test_agent_no_qualified_technician_goes_to_human_proactively(db_session):
    db = db_session
    clock = get_clock(db)
    for tech in db.scalars(select(Technician)).all():  # nobody can do level-5 gas work
        tech.skills = {k: min(v, 2) for k, v in tech.skills.items()}
    from app.services.snapshot import _matrix_cache
    _matrix_cache.clear()
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(14, 0), we=(15, 30))
    task = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, task)
    assert task.status == "waiting_human"
    case = db.get(HumanCase, task.human_case_id)
    assert case.category in ("no_qualified_technician", "no_solution") and case.unresolved_questions is not None


def test_human_resolution_wakes_waiting_task(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(14, 0), we=(15, 30))
    monkeypatch.setattr(MockPolicy, "decide", lambda self, task, history, ctx: Action("call_tool", "get_order_context", {}, summary="loop"))
    task = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, task, max_calls=3)
    assert task.status == "waiting_human"
    monkeypatch.undo()
    human_service.resolve(db, clock, task.human_case_id, resolution="added capacity", author="dispatcher")
    assert task.status == "pending" and task.wakeup_reason
    runtime.run_pending(db)
    assert task.status in ("succeeded", "waiting_human", "no_solution")


# ---------------------------------------------------------------- §18.3 tool authority
def test_tool_role_and_arg_checks_reject_authority_widening(db_session):
    db = db_session
    clock = get_clock(db)
    cust = ToolContext(db=db, clock=clock, role="customer")
    res, _ = invoke(cust, "submit_plan", {"plan_id": "x"})
    assert res.status == "forbidden" and "ROLE_NOT_ALLOWED" in res.reason_codes
    sched = ToolContext(db=db, clock=clock, role="scheduling", order_id="wo_006")
    res, _ = invoke(sched, "flag_for_human", {"category": "x", "urgency": "critical", "reason_summary": "r", "force_commit": True})
    assert res.status == "data_incomplete" and "INVALID_ARGS" in res.reason_codes and any(e.startswith("unknown:") for e in res.data["errors"])
    res, _ = invoke(sched, "submit_plan", {"plan_id": "does-not-exist"})
    assert res.status == "error" and "NOT_FOUND" in res.reason_codes
    res, _ = invoke(sched, "not_a_tool", {})
    assert "UNKNOWN_TOOL" in res.reason_codes


def test_submit_plan_beyond_authority_becomes_review_not_commit(db_session, monkeypatch):
    """The model only names a plan id; the PolicyEngine decides. A plan whose re-validation says 'manual' is queued."""
    db = db_session
    clock = get_clock(db)
    from app.services import plan_service
    o = order(db, trade="Refrigerator", problem="Not cooling", loc="loc_hougang", ws=(14, 0), we=(15, 30))
    ctx = ToolContext(db=db, clock=clock, role="scheduling", order_id=o.id)
    sim, _ = invoke(ctx, "simulate_insertion", {})
    assert sim.status == "ok" and sim.data["candidates"]
    pid = sim.data["candidates"][0]["plan_id"]
    original = plan_service.revalidate

    def forced_manual(db_, clock_, plan):
        snap, cand, outcome, conflict = original(db_, clock_, plan)
        return snap, cand, {**outcome, "decision": "manual", "reasons": ["test: forced review"]}, conflict
    monkeypatch.setattr(plan_service, "revalidate", forced_manual)
    res, _ = invoke(ctx, "submit_plan", {"plan_id": pid})
    assert res.status == "ok" and res.data["submitted"] == "pending_review"
    assert db.get(WorkOrder, o.id).scheduling_status == "PENDING_REVIEW"


# ---------------------------------------------------------------- §11 safety, §9 human takeover
def test_reschedule_can_be_previewed_approved_and_declined(db_session):
    """V3.1: the queue can now actually answer a reschedule request. Approving is guarded by a trial insertion,
    because it releases the confirmed appointment before re-dispatching."""
    db = db_session
    clock = get_clock(db)
    order = db.get(WorkOrder, "wo_009")
    assert order.lifecycle_status == "OPEN"
    ws = order.window_start + timedelta(minutes=120)
    r = negotiation_service.request_reschedule(db, clock, order, window_start=ws, window_end=ws + timedelta(minutes=90),
                                               reason="visiting my mother", customer_id=None, session_id=None)
    rid, case_id = r["request_id"], r["human_case_id"]
    assert r["status"] == "pending"

    prev = negotiation_service.preview_reschedule(db, clock, rid)     # read-only: nothing moved yet
    assert prev["request_id"] == rid and prev["requested_window"] != prev["current_window"]
    assert db.get(WorkOrder, "wo_009").window_start == order.window_start
    if prev["feasible"]:
        out = negotiation_service.approve_reschedule(db, clock, rid, author="dispatcher")
        moved = db.get(WorkOrder, "wo_009")
        assert out["status"] == "approved" and moved.window_start == ws
        assert db.get(HumanCase, case_id).status == "resolved"        # the case closes with its request
    else:
        with pytest.raises(OrderError) as exc:                        # an impossible window never costs the real slot
            negotiation_service.approve_reschedule(db, clock, rid, author="dispatcher")
        assert exc.value.code == "reschedule_infeasible"
        assert db.get(WorkOrder, "wo_009").window_start == order.window_start


def test_declining_or_closing_a_reschedule_case_never_leaves_it_pending(db_session):
    """Closing the case used to leave the request `pending` for ever and the customer without an answer."""
    db = db_session
    clock = get_clock(db)
    for oid, close_via in (("wo_011", "decline"), ("wo_012", "resolve")):
        order = db.get(WorkOrder, oid)
        ws = order.window_start + timedelta(minutes=60)
        r = negotiation_service.request_reschedule(db, clock, order, window_start=ws, window_end=ws + timedelta(minutes=90),
                                                   reason="", customer_id=None, session_id=None)
        if close_via == "decline":
            out = negotiation_service.decline_reschedule(db, clock, r["request_id"], author="dispatcher", reason="fully booked")
            assert out["status"] == "rejected"
        else:
            human_service.resolve(db, clock, r["human_case_id"], resolution="called the customer", author="dispatcher")
        req = db.get(RescheduleRequest, r["request_id"])
        assert req.status == "rejected", f"{oid}: closing via {close_via} left the request {req.status}"
        assert db.get(WorkOrder, oid).window_start == order.window_start   # the original appointment stands


def test_two_requests_on_one_order_never_send_contradictory_answers(db_session):
    """Both requests land on one merged case, so deciding one settles the other — but the customer must not be told
    "moved to 11:00" and "we kept your original time" in the same breath."""
    db = db_session
    clock = get_clock(db)
    order = db.get(WorkOrder, "wo_010")
    first = negotiation_service.request_reschedule(db, clock, order, window_start=order.window_start + timedelta(minutes=60),
                                                   window_end=order.window_start + timedelta(minutes=150), reason="a",
                                                   customer_id=None, session_id=None)
    second = negotiation_service.request_reschedule(db, clock, order, window_start=order.window_start + timedelta(minutes=120),
                                                    window_end=order.window_start + timedelta(minutes=210), reason="b",
                                                    customer_id=None, session_id=None)
    assert first["human_case_id"] == second["human_case_id"]      # one case, two requests
    if not negotiation_service.preview_reschedule(db, clock, second["request_id"])["feasible"]:
        pytest.skip("the second window is not servable in this seeded day")
    out = negotiation_service.approve_reschedule(db, clock, second["request_id"], author="dispatcher")
    assert out["superseded_request_ids"] == [first["request_id"]]
    assert db.get(RescheduleRequest, first["request_id"]).status == "superseded"
    msgs = [n.message for n in db.scalars(select(Notification).where(Notification.order_id == "wo_010")).all()]
    assert any("was moved to" in m for m in msgs)
    assert not any("kept your original appointment" in m for m in msgs), msgs


def test_position_reports_the_leg_being_driven_not_the_next_queued_job(db_session):
    """A technician en route to A with B queued was reported as "en route to B": the queued job won the branch.
    Anything that trusted it — the map tooltip, and the trimming of the drawn route — pointed at the wrong leg."""
    db = db_session
    rows = db.scalars(select(Assignment).where(Assignment.status == AssignmentStatus.ACTIVE)).all()
    by_tech: dict[str, list[Assignment]] = {}
    for r in rows:
        by_tech.setdefault(r.technician_id, []).append(r)
    pick = None
    for tid, items in by_tech.items():
        items.sort(key=lambda r: r.departure)
        first = items[0]
        if len(items) > 1 and first.travel_minutes >= 4 and db.get(WorkOrder, first.order_id).lifecycle_status == "OPEN":
            pick = (tid, first)
            break
    if pick is None:
        pytest.skip("no technician with a real drive followed by another job in this seeded day")
    tid, a = pick
    technician_service.set_sim_mode(db, tid, "manual")
    set_now(db, a.departure)
    technician_service.action(db, get_clock(db), tid, a.order_id, "depart")
    set_now(db, a.departure + timedelta(minutes=a.travel_minutes // 2))          # halfway down the leg
    p = next(x for x in position_service.positions(db, get_clock(db)) if x["technician_id"] == tid)
    assert p["moving"] and p["current_leg"] is not None, f"{tid} should be mid-drive"
    assert p["current_leg"]["order_id"] == a.order_id, "the leg being driven must be the departed one"
    assert p["next_destination"]["order_id"] == a.order_id, "the tooltip must name where the technician is actually going"
    assert p["current_leg"]["from_location_id"] == a.origin_location_id
    assert 0 < (p["current_leg"]["progress"] or 0) < 1


def test_a_new_customer_is_always_asked_for_a_name_and_phone(db_session):
    """A brand-new customer's stored `name` is their demo login (new_a1b2). Pre-filling the draft with it satisfied
    the contact slot, so the order was submitted without anyone ever asking who the customer is."""
    db, s = db_session, "sess_newcust"
    r = chat_service.handle(db, s, "", "identify", {"demo_login": "new_zz99"})
    assert r["draft"].get("customer_name") is None, "a login handle is not a name"
    assert r["draft"].get("contact_phone") is None
    chat_service.handle(db, s, "", "select_catalog", FRIDGE)
    chat_service.handle(db, s, "", "set_address", HOUGANG_MALL)
    r = chat_service.handle(db, s, "", "set_expedite_now", {"now": False})
    free = next(o for o in r["reply"]["options"] if o["type"] == "window" and o["state"] == "available")
    r = chat_service.handle(db, s, "", "set_window", {"window_start": free["window_start"], "window_end": free["window_end"],
                                                      "paid_required": False})
    assert any(o["type"] == "contact_form" for o in r["reply"]["options"]), "the contact step must not be skipped"
    assert r["state"] == "collecting"
    # a returning customer with real details still keeps them
    r2 = chat_service.handle(db, "sess_known", "", "identify", {"demo_login": "carol"})
    assert r2["draft"].get("customer_name") and r2["draft"].get("contact_phone")


def test_a_returning_customers_orders_survive_the_polling_refresh(db_session, client):
    """The chat page re-reads the session every few seconds. That endpoint dropped the identified customer, so a
    returning customer's earlier orders appeared on sign-in and vanished on the next tick."""
    db, sid = db_session, "sess_poll"
    signed_in = chat_service.handle(db, sid, "", "identify", {"demo_login": "carol"})
    assert len(signed_in["orders"]) > 0, "carol is seeded with earlier orders"
    polled = client.get(f"/api/chat/sessions/{sid}").json()
    assert len(polled["orders"]) == len(signed_in["orders"])


def test_safety_detection_with_negation_and_past_guards():
    assert safety_service.detect("I can smell gas in the kitchen")["danger_type"] == "gas_leak"
    d = safety_service.detect("there is no gas smell, just the stove won't light")
    assert d["guarded"] or d["danger_type"] is None
    d2 = safety_service.detect("last month there was a small fire but it's fixed now")
    assert d2["guarded"] or d2["danger_type"] is None
    assert safety_service.detect("sparks and burning smell from the socket")["danger_type"] in ("electric_shock", "fire")


def test_safety_incident_escalates_to_a_human_and_never_offers_to_call_anyone(db_session):
    """V3.1: the emergency-services panel was removed. A repair app must not present itself as a route to 995/999,
    so the only thing it offers is a person — while still detecting the danger and raising a critical case."""
    db = db_session
    r = chat_service.handle(db, "sess_safe", "I smell gas near the water heater", None, None)
    inc = r["reply"].get("incident")
    assert inc and inc["danger_type"] == "gas_leak"
    case = db.scalars(select(HumanCase).where(HumanCase.incident_id == inc["id"])).first()
    assert case is not None and case.source == "AGENT_ESCALATION" and case.urgency == "critical"
    # the customer gets safety advice and a human — no numbers, no dial links, no "did you call?" bookkeeping
    assert [o["type"] for o in r["reply"]["options"]] == ["handoff"]
    assert "channels" not in inc and "contact_events" not in inc
    visible = " ".join([r["reply"]["text"], *(str(o.get("label") or "") for o in r["reply"]["options"])])
    assert not any(n in visible for n in ("995", "999", "1800")) and "emergency service" not in visible.lower()
    assert "do not switch lights" in r["reply"]["text"].lower()   # the advice itself is kept
    # the action is gone, not merely hidden: it can no longer write "the customer says they called" onto an incident
    chat_service.handle(db, "sess_safe", "", "emergency_contact", {"incident_id": inc["id"], "kind": "user_confirmed_contacted"})
    assert db.get(SafetyIncident, inc["id"]).contact_events == []


def test_human_takeover_pauses_agent_and_reply_reaches_customer(db_session):
    db = db_session
    clock = get_clock(db)
    r = chat_service.handle(db, "sess_h", "", "handoff", {"reason": "I want a person"})
    case_id = r["reply"]["human_case"]["id"]
    r2 = chat_service.handle(db, "sess_h", "my fridge is dead, come at 3pm", None, None)
    assert r2["human_case"]["id"] == case_id and r2["draft"].get("catalog_item_id") is None  # nothing collected while paused
    human_service.take(db, clock, case_id, "dispatcher")
    human_service.reply(db, clock, case_id, text="We can do 15:00 with Chen Wei", author="dispatcher")
    r3 = chat_service.handle(db, "sess_h", "", "status", None)
    assert r3["human_case"]["replies"][-1]["text"].startswith("We can do")
    human_service.resolve(db, clock, case_id, resolution="booked manually", author="dispatcher")
    r4 = chat_service.handle(db, "sess_h", "my fridge is not cooling", None, None)
    assert r4["human_case"] is None and r4["draft"].get("catalog_item_id")  # assistant resumes


def test_flag_for_human_is_idempotent_and_escalates(db_session):
    db = db_session
    clock = get_clock(db)
    c1, created1 = human_service.flag_for_human(db, clock, source="POLICY_REQUIRED", category="x", urgency="normal", reason_summary="a", order_id="wo_006", idempotency_key="k1")
    c2, created2 = human_service.flag_for_human(db, clock, source="POLICY_REQUIRED", category="x", urgency="high", reason_summary="b", order_id="wo_006", idempotency_key="k1")
    assert created1 and not created2 and c1.id == c2.id  # exact replay: no change
    c3, created3 = human_service.flag_for_human(db, clock, source="AGENT_ESCALATION", category="x", urgency="high", reason_summary="worse", order_id="wo_006")
    assert not created3 and c3.id == c1.id and c3.urgency == "high" and c3.escalations == 1 and "worse" in c3.reason_summary


# ---------------------------------------------------------------- §8 history & preferences
def test_history_hint_ignores_shared_preset_areas_but_matches_customer(db_session):
    db = db_session
    clock = get_clock(db)
    assert customer_service.repeat_fault_hint(db, clock, None, "loc_bedok", "Air Conditioning") is None
    alice = customer(db, "alice")
    hint = customer_service.repeat_fault_hint(db, clock, alice.id, None, "Air Conditioning")
    assert hint and hint["count"] >= 1 and "similar" in hint["wording"] and "same unit" in hint["wording"]


def test_low_technician_rating_is_a_soft_preference_not_an_exclusion(db_session):
    db = db_session
    alice = customer(db, "alice")
    pen = customer_service.preference_penalties(db, alice.id)
    assert "tech_02" in pen and 0 < pen["tech_02"] < 1
    addr = customer_service.list_addresses(db, alice.id)[0]
    o = order(db, trade="Air Conditioning", problem="No cooling or heating", loc=addr["location_id"], ws=(14, 0), we=(15, 30), customer_id=alice.id,
              address={k: addr.get(k) for k in ("formatted_address", "unit_number", "latitude", "longitude")})
    snap = build_snapshot(db, get_clock(db))
    assert snap.preference_penalties.get(o.id, {}).get("tech_02")
    from app.scheduling.solver import qualified_techs
    assert "tech_02" in [x.id for x in qualified_techs(snap, snap.orders[o.id])]  # still allowed; only ranked lower


def test_customer_history_is_scoped_by_role(db_session):
    db = db_session
    clock = get_clock(db)
    alice = customer(db, "alice")
    own = customer_service.customer_history(db, clock, alice.id, role="customer")
    tech = customer_service.customer_history(db, clock, alice.id, role="technician")
    assert own["orders"] and own["addresses"] and own["negative_technicians"] == ["tech_02"]
    assert tech["addresses"] == [] and tech["human_conclusions"] == [] and all(f["comment"] is None for f in tech["feedback"])
    assert len(tech["orders"]) <= 5 and all(o["technician_notes"] is not None or o["actual_problem"] is None for o in tech["orders"])


# ---------------------------------------------------------------- §7 address / negotiation
def test_order_requires_confirmed_unit_or_explicit_na(db_session):
    db = db_session
    with pytest.raises(OrderError) as exc:
        chat_service.handle(db, "sess_u", "", "set_address", {"latitude": 1.3496, "longitude": 103.9568, "formatted_address": "1 Tampines Central"})
    assert exc.value.code == "unit_required" and exc.value.kind == "DATA_INCOMPLETE"
    r = chat_service.handle(db, "sess_u", "", "set_address", {"latitude": 1.3496, "longitude": 103.9568, "formatted_address": "1 Tampines Central", "unit_not_applicable": True})
    assert r["draft"]["address"]["confirmed"] and r["draft"]["address"]["unit_not_applicable"]


def test_windows_offered_are_feasible_and_named(db_session):
    db = db_session
    r = chat_service.handle(db, "sess_w", "", "set_address", {"latitude": 1.3724, "longitude": 103.8937, "formatted_address": "Hougang Mall", "unit_number": "03-01"})
    r = chat_service.handle(db, "sess_w", "", "select_catalog", {"catalog_item_id": make_catalog_item_id("Refrigerator", "Not cooling")})
    assert [o["type"] for o in r["reply"]["options"]] == ["expedite_now", "expedite_now"]  # asked before any slot
    r = chat_service.handle(db, "sess_w", "", "set_expedite_now", {"now": False})
    wins = [o for o in r["reply"]["options"] if o["type"] == "window" and o["state"] == "available"]
    assert wins and all(o.get("technician_id") and o.get("earliest_start") for o in wins)
    assert any(o["type"] == "more_windows" for o in r["reply"]["options"])
    chat_service.handle(db, "sess_w", "", "set_contact", {"customer_name": "W", "contact_phone": "+65 1"})
    r2 = chat_service.handle(db, "sess_w", "", "set_window", {"window_start": wins[0]["window_start"], "window_end": wins[0]["window_end"]})
    assert r2["reply"]["card"] and r2["state"] == "confirming" and r2["reply"]["card"]["unit"] == "03-01"


FRIDGE = {"catalog_item_id": make_catalog_item_id("Refrigerator", "Not cooling")}
HOUGANG_MALL = {"latitude": 1.3724, "longitude": 103.8937, "formatted_address": "Hougang Mall", "unit_number": "03-01"}


def test_propose_windows_with_moves_is_paid(db_session):
    db = db_session
    clock = get_clock(db)
    from app.services import negotiation_service
    moves = negotiation_service.propose_windows(db, clock, catalog_item_id=FRIDGE["catalog_item_id"], location_id="loc_hougang", count=3, allow_moves=True)
    assert moves["data"]["windows"]
    for w in moves["data"]["windows"]:
        assert w["paid_required"] is True and w["affected_count"] >= 0 and w["decision"] in ("auto", "manual") and "technician_changes" in w
    free = negotiation_service.propose_windows(db, clock, catalog_item_id=FRIDGE["catalog_item_id"], location_id="loc_hougang", count=3)
    assert free["data"]["windows"] and all(w["paid_required"] is False and w["affected_count"] == 0 for w in free["data"]["windows"])


def test_chat_asks_expedite_first_then_shows_the_whole_day(db_session):
    """4th meeting 加急逻辑: pay to be served now, or pick any slot — free ones directly, busy ones against a fee."""
    db, s = db_session, "sess_urgent"
    chat_service.handle(db, s, "", "select_catalog", FRIDGE)
    r = chat_service.handle(db, s, "", "set_address", HOUGANG_MALL)
    # 1. the expedite question comes before any time slot
    assert [o["type"] for o in r["reply"]["options"]] == ["expedite_now", "expedite_now"]
    assert {o["now"] for o in r["reply"]["options"]} == {True, False}
    # 2. declining it shows the whole remaining day, not only the free part
    r = chat_service.handle(db, s, "", "set_expedite_now", {"now": False})
    slots = [o for o in r["reply"]["options"] if o["type"] == "window"]
    assert slots and {o["state"] for o in slots} <= {"available", "paid", "impossible", "unknown"}
    assert all({"state", "selectable", "paid_required"} <= set(o) for o in slots)
    assert all(o["selectable"] is (o["state"] in ("available", "paid")) for o in slots)
    assert all(o["paid_required"] is (o["state"] == "paid") for o in slots)
    assert not any(o["type"] == "payment" for o in r["reply"]["options"])  # payment only after a paid slot is chosen
    free = [o for o in slots if o["state"] == "available"]
    assert free, "the seeded day must still have free slots"
    # 3. a free slot is taken directly, with no payment step
    r = chat_service.handle(db, s, "", "set_window", {"window_start": free[0]["window_start"], "window_end": free[0]["window_end"],
                                                     "technician_id": free[0]["technician_id"], "paid_required": False})
    assert not any(o["type"] == "payment" for o in r["reply"]["options"])
    r = chat_service.handle(db, s, "", "set_contact", {"customer_name": "U", "contact_phone": "+65 4"})
    assert r["state"] == "confirming" and r["reply"]["card"]["paid_expedite"] is False


def test_paid_slot_asks_for_payment_and_creates_a_p1_order(db_session):
    """A busy slot is purchasable: choosing it triggers the payment question and the order enters at P1."""
    db, s = db_session, "sess_paid"
    chat_service.handle(db, s, "", "select_catalog", FRIDGE)
    chat_service.handle(db, s, "", "set_address", HOUGANG_MALL)
    r = chat_service.handle(db, s, "", "set_expedite_now", {"now": False})
    slots = [o for o in r["reply"]["options"] if o["type"] == "window"]
    chosen = next((o for o in slots if o["state"] == "paid"), None)
    if chosen is None:                       # a quiet day has nothing to buy; the free path is covered above
        pytest.skip("no busy slot in this seeded day")
    r = chat_service.handle(db, s, "", "set_window", {"window_start": chosen["window_start"], "window_end": chosen["window_end"],
                                                     "technician_id": chosen["technician_id"], "paid_required": True,
                                                     "affected_count": chosen["affected_count"]})
    assert r["draft"]["window_paid_required"] is True
    assert [o["type"] for o in r["reply"]["options"]] == ["payment", "payment"]
    r = chat_service.handle(db, s, "", "confirm_payment", {"paid": True})
    r = chat_service.handle(db, s, "", "set_contact", {"customer_name": "P", "contact_phone": "+65 5"})
    card = r["reply"]["card"]
    assert card["paid_expedite"] and not card["expedite_now"]
    # the customer card still carries no internal grading
    assert "priority" not in card and "complexity_level" not in card
    assert not any("P1" in str(v) or "complexity" in str(v) for v in card.values())
    r = chat_service.handle(db, s, "", "confirm", None)
    assert r["state"] == "submitted" and r["orders"][0]["effective_priority"] == "P1"


def test_send_someone_now_is_p0_and_skips_the_slot_question(db_session):
    """"师傅现在就来" is a safety-shaped request, so it enters at P0 and replaces the slot choice entirely."""
    db, s = db_session, "sess_now"
    chat_service.handle(db, s, "", "select_catalog", FRIDGE)
    chat_service.handle(db, s, "", "set_address", HOUGANG_MALL)
    r = chat_service.handle(db, s, "", "set_expedite_now", {"now": True})
    if not (r["draft"].get("expedite_preview") or {}).get("possible"):
        pytest.skip("nobody can come now in this seeded day")
    assert r["draft"]["expedite_now"] is True and r["draft"]["paid_expedite"] is True
    assert r["draft"]["window_start"] and not any(o["type"] == "window" for o in r["reply"]["options"])
    r = chat_service.handle(db, s, "", "set_contact", {"customer_name": "N", "contact_phone": "+65 6"})
    assert r["reply"]["card"]["expedite_now"] is True
    r = chat_service.handle(db, s, "", "confirm", None)
    o = db.get(WorkOrder, r["orders"][0]["order_id"])
    assert o.expedite_now is True and o.paid_expedite is True and o.base_priority == "P0"


def test_free_slot_session_never_sees_payment(db_session):
    db, s = db_session, "sess_calm"
    chat_service.handle(db, s, "", "select_catalog", FRIDGE)
    chat_service.handle(db, s, "", "set_address", HOUGANG_MALL)
    r = chat_service.handle(db, s, "", "set_expedite_now", {"now": False})
    free = [o for o in r["reply"]["options"] if o["type"] == "window" and o["state"] == "available"]
    assert free and all(o["paid_required"] is False and "free" in o["label"] for o in free)
    r = chat_service.handle(db, s, "", "set_window", {"window_start": free[0]["window_start"], "window_end": free[0]["window_end"],
                                                     "paid_required": False})
    assert not any(o["type"] == "payment" for o in r["reply"]["options"])


def test_free_text_urgency_is_noted_but_never_answers_the_paid_question(db_session):
    """Saying "urgent" is not consent to be charged: the expedite question is still put to the customer."""
    db, s = db_session, "sess_freeurgent"
    r = chat_service.handle(db, s, "my fridge is not cooling, this is urgent", None, None)
    assert r["draft"].get("urgent") is True
    chat_service.handle(db, s, "", "select_catalog", FRIDGE)
    r = chat_service.handle(db, s, "", "set_address", HOUGANG_MALL)
    assert [o["type"] for o in r["reply"]["options"]] == ["expedite_now", "expedite_now"]


def test_reschedule_request_opens_case_and_approval_moves_order(db_session):
    db = db_session
    clock = get_clock(db)
    from app.services import negotiation_service
    o = db.get(WorkOrder, "wo_010")
    res = negotiation_service.request_reschedule(db, clock, o, window_start=t(15, 0), window_end=t(16, 30), reason="meeting", customer_id=None, session_id="sess_r")
    res["id"] = res["request_id"]
    case = db.get(HumanCase, res["human_case_id"])
    assert case.source == "CUSTOMER_REQUEST" and res["status"] == "pending"
    out = negotiation_service.approve_reschedule(db, clock, res["id"], author="dispatcher")
    assert out["status"] == "approved" and out["dispatch"]["decision"] in ("auto", "manual", "unresolved")
    assert db.get(WorkOrder, "wo_010").window_start == t(15, 0)
