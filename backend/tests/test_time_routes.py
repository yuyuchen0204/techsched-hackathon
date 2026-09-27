"""Section 24.2: time and route behaviours."""
from app.models.enums import SolveStatus
from app.scheduling.simulator import simulate_route
from app.scheduling.solver import solve_insert
from app.scheduling.validator import validate_plan
from tests.conftest import assign_from_route, make_matrix, make_order, make_snapshot, make_tech


def test_skill_insufficient_not_dispatchable_even_with_high_score(policy):
    o = make_order("o1", level=4, ws=600, we=720, loc="L1")
    t = make_tech("t1", skills={"Air Conditioning": 3}, loc="L1")  # zero travel, perfect otherwise
    snap = make_snapshot(510, [o], [t])
    res = solve_insert(snap, o, policy)
    assert res.status == SolveStatus.NO_SOLUTION_FOUND and "qualified" in (res.reason or "")


def test_unreachable_is_not_zero_minutes(policy):
    o = make_order("o1", ws=600, we=720, loc="L1")
    t = make_tech("t1", loc="L0")
    m = make_matrix(["L0", "L1", "L2", "L3", "L4"], overrides={("L0", "L1"): None})
    snap = make_snapshot(510, [o], [t], matrix=m)
    rr = simulate_route(snap, t, ["o1"])
    assert not rr.feasible and "unreachable" in rr.violations[0]
    assert solve_insert(snap, o, policy).status == SolveStatus.NO_SOLUTION_FOUND


def test_start_at_window_end_is_legal_and_service_may_finish_after(policy):
    # travel 20, tech free at 700 → arrival 720 == window_end (legal, [start,end) semantics)
    o = make_order("o1", ws=600, we=720, loc="L1", duration=35)
    t = make_tech("t1", loc="L0")
    snap = make_snapshot(700, [o], [t])
    res = solve_insert(snap, o, policy)
    assert res.status == SolveStatus.FEASIBLE
    a = res.candidates[0].plan["o1"]
    assert a.service_start == 720 and a.service_end == 755
    late = make_snapshot(701, [o], [t])
    assert solve_insert(late, o, policy).status == SolveStatus.NO_SOLUTION_FOUND


def test_shift_end_and_unavailability_are_hard(policy):
    o = make_order("o1", ws=1040, we=1080, loc="L1", duration=35)  # would end 1075 (ok) but shift ends 1060
    t = make_tech("t1", shift=(480, 1060))
    snap = make_snapshot(510, [o], [t])
    assert solve_insert(snap, o, policy).status == SolveStatus.NO_SOLUTION_FOUND
    o2 = make_order("o2", ws=600, we=640, loc="L1", duration=35)
    t2 = make_tech("t2", unavailable=[(560, 700)])
    snap2 = make_snapshot(510, [o2], [t2])
    assert solve_insert(snap2, o2, policy).status == SolveStatus.NO_SOLUTION_FOUND


def test_break_pushes_travel_and_service_correctly(policy):
    o = make_order("o1", ws=700, we=800, loc="L1", duration=35)
    t = make_tech("t1", breaks=[(690, 735)])
    snap = make_snapshot(600, [o], [t])
    rr = simulate_route(snap, t, ["o1"])
    a = rr.assignments[0]
    assert a.service_start >= 735 and not (a.departure < 735 and a.arrival > 690)
    assert validate_plan(snap, {"o1": a}).ok


def test_busy_technician_continues_from_locked_task(policy):
    o1 = make_order("o1", ws=500, we=600, loc="L1", status="EN_ROUTE")
    o2 = make_order("o2", ws=560, we=760, loc="L2")
    t = make_tech("t1")
    base = make_snapshot(500, [o1, o2], [t])
    locked = assign_from_route(base, "t1", ["o1"], locked={"o1"})
    snap = make_snapshot(520, [o1, o2], [t], locked)
    res = solve_insert(snap, o2, policy)
    assert res.status == SolveStatus.FEASIBLE
    a = res.candidates[0].plan["o2"]
    assert a.origin_location_id == "L1" and a.departure >= locked[0].service_end


def test_successor_unreachable_rejects_insert(policy):
    """Predecessor reachable, but the successor's window can no longer be met → whole route rejected."""
    o1 = make_order("o1", ws=600, we=640, loc="L1", duration=35)
    o2 = make_order("o2", ws=650, we=660, loc="L2", duration=35)
    t = make_tech("t1")
    base = make_snapshot(510, [o1, o2], [t])
    committed = assign_from_route(base, "t1", ["o1", "o2"])
    o3 = make_order("o3", ws=600, we=700, loc="L3", duration=50)
    snap = make_snapshot(510, [o1, o2, o3], [t], committed)
    res = solve_insert(snap, o3, policy)  # P3: cannot shift o2 (would miss its window anyway)
    assert all("o2" not in c.affected.affected_ids for c in res.candidates)
    for c in res.candidates:
        assert c.plan["o2"].service_start <= 660


def test_cancel_recomputes_predecessor_to_successor(policy):
    m = make_matrix(["L0", "L1", "L2", "L3", "L4"], overrides={("L0", "L2"): 45, ("L1", "L2"): 20, ("L0", "L1"): 20})
    o1 = make_order("o1", ws=600, we=700, loc="L1")
    o2 = make_order("o2", ws=700, we=800, loc="L2")
    t = make_tech("t1", loc="L0")
    base = make_snapshot(510, [o1, o2], [t], matrix=m)
    committed = assign_from_route(base, "t1", ["o1", "o2"])
    assert committed[1].origin_location_id == "L1" and committed[1].travel == 20
    # o1 cancelled: successor route must start from the real anchor L0 (45 min), start pinned at 700
    o1c = make_order("o1", ws=600, we=700, loc="L1", status="CANCELLED")
    snap = make_snapshot(510, [o1c, o2], [t], [committed[1]], matrix=m)
    rr = simulate_route(snap, t, ["o2"], {"o2": committed[1].service_start})
    a = rr.assignments[0]
    assert a.origin_location_id == "L0" and a.travel == 45 and a.service_start == 700
