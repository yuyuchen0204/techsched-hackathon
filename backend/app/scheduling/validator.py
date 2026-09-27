"""ConstraintValidator: independent hard-constraint check of a full plan (does not trust the solver)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.scheduling.domain import Assign, Snapshot, overlaps


@dataclass
class ValidationResult:
    ok: bool
    violations: list[dict[str, Any]] = field(default_factory=list)

    def add(self, code: str, message: str, **ctx: Any) -> None:
        self.ok = False
        self.violations.append({"code": code, "message": message, **ctx})

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": self.violations}


def _same_timing(a: Assign, b: Assign) -> bool:
    return (a.tech_id == b.tech_id and a.departure == b.departure and a.service_start == b.service_start
            and a.origin_location_id == b.origin_location_id and a.travel == b.travel)


def validate_plan(snap: Snapshot, plan: dict[str, Assign], *, allow_missing: set[str] | None = None) -> ValidationResult:
    """plan = full set of ACTIVE assignments after the change (order_id -> Assign).

    allow_missing: order ids that legitimately have no assignment (e.g. unassigned in initial batch).
    """
    res = ValidationResult(ok=True)
    allow_missing = allow_missing or set()

    # 8. no silently dropped valid orders; 7. locked tasks untouched
    for oid, base in snap.assignments.items():
        order = snap.orders.get(oid)
        if order is None or order.closed:
            continue
        new = plan.get(oid)
        if new is None:
            if oid not in allow_missing:
                res.add("dropped_order", f"active order {oid} lost its assignment", order_id=oid)
            continue
        if base.locked and (new.tech_id != base.tech_id or new.service_start != base.service_start
                            or new.departure != base.departure):
            res.add("locked_changed", f"locked assignment {oid} was modified", order_id=oid)

    per_tech: dict[str, list[Assign]] = {}
    for oid, a in plan.items():
        order = snap.orders.get(oid)
        tech = snap.techs.get(a.tech_id)
        if order is None:
            res.add("unknown_order", f"order {oid} not in snapshot", order_id=oid)
            continue
        if tech is None:
            res.add("unknown_technician", f"technician {a.tech_id} not in snapshot", order_id=oid)
            continue
        if a.order_id != oid:
            res.add("inconsistent_plan", "assignment order id mismatch", order_id=oid)
        if order.closed:
            res.add("closed_order_assigned", f"order {oid} is {order.lifecycle_status}", order_id=oid)
        per_tech.setdefault(a.tech_id, []).append(a)
        if a.locked:
            continue  # locked facts are not re-validated against future constraints
        committed = snap.assignments.get(oid)
        if committed is not None and _same_timing(committed, a):
            continue  # unchanged committed assignment: an existing fact, validated when it was committed
        # 1. skill
        if not tech.qualified(order.trade_type, order.required_level):
            res.add("skill", f"{tech.id} lacks {order.trade_type} level {order.required_level}", order_id=oid,
                    technician_id=tech.id, have=tech.skills.get(order.trade_type, 0), need=order.required_level)
        # timing identity
        travel = snap.travel(a.origin_location_id, order.location_id)
        if travel is None:
            res.add("unreachable", f"{a.origin_location_id} -> {order.location_id} unreachable", order_id=oid)
        elif a.travel != travel or a.arrival != a.departure + travel:
            res.add("travel_mismatch", "arrival != departure + travel", order_id=oid, expected=travel, got=a.travel)
        if a.service_end != a.service_start + order.duration:
            res.add("duration_mismatch", "service_end != service_start + catalog duration", order_id=oid)
        if a.service_start < a.arrival:
            res.add("start_before_arrival", "service starts before arrival", order_id=oid)
        # 4. window (start must be within [window_start, window_end]); recovery targets only need start >= now
        if order.recovery_target:
            if a.service_start < snap.now:
                res.add("recovery_in_past", "recovery start before now", order_id=oid)
        else:
            if a.service_start < order.window_start:
                res.add("window_early", "service starts before window_start", order_id=oid)
            if a.service_start > order.window_end:
                res.add("window_late", "service starts after window_end", order_id=oid,
                        service_start=a.service_start, window_end=order.window_end)
        # 9. not in the past
        if a.departure < snap.now:
            res.add("in_past", "departure before now", order_id=oid)
        # 5. shift
        if a.departure < tech.shift_start:
            res.add("before_shift", "departure before shift start", order_id=oid, technician_id=tech.id)
        if a.service_end > tech.shift_end:
            res.add("after_shift", "service ends after shift end", order_id=oid, technician_id=tech.id,
                    service_end=a.service_end, shift_end=tech.shift_end)
        # 3. breaks / unavailability (travel and service blocks)
        for b in tech.blocked:
            if a.travel > 0 and overlaps((a.departure, a.arrival), b):
                res.add("travel_in_break", "travel overlaps break/unavailability", order_id=oid, technician_id=tech.id)
            if overlaps((a.service_start, a.service_end), b):
                res.add("service_in_break", "service overlaps break/unavailability", order_id=oid, technician_id=tech.id)

    # 6. per-technician sequence: no overlap, chain origin/time consistency
    for tid, items in per_tech.items():
        items.sort(key=lambda x: (x.departure, x.service_start))
        loc, t = snap.anchor(tid)
        prev: Assign | None = None
        for a in items:
            if prev is not None and a.departure < prev.service_end:
                res.add("overlap", f"{a.order_id} departs before {prev.order_id} ends", technician_id=tid,
                        order_id=a.order_id)
            if not a.locked:
                expected_loc = snap.orders[prev.order_id].location_id if prev is not None else loc
                if a.origin_location_id != expected_loc:
                    res.add("origin_mismatch", f"{a.order_id} origin {a.origin_location_id} != predecessor {expected_loc}",
                            technician_id=tid, order_id=a.order_id)
                if prev is None and a.departure < t:
                    res.add("before_anchor", f"{a.order_id} departs before anchor time", technician_id=tid, order_id=a.order_id)
            prev = a
    return res
