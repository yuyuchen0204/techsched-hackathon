"""Match score (docs/scoring.md). Score is a preference, not a success probability.

Each component is normalized to [0,1], weighted, and scaled to 0-100. Raw quantities are kept for the UI.
"""
from __future__ import annotations

from typing import Any

from app.config import Policy
from app.scheduling.domain import Assign, OrderSpec, Snapshot, TechSpec

MAX_LEVEL = 5


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def skill_fit(tech: TechSpec, order: OrderSpec) -> tuple[float, dict[str, Any]]:
    level = tech.skills.get(order.trade_type, 0)
    if level < order.required_level:
        return 0.0, {"level": level, "required": order.required_level, "qualified": False}
    if order.required_level >= MAX_LEVEL:
        fit = 1.0
    else:
        fit = 0.7 + 0.3 * (level - order.required_level) / (MAX_LEVEL - order.required_level)
    return clamp01(fit), {"level": level, "required": order.required_level, "qualified": True}


def travel_component(travel_minutes: int, policy: Policy) -> float:
    return clamp01(1.0 - travel_minutes / max(1, policy.scales.travel_full_penalty_minutes))


def response_component(snap: Snapshot, order: OrderSpec, a: Assign, policy: Policy) -> tuple[float, dict[str, Any]]:
    reference = snap.now if order.recovery_target else max(snap.now, order.window_start)
    wait = max(0, a.service_start - reference)
    return clamp01(1.0 - wait / max(1, policy.scales.response_full_penalty_minutes)), {"reference": reference, "wait_minutes": wait}


def workload_component(snap: Snapshot, tech: TechSpec, plan: dict[str, Assign], policy: Policy) -> tuple[float, dict[str, Any]]:
    planned = 0
    for x in plan.values():
        if x.tech_id == tech.id and not x.locked:
            planned += (x.service_end - x.departure)
    # locked (already departed / ongoing) work counts once as actual, not again as planned
    actual = tech.actual_worked_minutes
    for x in snap.assignments.values():
        if x.tech_id == tech.id and x.locked:
            actual += x.service_end - x.departure
    shift_len = max(1, tech.shift_end - tech.shift_start)
    occupancy = actual + planned
    return clamp01(1.0 - occupancy / shift_len), {"actual_minutes": actual, "planned_minutes": planned, "shift_minutes": shift_len}


def stability_component(affected_count: int, own_shift_minutes: int, policy: Policy, own_tech_changed: bool = False) -> float:
    """1.0 = nothing else moves. Plan-level disruption (affected count) and this assignment's own change both count."""
    a = affected_count / max(1, policy.scales.affected_reference_count)
    s = own_shift_minutes / max(1, policy.scales.shift_full_penalty_minutes)
    own = max(clamp01(s), 0.5 if own_tech_changed else 0.0)
    return clamp01(1.0 - (0.5 * clamp01(a) + 0.5 * own))


def score_assignment(
    snap: Snapshot, a: Assign, plan: dict[str, Assign], policy: Policy, affected_count: int, own_shift_minutes: int
) -> tuple[float, dict[str, Any]]:
    order = snap.orders[a.order_id]
    tech = snap.techs[a.tech_id]
    w = policy.weights
    sf, sf_ctx = skill_fit(tech, order)
    tr = travel_component(a.travel, policy)
    rs, rs_ctx = response_component(snap, order, a, policy)
    wl, wl_ctx = workload_component(snap, tech, plan, policy)
    base = snap.assignments.get(a.order_id)
    own_tech_changed = base is not None and base.tech_id != a.tech_id
    st = stability_component(affected_count, own_shift_minutes, policy, own_tech_changed)
    total = 100.0 * (w.skill_fit * sf + w.travel * tr + w.response * rs + w.workload * wl + w.stability * st)
    components = {
        "skill_fit": {"value": round(sf, 4), "weight": w.skill_fit, **sf_ctx},
        "travel": {"value": round(tr, 4), "weight": w.travel, "travel_minutes": a.travel},
        "response": {"value": round(rs, 4), "weight": w.response, **rs_ctx},
        "workload": {"value": round(wl, 4), "weight": w.workload, **wl_ctx},
        "stability": {"value": round(st, 4), "weight": w.stability, "affected_count": affected_count,
                      "own_shift_minutes": own_shift_minutes, "own_technician_changed": own_tech_changed},
        "total": round(total, 4),
    }
    return total, components
