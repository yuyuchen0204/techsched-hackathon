from app.models.enums import Priority
from app.services.standby_service import compute_standby
from tests.conftest import assign_from_route, make_order, make_snapshot, make_tech


def test_standby_lists_zero_disturbance_alternatives_only(policy):
    # o1 assigned to t1; t2 is free (candidate); t3 is qualified but would have to shift its own o2 → excluded
    o1 = make_order("o1", ws=600, we=700, loc="L1", priority=Priority.P2)
    o2 = make_order("o2", ws=620, we=640, loc="L2")
    t1, t2, t3 = make_tech("t1", loc="L0"), make_tech("t2", loc="L4"), make_tech("t3", loc="L0")
    base = make_snapshot(510, [o1, o2], [t1, t2, t3])
    a1 = assign_from_route(base, "t1", ["o1"])
    a3 = assign_from_route(base, "t3", ["o2"])
    snap = make_snapshot(590, [o1, o2], [t1, t2, t3], a1 + a3)
    out = compute_standby(snap, "o1")
    ids = [c["technician_id"] for c in out]
    assert "t2" in ids and "t1" not in ids
    assert all(c["earliest_start"] <= 700 for c in out)
    # t3 could only take o1 by pushing o2 (window 620-640) → not a standby candidate
    assert "t3" not in ids or snap.assignments["o2"].service_start == 620
    assert len(out) <= policy.standby_max
