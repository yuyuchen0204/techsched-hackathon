from app.models.enums import SolveStatus
from app.scheduling.solver import solve_initial, solve_insert
from app.scheduling.validator import validate_plan
from tests.conftest import assign_from_route, make_order, make_snapshot, make_tech


def test_initial_and_insert_basic(policy):
    o1 = make_order("o1", ws=540, we=660, loc="L1")
    o2 = make_order("o2", ws=600, we=720, loc="L2")
    o3 = make_order("o3", ws=700, we=800, loc="L3")
    t1 = make_tech("t1")
    t2 = make_tech("t2")
    snap = make_snapshot(510, [o1, o2, o3], [t1, t2])
    res = solve_initial(snap, ["o1", "o2", "o3"], policy)
    assert res.status == SolveStatus.FEASIBLE, res.reason
    plan = res.candidates[0].plan
    assert set(plan) == {"o1", "o2", "o3"}
    assert validate_plan(snap, plan).ok
    for a in plan.values():
        o = snap.orders[a.order_id]
        assert o.window_start <= a.service_start <= o.window_end
        assert a.arrival == a.departure + a.travel
        assert a.service_end == a.service_start + o.duration

    # commit and insert a new P3 with zero disturbance
    snap2 = make_snapshot(510, [o1, o2, o3, make_order("o4", ws=800, we=900, loc="L4")], [t1, t2], list(plan.values()))
    res2 = solve_insert(snap2, snap2.orders["o4"], policy)
    assert res2.status == SolveStatus.FEASIBLE, res2.reason
    best = res2.candidates[0]
    assert best.affected.count == 0
    assert best.authority.ok
    assert best.decision_score is not None and best.decision_score > 0
    print("strategies", [(c.strategy, c.strategy_tags, round(c.decision_score or 0, 1)) for c in res2.candidates])


def test_locked_prefix_anchor(policy):
    o1 = make_order("o1", ws=500, we=600, loc="L1", status="IN_PROGRESS")
    o2 = make_order("o2", ws=560, we=700, loc="L2")
    t1 = make_tech("t1")
    snap0 = make_snapshot(500, [o1, o2], [t1])
    a1 = assign_from_route(snap0, "t1", ["o1"], locked={"o1"})
    snap = make_snapshot(530, [o1, o2], [t1], a1)
    loc, t = snap.anchor("t1")
    assert loc == "L1" and t == a1[0].service_end
    res = solve_insert(snap, o2, policy)
    assert res.status == SolveStatus.FEASIBLE
    a2 = res.candidates[0].plan["o2"]
    assert a2.origin_location_id == "L1" and a2.departure >= a1[0].service_end
