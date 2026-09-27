"""Sections 24.3 / 24.4: priority, authority limits, scores."""
from app.models.enums import PolicyDecision, Priority, RiskType, SolveStatus
from app.scheduling.affected import compute_affected
from app.scheduling.domain import Assign
from app.scheduling.policy import AuthorityCheck, check_authority, decide
from app.scheduling.priority import base_priority, cancellation_priority, combine, evaluate_order_risks
from app.scheduling.solver import evaluate_plan, select_strategies, solve_insert
from tests.conftest import assign_from_route, make_order, make_snapshot, make_tech


def test_base_priorities_and_paid_floor(policy):
    assert base_priority(False, policy) == Priority.P3 and base_priority(True, policy) == Priority.P1
    reasons = evaluate_order_risks(now=800, window_end=720, lifecycle_status="OPEN", service_started=False, predicted_start=None,
                                   has_valid_assignment=False, tech_cancel_remaining=None, verified_late_complaint=False, policy=policy)
    risk, eff = combine(Priority.P1, reasons)
    assert risk == Priority.P0 and eff == Priority.P0  # paid may rise to P0
    calm = evaluate_order_risks(now=500, window_end=720, lifecycle_status="OPEN", service_started=False, predicted_start=600,
                                has_valid_assignment=True, tech_cancel_remaining=None, verified_late_complaint=False, policy=policy)
    assert combine(Priority.P1, calm)[1] == Priority.P1  # never below paid floor
    assert combine(Priority.P3, calm)[1] == Priority.P3  # risk resolved → back down


def test_cancellation_boundaries(policy):
    assert cancellation_priority(29, policy) == Priority.P0
    assert cancellation_priority(30, policy) == Priority.P1
    assert cancellation_priority(120, policy) == Priority.P1
    assert cancellation_priority(121, policy) == Priority.P2


def test_predicted_late_beats_approaching_and_complaint_rules(policy):
    r = evaluate_order_risks(now=700, window_end=720, lifecycle_status="OPEN", service_started=False, predicted_start=730,
                             has_valid_assignment=True, tech_cancel_remaining=None, verified_late_complaint=False, policy=policy)
    types = {x.type for x in r}
    assert RiskType.PREDICTED_LATE in types and RiskType.APPROACHING_DEADLINE not in types
    r2 = evaluate_order_risks(now=700, window_end=720, lifecycle_status="OPEN", service_started=False, predicted_start=710,
                              has_valid_assignment=True, tech_cancel_remaining=None, verified_late_complaint=False, policy=policy)
    assert {x.type for x in r2} == {RiskType.APPROACHING_DEADLINE}
    # verified lateness complaint only counts when past deadline and not started
    r3 = evaluate_order_risks(now=700, window_end=720, lifecycle_status="OPEN", service_started=False, predicted_start=710,
                              has_valid_assignment=True, tech_cancel_remaining=None, verified_late_complaint=True, policy=policy)
    assert RiskType.LATENESS_COMPLAINT_VERIFIED not in {x.type for x in r3}
    # started orders never re-trigger "not started" risks
    r4 = evaluate_order_risks(now=800, window_end=720, lifecycle_status="IN_PROGRESS", service_started=True, predicted_start=None,
                              has_valid_assignment=True, tech_cancel_remaining=None, verified_late_complaint=True, policy=policy)
    assert r4 == []


def _two_tech_world(policy, p_other=Priority.P3, other_status="OPEN"):
    """t1 has o1 (window 600-700) and o2 (700-800) back to back; target o3 wants 600-620 at L1."""
    o1 = make_order("o1", ws=600, we=700, loc="L1", priority=p_other, status=other_status)
    o2 = make_order("o2", ws=640, we=800, loc="L2", priority=Priority.P3)
    t1 = make_tech("t1", loc="L0")
    t2 = make_tech("t2", loc="L4")
    base = make_snapshot(510, [o1, o2], [t1, t2])
    committed = assign_from_route(base, "t1", ["o1", "o2"], locked={"o1"} if other_status != "OPEN" else set())
    return o1, o2, t1, t2, committed


def test_p1_may_shift_any_p3_but_not_p2_or_departed(policy):
    o1, o2, t1, t2, committed = _two_tech_world(policy)
    target = make_order("o3", ws=600, we=620, loc="L1", priority=Priority.P1, paid=True)
    snap = make_snapshot(510, [o1, o2, target], [t1, t2], committed)
    res = solve_insert(snap, target, policy)
    assert res.status == SolveStatus.FEASIBLE
    for c in res.candidates:
        assert all(snap.orders[i].priority == Priority.P3 for i in c.affected.affected_ids)
    # same world but o1 is P2 → P1 may not move it
    o1p2, o2, t1, t2, committed = _two_tech_world(policy, p_other=Priority.P2)
    snap2 = make_snapshot(510, [o1p2, o2, target], [t1, t2], committed)
    res2 = solve_insert(snap2, target, policy)
    assert all("o1" not in c.affected.affected_ids for c in res2.candidates)
    # departed o1 can never move
    o1d, o2, t1, t2, committed = _two_tech_world(policy, other_status="EN_ROUTE")
    snap3 = make_snapshot(510, [o1d, o2, target], [t1, t2], committed)
    res3 = solve_insert(snap3, target, policy)
    assert all("o1" not in c.affected.affected_ids for c in res3.candidates)


def test_authority_limits_are_hard(policy):
    orders = {f"o{i}": make_order(f"o{i}", ws=600 + i, we=900, loc="L1") for i in range(1, 7)}
    t = make_tech("t1")
    snap = make_snapshot(510, list(orders.values()), [t])
    plan = {oid: Assign(oid, "t1", "L0", 600, 620, 620 + i, 655 + i, 20, 0) for i, oid in enumerate(orders)}
    base = {oid: Assign(oid, "t1", "L0", 600, 620, 600 + i, 635 + i, 20, 0) for i, oid in enumerate(orders)}  # all shift by 20
    snap.assignments = base
    aff = compute_affected(snap, plan, {"target"})
    assert aff.count == 6
    chk = check_authority(snap, Priority.P1, aff, policy, {"target"})
    assert chk.ok and chk.max_affected is None  # P1: no cap on moved P3 orders
    # P0 can move 5 P2/P3 but not 6, and never a P1/P0
    five = {k: v for k, v in plan.items() if k != "o6"}
    five["o6"] = base["o6"]
    aff5 = compute_affected(snap, five, {"target"})
    assert aff5.count == 5 and check_authority(snap, Priority.P0, aff5, policy, {"target"}).ok
    assert not check_authority(snap, Priority.P0, aff, policy, {"target"}).ok
    snap.orders["o1"] = make_order("o1", ws=601, we=900, loc="L1", priority=Priority.P1)
    chk_p0 = check_authority(snap, Priority.P0, aff5, policy, {"target"})
    assert not chk_p0.ok and any("may not move P1" in v for v in chk_p0.violations)


def test_affected_counting_rules(policy):
    o1 = make_order("o1", ws=600, we=900, loc="L1")
    o2 = make_order("o2", ws=600, we=900, loc="L2")
    o3 = make_order("o3", ws=600, we=900, loc="L3")
    snap = make_snapshot(510, [o1, o2, o3], [make_tech("t1"), make_tech("t2")])
    snap.assignments = {"o1": Assign("o1", "t1", "L0", 580, 600, 600, 635, 20, 0), "o2": Assign("o2", "t1", "L1", 635, 655, 655, 690, 20, 0),
                        "o3": Assign("o3", "t2", "L0", 580, 600, 600, 635, 20, 0)}
    plan = {"o1": Assign("o1", "t2", "L0", 580, 600, 601, 636, 20, 0),   # tech + time → 1
            "o2": Assign("o2", "t1", "L0", 635, 655, 655, 690, 20, 0),   # travel-only (origin changed) → 0
            "o3": Assign("o3", "t2", "L0", 580, 600, 600, 635, 20, 0)}   # unchanged
    aff = compute_affected(snap, plan, {"o3"})
    assert aff.affected_ids == ["o1"] and aff.technician_changes == 1 and aff.total_shift_minutes == 1
    changes = {d.order_id: d.change for d in aff.diff}
    assert changes["o2"] == "travel_only" and changes["o3"] == "unchanged"
    # target itself is never counted
    aff2 = compute_affected(snap, {**plan, "o3": Assign("o3", "t1", "L0", 580, 600, 700, 735, 20, 0)}, {"o3"})
    assert aff2.affected_ids == ["o1"]


def test_threshold_is_strict_and_decision_uses_minimum(policy):
    auth = AuthorityCheck(True, 0, [], 0)
    assert decide(target_priority=Priority.P3, decision_score=70.0, validation_ok=True, authority=auth, policy=policy, has_changes=True).decision == PolicyDecision.MANUAL
    assert decide(target_priority=Priority.P3, decision_score=70.01, validation_ok=True, authority=auth, policy=policy, has_changes=True).decision == PolicyDecision.AUTO
    # P0 rules
    assert decide(target_priority=Priority.P0, decision_score=65.0, validation_ok=True, authority=auth, policy=policy, has_changes=True).decision == PolicyDecision.MANUAL
    assert decide(target_priority=Priority.P0, decision_score=95.0, validation_ok=True, authority=auth, policy=policy, has_changes=True).decision == PolicyDecision.AUTO
    auth3 = AuthorityCheck(True, 5, ["P2", "P3"], 3)
    assert decide(target_priority=Priority.P0, decision_score=95.0, validation_ok=True, authority=auth3, policy=policy, has_changes=True).decision == PolicyDecision.MANUAL
    auth1 = AuthorityCheck(True, None, ["P3"], 7)
    out1 = decide(target_priority=Priority.P1, decision_score=80.0, validation_ok=True, authority=auth1, policy=policy, has_changes=True)
    assert out1.decision == PolicyDecision.AUTO and "affected 7/no limit" in out1.reasons[0]
    bad = AuthorityCheck(False, 2, ["P3"], 3, ["affected 3 exceeds limit 2 for P1"])
    out = decide(target_priority=Priority.P1, decision_score=99.0, validation_ok=True, authority=bad, policy=policy, has_changes=True)
    assert out.decision == PolicyDecision.FORBIDDEN and out.over_limit
    assert decide(target_priority=Priority.P3, decision_score=None, validation_ok=True, authority=auth, policy=policy, has_changes=False).decision == PolicyDecision.NO_ACTION


def test_decision_score_is_min_over_changed(policy):
    o1 = make_order("o1", ws=600, we=900, loc="L1")
    o2 = make_order("o2", ws=600, we=900, loc="L2")
    t1 = make_tech("t1", skills={"Air Conditioning": 5}, loc="L1")
    t2 = make_tech("t2", skills={"Air Conditioning": 2}, loc="L4")
    snap = make_snapshot(510, [o1, o2], [t1, t2])
    snap.assignments = {"o1": Assign("o1", "t1", "L1", 600, 600, 600, 635, 0, 0)}
    plan = {"o1": Assign("o1", "t2", "L4", 580, 600, 600, 635, 20, 0), "o2": Assign("o2", "t1", "L1", 600, 620, 620, 655, 20, 0)}
    cand = evaluate_plan(snap, plan, {"o2"}, Priority.P1, policy)
    assert cand.decision_score == min(cand.plan["o1"].match_score, cand.plan["o2"].match_score)
    assert cand.decision_score == cand.plan["o1"].match_score  # the worse re-assignment drives the decision


def test_strategies_never_fake_three(policy):
    o = make_order("o1", ws=600, we=700, loc="L1")
    snap = make_snapshot(510, [o], [make_tech("t1")])
    res = solve_insert(snap, o, policy)
    assert len(res.candidates) == 1 and set(res.candidates[0].strategy_tags) == {"faster_response", "less_disruption", "balanced"}
    assert select_strategies([]) == []


def test_time_hint_reports_past_and_unparsed():
    from datetime import date

    from app.agents.understanding import parse_time_hint_ex
    from app.services.clock import Clock
    from app.services.timeutil import local_time_utc
    c = Clock(now=local_time_utc(date(2026, 9, 15), 11, 50), day_origin=local_time_utc(date(2026, 9, 15), 0, 0),
              timezone="Asia/Singapore", running=False, scenario_generation=1, scenario_name="main", seed=1)
    assert parse_time_hint_ex(c, "7:00am") == (None, "past")
    assert parse_time_hint_ex(c, "whenever") == (None, "unparsed")
    win, note = parse_time_hint_ex(c, "2pm")
    assert note is None and win is not None and win[0] == local_time_utc(date(2026, 9, 15), 14, 0)


def test_explicit_clock_beats_day_part():
    from datetime import date

    from app.agents.understanding import parse_time_hint_ex
    from app.services.clock import Clock
    from app.services.timeutil import local_time_utc
    c = Clock(now=local_time_utc(date(2026, 9, 15), 8, 30), day_origin=local_time_utc(date(2026, 9, 15), 0, 0),
              timezone="Asia/Singapore", running=False, scenario_generation=1, scenario_name="main", seed=1)
    assert parse_time_hint_ex(c, "下午4点")[0][0] == local_time_utc(date(2026, 9, 15), 16, 0)
    assert parse_time_hint_ex(c, "afternoon around 3pm")[0][0] == local_time_utc(date(2026, 9, 15), 15, 0)
    assert parse_time_hint_ex(c, "afternoon")[0][0] == local_time_utc(date(2026, 9, 15), 13, 0)


def test_p1_urgent_front_insertion_sends_soonest_technician_and_rehomes_p3(policy):
    """Expedite = whoever can come now: the target goes first on t1's route even though t1 has three P3 jobs; the job
    that no longer fits (o1) is re-homed on t2, the rest keep their starts. No cap on moved orders for P1."""
    from tests.conftest import make_matrix
    o1 = make_order("o1", ws=540, we=560, loc="L1")
    o2 = make_order("o2", ws=600, we=620, loc="L2")
    o3 = make_order("o3", ws=660, we=680, loc="L3")
    target = make_order("tgt", ws=510, we=600, loc="L4", priority=Priority.P1, paid=True)
    t1, t2 = make_tech("t1", loc="L0"), make_tech("t2", loc="L3")
    matrix = make_matrix(["L0", "L1", "L2", "L3", "L4"], overrides={("L3", "L4"): 90})  # t2 is far from the urgent customer
    base = make_snapshot(510, [o1, o2, o3, target], [t1, t2], matrix=matrix)
    committed = assign_from_route(base, "t1", ["o1", "o2", "o3"])
    snap = make_snapshot(510, [o1, o2, o3, target], [t1, t2], committed, matrix=matrix)
    res = solve_insert(snap, target, policy)
    assert res.status == SolveStatus.FEASIBLE
    urgent = [c for c in res.candidates if c.move_kind == "urgent"]
    assert urgent, [(c.move_kind, c.target_start) for c in res.candidates]
    c = min(res.candidates, key=lambda c: c.target_start)
    assert c.move_kind == "urgent" and c.plan["tgt"].tech_id == "t1" and c.plan["tgt"].service_start == 530
    assert c.plan["o1"].tech_id == "t2" and c.plan["o2"].service_start == 600 and c.plan["o3"].service_start == 660
    assert c.authority.ok and c.validation.ok and c.affected.affected_ids == ["o1"]
