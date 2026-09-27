"""ScheduleSimulator: shared timing semantics used by initial scheduling, insertion, repair, baseline and commit.

arrival(j)       = departure(j) + travel(origin(j), destination(j))
service_start(j) = max(arrival(j), window_start(j) [, pinned_start(j)])   (recovery targets: max(arrival, now))
service_end(j)   = service_start(j) + catalog_duration(j)

Travel and service blocks may not overlap breaks/unavailability; waiting may coincide with a break
(the technician rests while waiting — engineering decision, see docs/decisions.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.scheduling.domain import Assign, Interval, OrderSpec, Snapshot, TechSpec, overlaps


@dataclass
class RouteResult:
    assignments: list[Assign]
    feasible: bool
    violations: list[str] = field(default_factory=list)


def _push_past_blocks(start: int, length: int, blocked: tuple[Interval, ...]) -> int:
    """Earliest t >= start such that [t, t+length) does not overlap any blocked interval."""
    t = start
    changed = True
    guard = 0
    while changed and guard < 64:
        changed = False
        guard += 1
        for b in sorted(blocked):
            if overlaps((t, t + length), b):
                t = b[1]
                changed = True
    return t


def _latest_departure(jit: int, length: int, blocked: tuple[Interval, ...], lower_bound: int) -> int:
    """Latest t in [lower_bound, jit] such that [t, t+length) avoids blocked intervals; else lower_bound."""
    t = jit
    guard = 0
    while guard < 64:
        guard += 1
        hit = [b for b in blocked if overlaps((t, t + length), b)]
        if not hit:
            return max(t, lower_bound)
        t = min(b[0] for b in hit) - length
        if t < lower_bound:
            return lower_bound
    return lower_bound


def simulate_route(
    snap: Snapshot,
    tech: TechSpec,
    sequence: list[str],
    pinned_starts: dict[str, int] | None = None,
    anchor: tuple[str, int] | None = None,
) -> RouteResult:
    """Simulate the MOVABLE part of a technician's route from the anchor. Locked tasks are not re-simulated."""
    pinned = pinned_starts or {}
    loc, t = anchor or snap.anchor(tech.id)
    out: list[Assign] = []
    violations: list[str] = []
    blocked = tech.blocked
    for oid in sequence:
        order: OrderSpec = snap.orders[oid]
        travel = snap.travel(loc, order.location_id)
        if travel is None:
            violations.append(f"{oid}: unreachable from {loc}")
            return RouteResult(out, False, violations)
        earliest_dep = max(t, tech.shift_start, snap.now)
        earliest_dep = _push_past_blocks(earliest_dep, travel, blocked) if travel > 0 else earliest_dep
        floor = snap.now if order.recovery_target else order.window_start
        service_start = max(earliest_dep + travel, floor, pinned.get(oid, 0))
        service_start = _push_past_blocks(service_start, order.duration, blocked)
        service_end = service_start + order.duration
        # depart just in time (idle stays at the previous location); if the JIT travel would cross a break,
        # leave before the break and wait at the customer instead
        departure = _latest_departure(service_start - travel, travel, blocked, earliest_dep) if travel > 0 else service_start
        arrival = departure + travel
        out.append(Assign(
            order_id=oid, tech_id=tech.id, origin_location_id=loc, departure=departure, arrival=arrival,
            service_start=service_start, service_end=service_end, travel=travel, waiting=service_start - arrival,
        ))
        loc, t = order.location_id, service_end
    return RouteResult(out, True, violations)


def route_end(snap: Snapshot, tech_id: str, assignments: list[Assign]) -> tuple[str, int]:
    if assignments:
        last = assignments[-1]
        return snap.orders[last.order_id].location_id, last.service_end
    return snap.anchor(tech_id)
