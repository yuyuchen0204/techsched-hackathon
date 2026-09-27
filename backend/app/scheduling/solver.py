"""Solver: feasible insertion + urgent front insertion (cascade) + bounded local search (relocate) under the PolicyEngine
authority rules.

Returns feasible / partial / no_solution_found / timeout / error and never claims global optimality.
Every candidate is a FULL plan (all active assignments after the change), independently validated.

Search stages for one target:
1. direct insertion — every qualified technician × every position of the movable route (successors may shift later);
2. urgent front insertion — only when the priority rule allows moves: the target goes FIRST on the route of the
   technician who can reach the customer soonest from where they are now; successors that no longer fit their window or
   shift are displaced and re-homed on other qualified technicians (this is what "expedite = send whoever can come now"
   relies on; the number of moved orders is bounded only by the rule's `max_affected`, which may be unlimited);
3. bounded local search — relocate / reorder one movable order, then insert the target.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.config import Policy, RescheduleRule
from app.models.enums import Priority, SolveStatus
from app.scheduling.affected import AffectedResult, changed_or_new_ids, compute_affected
from app.scheduling.domain import Assign, OrderSpec, Snapshot, TechSpec
from app.scheduling.policy import AuthorityCheck, check_authority
from app.scheduling.scoring import score_assignment
from app.scheduling.simulator import simulate_route
from app.scheduling.validator import ValidationResult, validate_plan

STRATEGIES = ("faster_response", "less_disruption", "balanced")


@dataclass
class Candidate:
    plan: dict[str, Assign]
    affected: AffectedResult
    authority: AuthorityCheck
    validation: ValidationResult
    decision_score: float | None
    changed_ids: list[str]
    target_ids: set[str]
    strategy: str = ""
    strategy_tags: list[str] = field(default_factory=list)
    move_kind: str = "insert"

    @property
    def target_start(self) -> int:
        starts = [self.plan[t].service_start for t in self.target_ids if t in self.plan]
        return min(starts) if starts else 10**9

    def signature(self) -> tuple[Any, ...]:
        return tuple(sorted(self.plan[o].signature() for o in self.changed_ids))

    @property
    def approvable(self) -> bool:
        return self.validation.ok and self.authority.ok

    def metrics(self, snap: Snapshot) -> dict[str, Any]:
        total_travel = sum(a.travel for a in self.plan.values())
        base_travel = sum(a.travel for a in snap.assignments.values())
        target_assign = [self.plan[t] for t in self.target_ids if t in self.plan]
        return {
            "affected_count": self.affected.count,
            "technician_changes": self.affected.technician_changes,
            "total_shift_minutes": self.affected.total_shift_minutes,
            "total_travel_minutes": total_travel,
            "travel_delta_minutes": total_travel - base_travel,
            "target_service_start": target_assign[0].service_start if target_assign else None,
            "target_travel_minutes": target_assign[0].travel if target_assign else None,
            "target_waiting_minutes": target_assign[0].waiting if target_assign else None,
            "move_kind": self.move_kind,
        }


@dataclass
class SolveResult:
    status: SolveStatus
    candidates: list[Candidate] = field(default_factory=list)
    over_limit: list[Candidate] = field(default_factory=list)
    explored: int = 0
    elapsed_ms: int = 0
    reason: str | None = None
    unassigned: list[str] = field(default_factory=list)
    unassigned_reasons: dict[str, str] = field(default_factory=dict)
    search_incomplete: bool = False


class Budget:
    def __init__(self, ms: int):
        self.deadline = time.monotonic() + ms / 1000.0
        self.start = time.monotonic()
        self.exhausted = False

    def check(self) -> bool:
        if time.monotonic() > self.deadline:
            self.exhausted = True
        return not self.exhausted

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.start) * 1000)


def pinned_starts(snap: Snapshot) -> dict[str, int]:
    return {oid: a.service_start for oid, a in snap.assignments.items()}


def evaluate_plan(snap: Snapshot, plan: dict[str, Assign], target_ids: set[str], target_priority: Priority,
                  policy: Policy, allow_missing: set[str] | None = None, move_kind: str = "insert") -> Candidate:
    validation = validate_plan(snap, plan, allow_missing=allow_missing)
    affected = compute_affected(snap, plan, target_ids)
    authority = check_authority(snap, target_priority, affected, policy, target_ids)
    changed = changed_or_new_ids(snap, plan)
    shift_by_id = {d.order_id: abs(d.shift_minutes) for d in affected.diff}
    scores: list[float] = []
    for oid in changed:
        a = plan[oid]
        score, comps = score_assignment(snap, a, plan, policy, affected.count, shift_by_id.get(oid, 0))
        plan[oid] = a.with_score(score, comps)
        scores.append(score)
    decision_score = min(scores) if scores else None
    return Candidate(plan, affected, authority, validation, decision_score, changed, target_ids, move_kind=move_kind)


def _replace_tech_route(plan: dict[str, Assign], tech_id: str, new_route: list[Assign], keep_locked: list[Assign]) -> dict[str, Assign]:
    out = {oid: a for oid, a in plan.items() if a.tech_id != tech_id}
    for a in keep_locked:
        out[a.order_id] = a
    for a in new_route:
        out[a.order_id] = a
    return out


def qualified_techs(snap: Snapshot, order: OrderSpec) -> list[TechSpec]:
    techs = [t for t in snap.techs.values() if t.qualified(order.trade_type, order.required_level)
             and t.id not in order.excluded_technicians]
    return sorted(techs, key=lambda t: t.id)


def _insertions(snap: Snapshot, base_plan: dict[str, Assign], target: OrderSpec, techs: list[TechSpec],
                pins: dict[str, int], budget: Budget):
    """Yield (tech, new_route_assignments, position) for every feasible simulated insertion."""
    for tech in techs:
        if tech.status == "UNAVAILABLE" and any(s <= snap.now < e for s, e in tech.unavailable):
            pass  # still allowed to plan after the unavailable interval; simulator pushes past blocks
        movable = snap.movable_route(tech.id, base_plan)
        seq = [a.order_id for a in movable]
        for pos in range(len(seq) + 1):
            if not budget.check():
                return
            new_seq = seq[:pos] + [target.id] + seq[pos:]
            rr = simulate_route(snap, tech, new_seq, pins)
            if rr.feasible:
                yield tech, rr.assignments, pos


def _fits(snap: Snapshot, tech: TechSpec, a: Assign) -> bool:
    """Window / shift check used while building a cascade (the validator re-checks the full plan afterwards)."""
    o = snap.orders[a.order_id]
    if a.service_end > tech.shift_end:
        return False
    return a.service_start >= snap.now if o.recovery_target else a.service_start <= o.window_end


def _front_insertions_with_cascade(snap: Snapshot, base_plan: dict[str, Assign], target: OrderSpec, techs: list[TechSpec],
                                   pins: dict[str, int], budget: Budget, rule: RescheduleRule):
    """Yield full plans in which the target is the technician's NEXT job (earliest possible arrival from the anchor).

    Technicians are tried in order of arrival time. Successors pushed out of their window / shift are displaced and
    re-inserted (earliest feasible start) on any qualified technician — on the same one only after the target. Displaced
    orders must be movable under the rule; a cascade that cannot re-home every displaced order yields nothing.
    Routes without displacement are skipped: direct insertion at position 0 already produced that plan."""

    def arrival(t: TechSpec) -> int | None:
        loc, at = snap.anchor(t.id)
        tr = snap.travel(loc, target.location_id)
        return None if tr is None else max(at, snap.now, t.shift_start) + tr

    ordered = sorted((t for t in techs if arrival(t) is not None), key=lambda t: (arrival(t) or 0, t.id))
    for tech in ordered:
        if not budget.check():
            return
        movable = snap.movable_route(tech.id, base_plan)
        if not movable:
            continue
        rr = simulate_route(snap, tech, [target.id] + [a.order_id for a in movable], pins)
        if not rr.feasible:
            continue
        keep = [a.order_id for a in rr.assignments if a.order_id == target.id or _fits(snap, tech, a)]
        displaced = [a.order_id for a in rr.assignments if a.order_id not in keep]
        if not displaced:
            continue
        if any(snap.orders[x].priority.value not in rule.movable_priorities or snap.orders[x].departed for x in displaced):
            continue
        rr2 = simulate_route(snap, tech, keep, pins)
        if not rr2.feasible:
            continue
        urgent_start = next(a.service_start for a in rr2.assignments if a.order_id == target.id)
        plan = _replace_tech_route({oid: a for oid, a in base_plan.items() if oid not in displaced}, tech.id, rr2.assignments,
                                   snap.locked_prefix(tech.id))
        ok = True
        for x in sorted(displaced, key=lambda o: (snap.orders[o].window_end, o)):
            best: tuple[int, TechSpec, list[Assign]] | None = None
            for t2, route2, _pos in _insertions(snap, plan, snap.orders[x], qualified_techs(snap, snap.orders[x]), pins, budget):
                if not all(_fits(snap, t2, a) for a in route2):
                    continue
                if t2.id == tech.id and next(a.service_start for a in route2 if a.order_id == target.id) != urgent_start:
                    continue  # never re-insert a displaced order ahead of the urgent target
                st = next(a.service_start for a in route2 if a.order_id == x)
                if best is None or st < best[0]:
                    best = (st, t2, route2)
            if best is None:
                ok = False
                break
            plan = _replace_tech_route(plan, best[1].id, best[2], snap.locked_prefix(best[1].id))
        if ok:
            yield plan


def solve_insert(snap: Snapshot, target: OrderSpec, policy: Policy, *, budget_ms: int | None = None,
                 base_plan: dict[str, Assign] | None = None, allow_relocate: bool = True) -> SolveResult:
    """Insert one target order (new, unassigned, or recovery target) into the schedule."""
    budget = Budget(budget_ms or policy.repair_budget_ms)
    base_plan = dict(base_plan if base_plan is not None else snap.assignments)
    base_plan.pop(target.id, None)  # target may be re-placed; its old assignment is not a constraint
    pins = pinned_starts(snap)
    pins.pop(target.id, None)
    target_ids = {target.id}
    rule = policy.rule(target.priority.value)
    techs = qualified_techs(snap, target)
    result = SolveResult(status=SolveStatus.NO_SOLUTION_FOUND)
    if not techs:
        result.reason = f"no technician qualified for {target.trade_type} level {target.required_level}"
        result.elapsed_ms = budget.elapsed_ms
        return result
    pool: list[Candidate] = []
    over: list[Candidate] = []
    explored = 0
    infeasible_reasons: dict[str, int] = {}

    def consider(cand: Candidate) -> None:
        nonlocal explored
        explored += 1
        if cand.approvable:
            pool.append(cand)
        elif cand.validation.ok and not rule.within_limit(cand.authority.affected_count) and all(
            snap.orders[o].priority.value in rule.movable_priorities and not snap.orders[o].departed for o in cand.affected.affected_ids
        ):
            over.append(cand)
        else:
            for v in cand.validation.violations:
                infeasible_reasons[v["code"]] = infeasible_reasons.get(v["code"], 0) + 1
            for _msg in cand.authority.violations:
                infeasible_reasons["authority"] = infeasible_reasons.get("authority", 0) + 1

    # 1. direct insertion (zero-disturbance or shifting successors)
    for tech, route, _pos in _insertions(snap, base_plan, target, techs, pins, budget):
        plan = _replace_tech_route(base_plan, tech.id, route, snap.locked_prefix(tech.id))
        consider(evaluate_plan(snap, plan, target_ids, target.priority, policy, move_kind="insert"))
    zero = [c for c in pool if c.affected.count == 0]

    # 2. urgent front insertion with cascade (only if authority allows moves)
    if allow_relocate and rule.allows_moves and budget.check():
        for plan in _front_insertions_with_cascade(snap, base_plan, target, techs, pins, budget, rule):
            consider(evaluate_plan(snap, plan, target_ids, target.priority, policy, move_kind="urgent"))

    # 3. bounded local search: relocate one movable order elsewhere, then insert target (only if authority allows moves)
    if allow_relocate and rule.allows_moves and budget.check() and not (zero and target.priority in (Priority.P3, Priority.P2)):
        relocations = 0
        for tech in techs:
            for x in snap.movable_route(tech.id, base_plan):
                xo = snap.orders[x.order_id]
                if xo.priority.value not in rule.movable_priorities or xo.departed or x.order_id in target_ids:
                    continue
                if relocations >= policy.max_relocate_candidates or not budget.check():
                    break
                relocations += 1
                without_x = {oid: a for oid, a in base_plan.items() if oid != x.order_id}
                for _t, route_a, _pos in _insertions(snap, without_x, target, [tech], pins, budget):
                    plan_a = _replace_tech_route(without_x, tech.id, route_a, snap.locked_prefix(tech.id))
                    other_techs = [t for t in qualified_techs(snap, xo) if t.id != tech.id]
                    for tech_b, route_b, _p in _insertions(snap, plan_a, xo, other_techs, pins, budget):
                        plan_b = _replace_tech_route(plan_a, tech_b.id, route_b, snap.locked_prefix(tech_b.id))
                        consider(evaluate_plan(snap, plan_b, target_ids, target.priority, policy, move_kind="relocate"))
                    # also: X re-inserted later on the same tech (reorder)
                    seq_a = [a.order_id for a in snap.movable_route(tech.id, plan_a)]
                    for pos in range(len(seq_a) + 1):
                        rr = simulate_route(snap, tech, seq_a[:pos] + [x.order_id] + seq_a[pos:], pins)
                        if rr.feasible:
                            plan_c = _replace_tech_route(plan_a, tech.id, rr.assignments, snap.locked_prefix(tech.id))
                            consider(evaluate_plan(snap, plan_c, target_ids, target.priority, policy, move_kind="reorder"))

    result.explored = explored
    result.elapsed_ms = budget.elapsed_ms
    result.candidates = select_strategies(pool)
    result.over_limit = sorted(over, key=lambda c: (c.affected.count, c.target_start))[:1]
    result.search_incomplete = budget.exhausted
    if pool:
        result.status = SolveStatus.TIMEOUT if budget.exhausted else SolveStatus.FEASIBLE
        if budget.exhausted:
            result.reason = "budget exhausted; incumbent candidates returned"
    elif budget.exhausted:
        result.status = SolveStatus.TIMEOUT
        result.reason = "budget exhausted before any feasible candidate was found"
    else:
        result.status = SolveStatus.NO_SOLUTION_FOUND
        parts = [f"{k}×{v}" for k, v in sorted(infeasible_reasons.items(), key=lambda kv: -kv[1])]
        result.reason = "no feasible candidate within authority" + (f" (rejections: {', '.join(parts)})" if parts else "")
        if over:
            best = result.over_limit[0]
            result.reason += f"; a plan exists only by affecting {best.affected.count} orders (> limit {rule.max_affected})"
    return result


def select_strategies(pool: list[Candidate]) -> list[Candidate]:
    """Pick up to three genuinely different plans. Identical plans are merged, never relabelled."""
    if not pool:
        return []
    picks: list[tuple[str, Candidate]] = [
        ("faster_response", min(pool, key=lambda c: (c.target_start, -(c.decision_score or 0), c.affected.count))),
        ("less_disruption", min(pool, key=lambda c: (c.affected.count, c.affected.total_shift_minutes,
                                                     c.affected.technician_changes, -(c.decision_score or 0), c.target_start))),
        ("balanced", max(pool, key=lambda c: ((c.decision_score or 0), -c.target_start, -c.affected.count))),
    ]
    out: list[Candidate] = []
    seen: dict[tuple[Any, ...], Candidate] = {}
    for name, cand in picks:
        sig = cand.signature()
        if sig in seen:
            seen[sig].strategy_tags.append(name)
            continue
        cand.strategy = name
        cand.strategy_tags = [name]
        seen[sig] = cand
        out.append(cand)
    return out


def solve_initial(snap: Snapshot, order_ids: list[str], policy: Policy, *, budget_ms: int | None = None) -> SolveResult:
    """Greedy batch scheduling: higher priority first, then deadline / window urgency, stable id tie-break.

    Committed assignments are pinned; batch placements may still be re-timed by later insertions (not committed yet).
    """
    budget = Budget(budget_ms or policy.initial_budget_ms)
    plan = dict(snap.assignments)
    order = sorted((snap.orders[o] for o in order_ids if o in snap.orders and o not in plan),
                   key=lambda o: (o.priority.rank, o.window_end, o.window_start, o.id))
    pins = pinned_starts(snap)
    result = SolveResult(status=SolveStatus.FEASIBLE)
    placed: list[str] = []
    for o in order:
        if not budget.check():
            result.unassigned.append(o.id)
            result.unassigned_reasons[o.id] = "budget exhausted"
            continue
        best: Candidate | None = None
        techs = qualified_techs(snap, o)
        if not techs:
            result.unassigned.append(o.id)
            result.unassigned_reasons[o.id] = f"no technician qualified for {o.trade_type} level {o.required_level}"
            continue
        for tech, route, _pos in _insertions(snap, plan, o, techs, pins, budget):
            cand_plan = _replace_tech_route(plan, tech.id, route, snap.locked_prefix(tech.id))
            cand = evaluate_plan(snap, cand_plan, set(placed) | {o.id}, o.priority, policy,
                                 allow_missing=set(result.unassigned), move_kind="initial")
            if not cand.validation.ok:
                continue
            # committed orders must not move at all during initial batch (authority for batch = zero disturbance)
            if cand.affected.count > 0:
                continue
            own = cand.plan[o.id].match_score or 0.0
            if best is None or (own, -cand.plan[o.id].service_start) > ((best.plan[o.id].match_score or 0.0), -best.plan[o.id].service_start):
                best = cand
        if best is None:
            result.unassigned.append(o.id)
            result.unassigned_reasons[o.id] = "no feasible slot for any qualified technician"
            continue
        plan = best.plan
        placed.append(o.id)
    final = evaluate_plan(snap, plan, set(placed), Priority.P3, policy, allow_missing=set(result.unassigned), move_kind="initial")
    final.strategy = "initial"
    final.strategy_tags = ["initial"]
    result.candidates = [final] if placed else []
    result.explored = len(placed)
    result.elapsed_ms = budget.elapsed_ms
    result.search_incomplete = budget.exhausted
    if not placed:
        result.status = SolveStatus.NO_SOLUTION_FOUND
        result.reason = "no order could be placed"
    elif result.unassigned:
        result.status = SolveStatus.PARTIAL
        result.reason = f"{len(result.unassigned)} order(s) could not be placed"
    if budget.exhausted:
        result.status = SolveStatus.TIMEOUT
    return result
