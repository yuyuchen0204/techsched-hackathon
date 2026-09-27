"""Section 24.5 / 24.3 flows against the persisted state (in-memory SQLite, seeded main scenario)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.models.entities import Assignment, CandidatePlan, ExecutionEvent, HumanCase, WorkOrder
from app.models.enums import AssignmentStatus, PlanStatus
from app.orchestration.orchestrator import dispatch_order
from app.services import event_service, execution_service, order_service, plan_service, risk_service
from app.services.catalog_importer import make_catalog_item_id
from app.services.clock import get_clock
from app.services.demo_service import seed_scenario
from app.services.order_service import OrderError
from app.services.snapshot import build_snapshot
from app.services.timeutil import local_time_utc

DAY = date(2026, 9, 15)


def t(h, m=0):
    return local_time_utc(DAY, h, m)


def new_order(db, *, trade, problem, loc, ws, we, paid=False, ref="sess_x"):
    clock = get_clock(db)
    return order_service.create_order(db, clock, customer_ref=ref, customer_name="T", contact_phone="+65 1", description="",
                                      location_id=loc, catalog_item_id=make_catalog_item_id(trade, problem),
                                      window_start=ws, window_end=we, paid_expedite=paid)


def test_seed_is_consistent(db_session):
    db = db_session
    snap = build_snapshot(db, get_clock(db))
    assert len(snap.techs) == 8 and len(snap.orders) == 20
    assert snap.assignments["wo_001"].locked and snap.assignments["wo_002"].locked
    assert all(o.id in snap.assignments for o in snap.orders.values())


def test_p3_insert_zero_disturbance_auto(db_session):
    db = db_session
    before = {k: (a.tech_id, a.service_start) for k, a in build_snapshot(db, get_clock(db)).assignments.items()}
    o = new_order(db, trade="Refrigerator", problem="Unusual noise", loc="loc_hougang", ws=t(11, 30), we=t(13, 0))
    out = dispatch_order(db, o.id, trigger="test")
    assert out.decision == "auto" and out.schedule_version
    after = build_snapshot(db, get_clock(db)).assignments
    assert o.id in after
    for k, v in before.items():
        assert (after[k].tech_id, after[k].service_start) == v  # nobody else changed


def test_paid_p1_moves_only_p3(db_session):
    db = db_session
    o = new_order(db, trade="Air Conditioning", problem="No cooling or heating", loc="loc_paya_lebar", ws=t(9, 30), we=t(9, 50), paid=True)
    assert o.effective_priority == "P1"
    out = dispatch_order(db, o.id, trigger="test")
    plans = [db.get(CandidatePlan, p) for p in out.plan_ids]
    committed = [p for p in plans if p.status == PlanStatus.COMMITTED]
    assert committed  # P1 has no cap on the number of moved P3 orders; every moved order must still be P3 (checked below)
    build_snapshot(db, get_clock(db))
    for oid in committed[0].affected_order_ids:
        assert db.get(WorkOrder, oid).effective_priority == "P3"
    over = [p for p in plans if p.status == PlanStatus.OVER_LIMIT]
    for p in over:
        assert p.policy_check["decision"] == "forbidden"
        with pytest.raises(OrderError) as exc:
            plan_service.approve_plan(db, get_clock(db), p.id, actor="d", reason=None, idempotency_key=None, expected_versions=None)
        assert exc.value.code == "over_limit"


def test_customer_cancel_rules_and_release(db_session):
    db = db_session
    res = event_service.customer_cancel(db, "wo_013", customer_ref="seed:wo_013", actor="customer", reason="x", idempotency_key="k1")
    assert res["status"] == "CANCELLED" and res["released_technician"] == "tech_03"
    again = event_service.customer_cancel(db, "wo_013", customer_ref="seed:wo_013", actor="customer", reason="x", idempotency_key="k1")
    assert again == {**res, "idempotent": True}  # idempotent replay
    third = event_service.customer_cancel(db, "wo_013", customer_ref="seed:wo_013", actor="customer", reason="x")
    assert third["already"] is True
    assert db.scalars(__import__("sqlalchemy").select(Assignment).where(Assignment.order_id == "wo_013", Assignment.status == AssignmentStatus.ACTIVE)).first() is None
    with pytest.raises(OrderError) as exc:
        event_service.customer_cancel(db, "wo_001", customer_ref="seed:wo_001", actor="customer", reason="x")
    assert exc.value.code == "already_departed"
    with pytest.raises(OrderError) as exc2:
        event_service.customer_cancel(db, "wo_012", customer_ref="someone_else", actor="customer", reason="x")
    assert exc2.value.code == "forbidden"
    # successor of wo_013 on tech_03 (wo_017) now departs from the real predecessor
    snap = build_snapshot(db, get_clock(db))
    a17 = snap.assignments["wo_017"]
    assert a17.origin_location_id != "loc_yishun"


@pytest.fixture
def manual_only(policy):
    """Force every feasible plan into review (threshold semantics are tested in test_priority_policy)."""
    old = policy.auto_score_threshold
    policy.auto_score_threshold = 100.0
    yield
    policy.auto_score_threshold = old


def test_cancel_kills_pending_plans_and_approval_cannot_revive(db_session, manual_only):
    db = db_session
    o = new_order(db, trade="Washing Machine", problem="Spin cycle malfunction", loc="loc_woodlands", ws=t(16, 0), we=t(17, 30))
    out = dispatch_order(db, o.id, trigger="test")
    assert out.decision == "manual"
    pending = [p for p in out.plan_ids if db.get(CandidatePlan, p).status == PlanStatus.PENDING_REVIEW]
    assert pending
    event_service.customer_cancel(db, o.id, customer_ref="sess_x", actor="customer", reason="x")
    assert db.get(CandidatePlan, pending[0]).status == PlanStatus.INVALIDATED
    with pytest.raises(OrderError) as exc:
        plan_service.approve_plan(db, get_clock(db), pending[0], actor="d", reason=None, idempotency_key=None, expected_versions=None)
    assert exc.value.status == 409
    assert db.get(WorkOrder, o.id).lifecycle_status == "CANCELLED"


def test_technician_cancel_keeps_customer_demand_and_classifies(db_session):
    db = db_session
    clock = get_clock(db)
    res = event_service.technician_unavailable(db, "tech_06", start=t(9, 0), end=t(18, 0), reason="sick", idempotency_key="tc1")
    released = {r["order_id"]: r for r in res["released"]}
    assert "wo_007" in released and released["wo_007"]["cancellation_priority"] == "P2"  # 150 min remaining
    o7 = db.get(WorkOrder, "wo_007")
    assert o7.lifecycle_status == "OPEN" and o7.technician_id is None
    assert event_service.technician_unavailable(db, "tech_06", start=t(9, 0), end=t(18, 0), reason="sick", idempotency_key="tc1") == {**res, "idempotent": True}
    # a near-deadline cancel becomes P0 / P1 per remaining minutes
    o = new_order(db, trade="Gas Stove", problem="Won't ignite", loc="loc_punggol", ws=t(8, 40), we=t(9, 5))
    dispatch_order(db, o.id, trigger="test")
    if db.get(WorkOrder, o.id).technician_id:
        tid = db.get(WorkOrder, o.id).technician_id
        res2 = event_service.technician_unavailable(db, tid, start=clock.now, end=t(18, 0), reason="x")
        rel = {r["order_id"]: r for r in res2["released"]}
        if o.id in rel:
            assert rel[o.id]["cancellation_priority"] in ("P0", "P1")
            assert rel[o.id]["cancellation_priority"] == ("P0" if rel[o.id]["remaining_minutes"] < 30 else "P1")


def test_execution_interrupt_is_manual_not_auto_reassigned(db_session):
    db = db_session
    res = event_service.technician_unavailable(db, "tech_03", start=get_clock(db).now, end=t(18, 0), reason="accident")
    assert "wo_002" in res["interrupted"]
    assert db.get(WorkOrder, "wo_002").lifecycle_status == "IN_PROGRESS"
    risks = {r.type: r for r in risk_service.active_risks(db, "wo_002")}
    assert "EXECUTION_INTERRUPTED" in risks and risks["EXECUTION_INTERRUPTED"].status == "manual"


def test_technician_unavailable_while_en_route_releases_the_order_at_p0(db_session):
    """The bug: a technician who fell ill mid-drive left the order locked to them (EN_ROUTE, no re-dispatch), while
    its EXECUTION_INTERRUPTED risk said P0 but never reached the order's priority. Now the drive is released, the
    order is P0, and it is recovered in the same event — by someone else."""
    db = db_session
    # put a real drive in progress: manual mode, leave exactly at the planned time. Pick a job somebody ELSE is
    # qualified for — the seeded Gas Stove order, for instance, has exactly one qualified technician, and "nobody can
    # take it" would then be the correct answer rather than a failure of this fix.
    from app.models.entities import Technician
    from app.services import technician_service
    from app.services.clock import set_now
    techs = {t.id: t for t in db.scalars(select(Technician)).all()}

    def someone_else_can(r: Assignment) -> bool:
        o = db.get(WorkOrder, r.order_id)
        trade, need = o.catalog_snapshot["trade_type"], int(o.catalog_snapshot["complexity_level"])
        return any(tid != r.technician_id and (t.skills or {}).get(trade, 0) >= need for tid, t in techs.items())
    a = next(r for r in db.scalars(select(Assignment).where(Assignment.status == AssignmentStatus.ACTIVE)).all()
             if r.travel_minutes >= 5 and db.get(WorkOrder, r.order_id).lifecycle_status == "OPEN" and someone_else_can(r))
    tid, oid = a.technician_id, a.order_id
    technician_service.set_sim_mode(db, tid, "manual")
    set_now(db, a.departure)
    technician_service.action(db, get_clock(db), tid, oid, "depart")
    set_now(db, a.departure + timedelta(minutes=a.travel_minutes // 2))     # halfway there
    assert db.get(WorkOrder, oid).lifecycle_status == "EN_ROUTE"

    res = event_service.technician_unavailable(db, tid, start=get_clock(db).now, end=t(18, 0), reason="fell ill on the road")
    o = db.get(WorkOrder, oid)
    # 1. released, not kept locked
    assert oid in [r["order_id"] for r in res["released"]], "the en-route order must be released for recovery"
    assert oid not in res["interrupted"]
    assert o.lifecycle_status != "EN_ROUTE" and o.departed_at is None
    # 2. it is P0 — as an order, not only as a risk row
    assert o.effective_priority == "P0", f"expected P0, got {o.effective_priority}"
    assert any(r["type"] == "EXECUTION_INTERRUPTED" and r["priority"] == "P0" for r in o.priority_reasons)
    # 3. and it was actually recovered in this same event, to somebody else
    outcome = next(x for x in res["dispatch"] if x["target_order_id"] == oid)
    assert outcome["decision"] in ("auto", "manual"), f"recovery ran: {outcome}"
    if outcome["decision"] == "auto":
        assert o.technician_id and o.technician_id != tid, "auto-recovered to a different technician"
        # once validly re-assigned the P0 reason clears on the next evaluation
        snap = build_snapshot(db, get_clock(db))
        risk_service.evaluate_order(db, get_clock(db), snap, o)
        assert "EXECUTION_INTERRUPTED" not in {r.type for r in risk_service.active_risks(db, oid)}
    # 4. the customer was told, and no human case was opened for something the system handled itself
    assert not any(c.category == "execution_interrupted" and c.order_id == oid
                   for c in db.scalars(select(HumanCase)).all())


def test_arrived_or_in_progress_still_goes_to_a_human(db_session):
    """Deliberately unchanged: a technician inside the customer's home mid-repair is not silently swapped —
    the lock stays and a person decides (policy execution.auto_release_on_unavailable lists EN_ROUTE only)."""
    db = db_session
    res = event_service.technician_unavailable(db, "tech_03", start=get_clock(db).now, end=t(18, 0), reason="accident")
    assert "wo_002" in res["interrupted"] and db.get(WorkOrder, "wo_002").lifecycle_status == "IN_PROGRESS"
    assert any(c.category == "execution_interrupted" and c.order_id == "wo_002" for c in db.scalars(select(HumanCase)).all())


def test_interrupted_execution_can_be_released_by_the_dispatcher(db_session):
    """Before V3.1 an interrupted departed order was a dead end: no cancel (409), no re-dispatch (execution locked),
    only a human case with no action attached. abort_execution is the way out."""
    db = db_session
    event_service.technician_unavailable(db, "tech_03", start=get_clock(db).now, end=t(18, 0), reason="accident")
    o = db.get(WorkOrder, "wo_002")
    assert o.lifecycle_status == "IN_PROGRESS" and o.technician_id == "tech_03"
    # the dead end: neither customer cancel nor a re-dispatch can move it
    with pytest.raises(OrderError) as exc:
        event_service.customer_cancel(db, "wo_002", customer_ref=None, actor="dispatcher", reason="stuck")
    assert exc.value.code == "already_departed"
    assert dispatch_order(db, "wo_002", trigger="test").reason == "order already departed; execution is locked"

    res = event_service.abort_execution(db, "wo_002", actor="dispatcher", reason="technician had an accident on site")
    o = db.get(WorkOrder, "wo_002")
    assert res["released_from"] == "IN_PROGRESS" and res["released_technician"] == "tech_03"
    assert o.lifecycle_status != "IN_PROGRESS" and o.departed_at is None and o.service_started_at is None
    assert db.scalars(select(Assignment).where(Assignment.order_id == "wo_002",
                                               Assignment.status == AssignmentStatus.ACTIVE)).first() is None \
        or o.technician_id != "tech_03"   # released, and only re-assigned to somebody else
    assert "EXECUTION_INTERRUPTED" not in {r.type for r in risk_service.active_risks(db, "wo_002")}
    # the facts are reported back, not silently dropped (wo_002 is seeded IN_PROGRESS, so it has no execution_events rows)
    assert res["released_facts"]["lifecycle_status"] == "IN_PROGRESS" and res["released_facts"]["technician_id"] == "tech_03"
    # a second abort on a released order is refused with a clear reason
    with pytest.raises(OrderError) as exc2:
        event_service.abort_execution(db, "wo_002", actor="dispatcher", reason="again")
    assert exc2.value.code == "not_executing"


def test_abort_execution_can_also_cancel_and_requires_a_reason(db_session):
    db = db_session
    execution_service.advance_clock(db, 45)  # 08:30 → 09:15, wo_003 has departed
    assert db.get(WorkOrder, "wo_003").lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS")
    with pytest.raises(OrderError) as exc:
        event_service.abort_execution(db, "wo_003", actor="dispatcher", reason="  ")
    assert exc.value.code == "reason_required"
    res = event_service.abort_execution(db, "wo_003", actor="dispatcher", reason="customer not at home", outcome="cancel")
    assert res["outcome"] == "cancel" and db.get(WorkOrder, "wo_003").lifecycle_status == "CANCELLED"
    # this one really drove: its depart/arrive rows survive the abort as the audit trail
    assert db.scalars(select(ExecutionEvent).where(ExecutionEvent.order_id == "wo_003")).first() is not None


def test_clock_advance_executes_and_departure_blocks_cancel(db_session):
    db = db_session
    out = execution_service.advance_clock(db, 45)  # 08:30 → 09:15
    kinds = {(e["order_id"], e["lifecycle_status"]) for e in out["fired_events"]}
    assert ("wo_001", "ARRIVED") in kinds or ("wo_001", "IN_PROGRESS") in kinds
    o3 = db.get(WorkOrder, "wo_003")
    assert o3.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS")  # departed 09:00 (zero travel → already working)
    with pytest.raises(OrderError) as exc:
        event_service.customer_cancel(db, "wo_003", customer_ref="seed:wo_003", actor="customer", reason="late")
    assert exc.value.code == "already_departed"
    # repeat advance events are idempotent: no duplicate departures
    again = execution_service.advance_clock(db, 1)
    assert not any(e["order_id"] == "wo_003" and e["lifecycle_status"] == "EN_ROUTE" for e in again["fired_events"])


def test_approve_flow_idempotent_and_stale(db_session, manual_only):
    db = db_session
    o = new_order(db, trade="Water Heater", problem="Gas water heater won't ignite", loc="loc_woodlands", ws=t(15, 0), we=t(16, 0))
    out = dispatch_order(db, o.id, trigger="test")
    pending = [p for p in out.plan_ids if db.get(CandidatePlan, p).status == PlanStatus.PENDING_REVIEW]
    assert pending and db.get(WorkOrder, o.id).scheduling_status == "PENDING_REVIEW"
    clock = get_clock(db)
    r1 = plan_service.approve_plan(db, clock, pending[0], actor="d", reason="ok", idempotency_key="ap1", expected_versions=None)
    r2 = plan_service.approve_plan(db, clock, pending[0], actor="d", reason="ok", idempotency_key="ap1", expected_versions=None)
    assert r1 == r2 and db.get(CandidatePlan, pending[0]).status == PlanStatus.COMMITTED
    assert db.get(WorkOrder, o.id).scheduling_status == "ASSIGNED"
    for sib in pending[1:]:
        with pytest.raises(OrderError):
            plan_service.approve_plan(db, clock, sib, actor="d", reason=None, idempotency_key=None, expected_versions=None)
    # a plan generated against an older schedule version is stale once anything else commits
    o2 = new_order(db, trade="Refrigerator", problem="Not cooling", loc="loc_bedok", ws=t(15, 0), we=t(16, 30))
    out2 = dispatch_order(db, o2.id, trigger="test")
    stale = next(p for p in out2.plan_ids if db.get(CandidatePlan, p).status == PlanStatus.PENDING_REVIEW)
    o3 = new_order(db, trade="Refrigerator", problem="Unusual noise", loc="loc_hougang", ws=t(15, 0), we=t(16, 30))
    out3 = dispatch_order(db, o3.id, trigger="test")
    p3 = next(p for p in out3.plan_ids if db.get(CandidatePlan, p).status == PlanStatus.PENDING_REVIEW)
    plan_service.approve_plan(db, clock, p3, actor="d", reason=None, idempotency_key=None, expected_versions=None)
    with pytest.raises(OrderError) as exc:
        plan_service.approve_plan(db, clock, stale, actor="d", reason=None, idempotency_key=None, expected_versions=None)
    assert exc.value.code in ("schedule_changed", "facts_changed") and exc.value.status == 409
    assert db.get(CandidatePlan, stale).status == PlanStatus.EXPIRED


def test_reset_increments_generation_and_isolates(db_session):
    db = db_session
    g0 = get_clock(db).scenario_generation
    o = new_order(db, trade="Refrigerator", problem="Not cooling", loc="loc_bedok", ws=t(12, 0), we=t(13, 30))
    seed_scenario(db, "main")
    clock = get_clock(db)
    assert clock.scenario_generation == g0 + 1
    assert db.get(WorkOrder, o.id) is None
    snap = build_snapshot(db, clock)
    assert len(snap.orders) == 20 and all(a for a in snap.assignments.values())


def test_non_scheduling_complaint_does_not_change_priority(db_session):
    db = db_session
    before = db.get(WorkOrder, "wo_004").effective_priority
    res = event_service.complaint(db, "wo_004", complaint_type="attitude", text="rude", customer_ref="seed:wo_004")
    assert res["handling"] == "manual_service_queue" and res["priority_changed"] is False
    assert db.get(WorkOrder, "wo_004").effective_priority == before


def test_lateness_complaint_verified_only_after_deadline(db_session):
    db = db_session
    res = event_service.complaint(db, "wo_012", complaint_type="lateness", text="late!", customer_ref="seed:wo_012")
    assert res["verified"] is False
    execution_service.advance_clock(db, 5)
    # make wo_012 overdue: move clock to 12:31 without executing (technician unavailable first so nobody departs)
    event_service.technician_unavailable(db, "tech_04", start=t(8, 35), end=t(18, 0), reason="x")
    from app.services.clock import set_now
    set_now(db, t(12, 31))
    res2 = event_service.complaint(db, "wo_012", complaint_type="lateness", text="still late", customer_ref="seed:wo_012")
    assert res2["verified"] is True and db.get(WorkOrder, "wo_012").effective_priority == "P0"
