"""Offline evaluation: Nearest-Feasible baseline vs Proposed (scored + authority-bounded repair).

Both strategies see the same committed base plan, events, clock, catalog and fixture matrix. Runs in memory on
synthetic scenarios built from the REAL catalog and the fixture locations; results are saved as JSON + CSV.
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import REPO_ROOT, Policy, get_policy, get_settings
from app.models.enums import Priority
from app.providers.route.base import LocationPoint
from app.providers.route.factory import fixture_provider
from app.scheduling.domain import Assign, OrderSpec, Snapshot, TechSpec
from app.scheduling.policy import decide
from app.scheduling.simulator import simulate_route
from app.scheduling.solver import (
    Candidate,
    evaluate_plan,
    pinned_starts,
    qualified_techs,
    solve_initial,
    solve_insert,
)
from app.scheduling.validator import validate_plan
from app.services.catalog_importer import import_catalog_file
from app.services.demo_service import load_scenario

EVAL_DIR = REPO_ROOT / "data" / "evaluation"


def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def build_world(seed: int, n_orders: int, catalog: list[Any], locations: list[LocationPoint], techs_raw: list[dict[str, Any]],
                now: int = 510) -> Snapshot:
    rng = random.Random(seed)
    techs: dict[str, TechSpec] = {}
    for t in techs_raw:
        techs[t["id"]] = TechSpec(id=t["id"], name=t["name"], skills=dict(t["skills"]), shift_start=_hhmm_to_min(t["shift"][0]),
                                  shift_end=_hhmm_to_min(t["shift"][1]), breaks=tuple((_hhmm_to_min(a), _hhmm_to_min(b)) for a, b in t.get("breaks", [])),
                                  unavailable=(), home_location_id=t["home"], current_location_id=t["home"], status="AVAILABLE", version=1)
    orders: dict[str, OrderSpec] = {}
    trades_covered = {tr for t in techs_raw for tr in t["skills"]}
    usable = [c for c in catalog if c.trade_type in trades_covered]
    for i in range(n_orders):
        c = rng.choice(usable)
        ws = rng.choice(range(540, 16 * 60 + 1, 30))
        we = ws + rng.choice([60, 90, 120])
        oid = f"ev_{seed}_{i:02d}"
        orders[oid] = OrderSpec(id=oid, trade_type=c.trade_type, required_level=c.complexity_level, duration=c.repair_duration_minutes,
                                window_start=ws, window_end=we, location_id=rng.choice(locations).id, priority=Priority.P3,
                                lifecycle_status="OPEN", version=1)
    matrix = fixture_provider().travel_matrix(locations)
    return Snapshot(now=now, orders=orders, techs=techs, assignments={}, matrix=matrix, schedule_version=1, scenario_generation=0,
                    location_names={l.id: l.name for l in locations})


def nearest_feasible(snap: Snapshot, target: OrderSpec, policy: Policy) -> Candidate | None:
    """Baseline: nearest (smallest inbound travel) zero-disturbance feasible insertion; no repair moves, no scoring."""
    pins = pinned_starts(snap)
    pins.pop(target.id, None)
    base = {k: v for k, v in snap.assignments.items() if k != target.id}
    best: tuple[tuple[int, int], Candidate] | None = None
    for tech in qualified_techs(snap, target):
        seq = [a.order_id for a in snap.movable_route(tech.id, base)]
        for pos in range(len(seq) + 1):
            rr = simulate_route(snap, tech, seq[:pos] + [target.id] + seq[pos:], pins)
            if not rr.feasible:
                continue
            plan = {oid: a for oid, a in base.items() if a.tech_id != tech.id}
            for a in snap.locked_prefix(tech.id):
                plan[a.order_id] = a
            for a in rr.assignments:
                plan[a.order_id] = a
            cand = evaluate_plan(snap, plan, {target.id}, target.priority, policy)
            if not cand.validation.ok or cand.affected.count > 0:
                continue
            key = (cand.plan[target.id].travel, cand.plan[target.id].service_start)
            if best is None or key < best[0]:
                best = (key, cand)
    return best[1] if best else None


@dataclass
class EventResult:
    strategy: str
    event: str
    target: str
    priority: str
    status: str
    assigned: bool
    on_window: bool | None
    response_minutes: int | None
    affected: int
    tech_changes: int
    shift_minutes: int
    travel_delta: int
    decision: str
    decision_score: float | None
    solve_ms: int
    violations: int

    def row(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _apply(snap: Snapshot, cand: Candidate) -> Snapshot:
    return Snapshot(now=snap.now, orders=snap.orders, techs=snap.techs, assignments=dict(cand.plan), matrix=snap.matrix,
                    schedule_version=snap.schedule_version + 1, scenario_generation=0, location_names=snap.location_names)


def _advance(snap: Snapshot, now: int) -> Snapshot:
    """Simulate execution up to `now`: departed tasks become locked, finished tasks complete (as the live clock does)."""
    orders = dict(snap.orders)
    techs = dict(snap.techs)
    assignments: dict[str, Assign] = {}
    for oid, a in snap.assignments.items():
        o = orders[oid]
        if a.service_end <= now:
            orders[oid] = OrderSpec(**{**o.__dict__, "lifecycle_status": "COMPLETED"})
            t = techs[a.tech_id]
            techs[a.tech_id] = TechSpec(**{**t.__dict__, "current_location_id": o.location_id,
                                           "actual_worked_minutes": t.actual_worked_minutes + (a.service_end - a.departure)})
            continue
        if a.departure <= now:
            status = "IN_PROGRESS" if a.service_start <= now else "EN_ROUTE"
            orders[oid] = OrderSpec(**{**o.__dict__, "lifecycle_status": status})
            assignments[oid] = Assign(**{**a.__dict__, "locked": True})
        else:
            assignments[oid] = a
    return Snapshot(now=now, orders=orders, techs=techs, assignments=assignments, matrix=snap.matrix,
                    schedule_version=snap.schedule_version, scenario_generation=0, location_names=snap.location_names)


def run_strategy(strategy: str, base: Snapshot, events: list[dict[str, Any]], policy: Policy) -> tuple[list[EventResult], Snapshot]:
    snap = base
    out: list[EventResult] = []
    for ev in events:
        target = ev["order"]
        snap = _advance(snap, ev["now"])
        if target is not None:
            snap.orders[target.id] = target
        if ev["type"] == "tech_cancel":
            # facts first: technician unavailable from now; release its undeparted assignments
            t = snap.techs[ev["technician"]]
            snap.techs[t.id] = TechSpec(**{**t.__dict__, "unavailable": ((ev["now"], t.shift_end),), "version": t.version + 1})
            for oid, a in list(snap.assignments.items()):
                if a.tech_id == t.id and not a.locked:
                    del snap.assignments[oid]
            continue
        t0 = time.monotonic()
        if strategy == "baseline":
            cand = nearest_feasible(snap, target, policy)
            status = "feasible" if cand else "no_solution_found"
            decision = "assign" if cand else "unresolved"
        else:
            res = solve_insert(snap, target, policy)
            cand = max(res.candidates, key=lambda c: (c.decision_score or 0, -c.target_start)) if res.candidates else None
            status = res.status.value
            decision = "unresolved"
            if cand:
                decision = decide(target_priority=target.priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                                  authority=cand.authority, policy=policy, has_changes=True).decision.value
        ms = int((time.monotonic() - t0) * 1000)
        violations = 0
        if cand:
            v = validate_plan(snap, cand.plan)
            violations = len(v.violations) + (0 if cand.authority.ok else 1)
            a = cand.plan[target.id]
            ref = target.window_start if not target.recovery_target else snap.now
            out.append(EventResult(strategy, ev["type"], target.id, target.priority.value, status, True,
                                   a.service_start <= target.window_end, a.service_start - max(snap.now, ref),
                                   cand.affected.count, cand.affected.technician_changes, cand.affected.total_shift_minutes,
                                   sum(x.travel for x in cand.plan.values()) - sum(x.travel for x in snap.assignments.values()),
                                   decision, cand.decision_score, ms, violations))
            if decision in ("auto", "assign", "manual"):  # manual plans are counted as approved in the offline harness
                snap = _apply(snap, cand)
        else:
            out.append(EventResult(strategy, ev["type"], target.id, target.priority.value, status, False, False, None, 0, 0, 0, 0,
                                   decision, None, ms, 0))
    return out, snap


def make_events(seed: int, snap: Snapshot, catalog: list[Any], locations: list[LocationPoint]) -> list[dict[str, Any]]:
    rng = random.Random(seed * 7919)
    trades = {tr for t in snap.techs.values() for tr in t.skills}
    usable = [c for c in catalog if c.trade_type in trades]
    events: list[dict[str, Any]] = []
    now = snap.now + 15
    for i in range(3):  # P3 inserts
        c = rng.choice(usable)
        ws = rng.choice(range(now + 60, 16 * 60, 30))
        events.append({"type": "p3_insert", "now": now, "order": OrderSpec(f"new_{seed}_{i}", c.trade_type, c.complexity_level, c.repair_duration_minutes,
                                                                            ws, ws + 90, rng.choice(locations).id, Priority.P3, "OPEN", 1)})
    c = rng.choice(usable)  # paid P1 with a tight window soon
    ws = now + 45
    events.append({"type": "paid_p1", "now": now + 5, "order": OrderSpec(f"p1_{seed}", c.trade_type, c.complexity_level, c.repair_duration_minutes,
                                                                      ws, ws + 30, rng.choice(locations).id, Priority.P1, "OPEN", 1, paid_expedite=True)})
    # technician cancel at now+30: released orders become recovery targets with cancellation priority
    tid = rng.choice(sorted(snap.techs))
    events.append({"type": "tech_cancel", "now": now + 30, "technician": tid, "order": None})
    return events


def expand_cancel(events: list[dict[str, Any]], snap_after_base: Snapshot, policy: Policy) -> list[dict[str, Any]]:
    """Turn the tech_cancel marker into per-order recovery events (same order for both strategies)."""
    out: list[dict[str, Any]] = []
    for ev in events:
        out.append(ev)
        if ev["type"] == "tech_cancel":
            tid, now = ev["technician"], ev["now"]
            released = [a for a in snap_after_base.assignments.values() if a.tech_id == tid and a.departure > now]
            from app.scheduling.priority import cancellation_priority
            for a in sorted(released, key=lambda x: x.service_start):
                o = snap_after_base.orders[a.order_id]
                pr = Priority.most_urgent(o.priority, cancellation_priority(o.window_end - now, policy))
                out.append({"type": "tech_cancel_recovery", "now": now, "order": OrderSpec(**{**o.__dict__, "priority": pr, "version": o.version + 1})})
    return out


def summarize(rows: list[EventResult]) -> dict[str, Any]:
    by: dict[str, list[EventResult]] = {}
    for r in rows:
        by.setdefault(r.strategy, []).append(r)
    summary: dict[str, Any] = {}
    for strat, rs in by.items():
        n = len(rs)
        assigned = [r for r in rs if r.assigned]
        urgent = [r for r in rs if r.priority in ("P0", "P1")]
        resp = [r.response_minutes for r in urgent if r.assigned and r.response_minutes is not None]
        summary[strat] = {
            "events": n, "assignment_rate": round(len(assigned) / n, 3) if n else None,
            "unassigned_by_priority": {p: len([r for r in rs if not r.assigned and r.priority == p]) for p in ("P0", "P1", "P2", "P3")},
            "on_window_rate": round(len([r for r in assigned if r.on_window]) / n, 3) if n else None,  # unassigned counted as missed
            "urgent_events": len(urgent), "urgent_unserved": len([r for r in urgent if not r.assigned]),
            "urgent_response_minutes_mean": round(statistics.mean(resp), 1) if resp else None,
            "travel_delta_total": sum(r.travel_delta for r in assigned),
            "affected_total": sum(r.affected for r in assigned), "technician_changes_total": sum(r.tech_changes for r in assigned),
            "shift_minutes_total": sum(r.shift_minutes for r in assigned),
            "hard_or_authority_violations": sum(r.violations for r in rs),
            "solve_ms_mean": round(statistics.mean(r.solve_ms for r in rs), 1) if rs else None,
            "solve_ms_max": max((r.solve_ms for r in rs), default=0),
            "decisions": {d: len([r for r in rs if r.decision == d]) for d in sorted({r.decision for r in rs})},
        }
    return summary


def run_evaluation(seeds: list[int] | None = None, n_orders: int = 18, scenario: str = "main") -> dict[str, Any]:
    policy = get_policy()
    seeds = seeds or list(range(1, 11))
    catalog = import_catalog_file(get_settings().catalog_path).items
    if not catalog:
        raise RuntimeError("catalog not configured")
    raw = json.loads((REPO_ROOT / "data" / "fixtures" / "locations.json").read_text())["locations"]
    locations = [LocationPoint(l["id"], l["name"], l["lat"], l["lon"]) for l in raw]
    techs_raw = load_scenario(scenario)["technicians"]
    all_rows: list[EventResult] = []
    per_seed: list[dict[str, Any]] = []
    for seed in seeds:
        world = build_world(seed, n_orders, catalog, locations, techs_raw)
        t0 = time.monotonic()
        init = solve_initial(world, list(world.orders), policy)
        init_ms = int((time.monotonic() - t0) * 1000)
        base = _apply(world, init.candidates[0]) if init.candidates else world
        events = expand_cancel(make_events(seed, base, catalog, locations), base, policy)
        seed_rows: list[EventResult] = []
        for strat in ("baseline", "proposed"):
            rows, _ = run_strategy(strat, base, events, policy)
            seed_rows.extend(rows)
        all_rows.extend(seed_rows)
        per_seed.append({"seed": seed, "orders": n_orders, "initial_status": init.status.value, "initial_unassigned": init.unassigned,
                         "initial_ms": init_ms, "events": len([e for e in events if e["type"] != "tech_cancel"]),
                         "summary": summarize(seed_rows)})
    result = {"generated_at": datetime.now(UTC).isoformat(), "seeds": seeds, "orders_per_seed": n_orders,
              "policy_version": policy.policy_version, "route_provider": "fixture", "summary": summarize(all_rows), "per_seed": per_seed,
              "notes": ["baseline = nearest feasible zero-disturbance insertion (no repair moves, no scoring)",
                        "proposed = scored candidates with authority-bounded repair; manual plans counted as approved offline",
                        "unassigned events count as missed in on_window_rate; response time only over served urgent events",
                        "synthetic scenarios; no claim of real-world savings"]}
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (EVAL_DIR / f"eval_{stamp}.json").write_text(json.dumps(result, indent=1))
    with (EVAL_DIR / f"eval_{stamp}_events.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].row().keys()) if all_rows else ["strategy"])
        w.writeheader()
        for r in all_rows:
            w.writerow(r.row())
    (EVAL_DIR / "latest.json").write_text(json.dumps(result, indent=1))
    result["files"] = [str(EVAL_DIR / f"eval_{stamp}.json"), str(EVAL_DIR / f"eval_{stamp}_events.csv")]
    return result


# ====================================================================== V3 (§18.4)
# Same worlds, policy, solver, events and clock as above. Only the decision orchestration changes:
#   fast_path  = V2 pipeline (insertion → bounded repair → PolicyEngine), unresolved stays unresolved
#   agent      = fast path, then the bounded investigation the agent runtime performs on UNRESOLVED (≤3 searches):
#                zero-disturbance trials over alternative 90-minute windows later the same day; the earliest feasible one is
#                taken as if the customer accepted it (an explicit assumption, reported separately as "served_in_alt_window");
#                nothing feasible → human escalation (counted, never hidden).
# Rest: fixed_lunch = every technician unavailable 12:00–13:00 (V2); dynamic = no fixed block, rest feasibility measured
# afterwards as an idle gap ≥ 30 min between 180 and 240 cumulative work minutes (the V3 thresholds).


def _alt_window_search(snap: Snapshot, target: OrderSpec, policy: Policy, max_searches: int) -> tuple[Candidate | None, int, OrderSpec | None]:
    """Agent branch: try later 90-min windows (30-min steps) with zero-disturbance insertion; bounded by the search budget."""
    searches = 0
    start = max(snap.now + 30, target.window_end)
    for ws in range(start - start % 30, 17 * 60, 30):
        if searches >= max_searches:
            break
        alt = OrderSpec(**{**target.__dict__, "window_start": ws, "window_end": ws + 90, "recovery_target": False})
        snap.orders[alt.id] = alt
        res = solve_insert(snap, alt, policy, allow_relocate=False, budget_ms=300)
        searches += 1
        if res.candidates:
            cand = max(res.candidates, key=lambda c: (c.decision_score or 0, -c.target_start))
            return cand, searches, alt
    snap.orders[target.id] = target
    return None, searches, None


def _rest_feasibility(snap: Snapshot, policy: Policy) -> dict[str, Any]:
    """Offline proxy for dynamic rest: does each technician have a ≥30-min idle gap once cumulative work reaches 180 min
    and before it reaches 240 min? (work = travel + service, idle gaps do not count)."""
    b = policy.breaks
    ok, escalate, not_needed = 0, 0, 0
    for tid in snap.techs:
        route = snap.route_of(tid)
        acc, found, needed = 0, False, False
        for i, a in enumerate(route):
            acc += a.service_end - a.departure
            if acc >= b.evaluate_after_work_minutes:
                needed = True
                nxt = route[i + 1].departure if i + 1 < len(route) else snap.techs[tid].shift_end
                if nxt - a.service_end >= b.default_break_minutes:
                    found = True
                    break
                if acc >= b.escalate_after_work_minutes:
                    break
        if not needed:
            not_needed += 1
        elif found:
            ok += 1
        else:
            escalate += 1
    return {"technicians": len(snap.techs), "no_rest_needed": not_needed, "rest_slot_found": ok, "rest_escalations": escalate}


def _workload(snap: Snapshot) -> dict[str, Any]:
    util = []
    for tid, t in snap.techs.items():
        busy = sum(a.service_end - a.departure for a in snap.route_of(tid))
        util.append(busy / max(1, t.shift_end - t.shift_start))
    return {"utilisation_mean": round(statistics.mean(util), 3) if util else None, "utilisation_stdev": round(statistics.pstdev(util), 3) if util else None,
            "travel_total": sum(a.travel for a in snap.assignments.values())}


def run_v3_evaluation(seeds: list[int] | None = None, scenarios: list[str] | None = None) -> dict[str, Any]:
    policy = get_policy()
    seeds = seeds or list(range(1, 11))
    scenarios = scenarios or ["main", "relaxed", "scarce"]
    catalog = import_catalog_file(get_settings().catalog_path).items
    if not catalog:
        raise RuntimeError("catalog not configured")
    raw = json.loads((REPO_ROOT / "data" / "fixtures" / "locations.json").read_text())["locations"]
    locations = [LocationPoint(l["id"], l["name"], l["lat"], l["lon"]) for l in raw]
    budget = policy.agent
    out_scen: dict[str, Any] = {}
    for scen in scenarios:
        sc = load_scenario(scen)
        techs_raw = sc["technicians"]
        n_orders = max(8, len(sc["orders"]))
        strat_rows: dict[str, list[dict[str, Any]]] = {"fast_path": [], "agent": []}
        rest: dict[str, list[dict[str, Any]]] = {"fixed_lunch": [], "dynamic": []}
        for seed in seeds:
            for rest_mode in ("fixed_lunch", "dynamic"):
                traw = [{**t, "breaks": [["12:00", "13:00"]] if rest_mode == "fixed_lunch" else []} for t in techs_raw]
                world = build_world(seed, n_orders, catalog, locations, traw)
                for t in traw:  # scenario leave (scarce): technician unavailable for the day
                    if t.get("leave"):
                        ts = world.techs[t["id"]]
                        world.techs[t["id"]] = TechSpec(**{**ts.__dict__, "unavailable": ((_hhmm_to_min(t["leave"][0]), _hhmm_to_min(t["leave"][1])),)})
                init = solve_initial(world, list(world.orders), policy)
                base = _apply(world, init.candidates[0]) if init.candidates else world
                rest[rest_mode].append({"initial_unassigned": len(init.unassigned), **_workload(base), **_rest_feasibility(base, policy)})
                if rest_mode != "dynamic":
                    continue
                events = expand_cancel(make_events(seed, base, catalog, locations), base, policy)
                for strat in ("fast_path", "agent"):
                    rows, _snap = run_strategy("proposed", base, events, policy)
                    tool_calls, searches, alt_served, escalations, agent_ms = 0, 0, 0, 0, 0
                    if strat == "agent":
                        snap = base
                        rows = []
                        for ev in events:
                            target = ev["order"]
                            snap = _advance(snap, ev["now"])
                            if target is not None:
                                snap.orders[target.id] = target
                            if ev["type"] == "tech_cancel":
                                tk = snap.techs[ev["technician"]]
                                snap.techs[tk.id] = TechSpec(**{**tk.__dict__, "unavailable": ((ev["now"], tk.shift_end),), "version": tk.version + 1})
                                for oid, a in list(snap.assignments.items()):
                                    if a.tech_id == tk.id and not a.locked:
                                        del snap.assignments[oid]
                                continue
                            t0 = time.monotonic()
                            res = solve_insert(snap, target, policy)
                            cand = max(res.candidates, key=lambda c: (c.decision_score or 0, -c.target_start)) if res.candidates else None
                            decision = "unresolved"
                            served_alt = False
                            if cand:
                                decision = decide(target_priority=target.priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                                                  authority=cand.authority, policy=policy, has_changes=True).decision.value
                            else:  # agent runtime branch: get_order_context + simulate_insertion already spent → alternative windows
                                tool_calls += 2
                                alt, n_s, alt_spec = _alt_window_search(snap, target, policy, budget.max_plan_searches_per_wakeup - 1)
                                searches += 1 + n_s
                                tool_calls += n_s + 1
                                if alt and alt_spec is not None:
                                    cand, decision, served_alt = alt, "alt_window", True
                                    target = alt_spec
                                else:
                                    escalations += 1
                                    tool_calls += 1  # flag_for_human
                            ms = int((time.monotonic() - t0) * 1000)
                            agent_ms += ms
                            if cand:
                                v = validate_plan(snap, cand.plan)
                                a = cand.plan[target.id]
                                rows.append(EventResult(strat, ev["type"], target.id, target.priority.value, res.status.value, True,
                                                        (not served_alt) and a.service_start <= target.window_end, a.service_start - max(snap.now, target.window_start),
                                                        cand.affected.count, cand.affected.technician_changes, cand.affected.total_shift_minutes,
                                                        sum(x.travel for x in cand.plan.values()) - sum(x.travel for x in snap.assignments.values()),
                                                        decision, cand.decision_score, ms, len(v.violations) + (0 if cand.authority.ok else 1)))
                                if decision in ("auto", "manual", "alt_window"):
                                    snap = _apply(snap, cand)
                                    if served_alt:
                                        alt_served += 1
                            else:
                                rows.append(EventResult(strat, ev["type"], target.id, target.priority.value, res.status.value, False, False, None, 0, 0, 0, 0, decision, None, ms, 0))
                    s = summarize([EventResult(**{**r.__dict__, "strategy": strat}) for r in rows])[strat]
                    s.update({"served_in_alt_window": alt_served, "human_escalations": escalations, "tool_calls": tool_calls, "plan_searches": searches,
                              "processing_ms": agent_ms, "workload": _workload(_snap if strat == "fast_path" else snap)})
                    strat_rows[strat].append(s)

        def agg(rows: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
            return {k: round(statistics.mean(float(r[k]) for r in rows if r.get(k) is not None), 3) if any(r.get(k) is not None for r in rows) else None for k in keys}
        out_scen[scen] = {
            "orders_per_seed": n_orders, "seeds": len(seeds),
            "orchestration": {strat: agg(rs, ["assignment_rate", "on_window_rate", "urgent_unserved", "affected_total", "technician_changes_total",
                                              "hard_or_authority_violations", "served_in_alt_window", "human_escalations", "tool_calls", "plan_searches",
                                              "processing_ms", "solve_ms_max"]) for strat, rs in strat_rows.items()},
            "rest": {mode: agg(rs, ["initial_unassigned", "utilisation_mean", "utilisation_stdev", "travel_total", "no_rest_needed", "rest_slot_found", "rest_escalations"])
                     for mode, rs in rest.items()},
        }
    result = {"generated_at": datetime.now(UTC).isoformat(), "policy_version": policy.policy_version, "route_provider": "fixture",
              "budgets": policy.agent.model_dump(), "scenarios": out_scen,
              "notes": ["fast_path and agent share worlds, events, policy, solver and budgets; only the orchestration of UNRESOLVED differs",
                        "agent: alternative-window acceptance is an ASSUMPTION (customer takes the earliest feasible later window); reported separately",
                        "on_window_rate counts the ORIGINAL window; alt-window service is not counted as on-window",
                        "rest: fixed_lunch = 12:00–13:00 unavailable for all; dynamic = idle-gap proxy for a 30-min rest between 180 and 240 work minutes",
                        "synthetic worlds from the real catalog; per-scenario means over seeds; no generalised performance claim"]}
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (EVAL_DIR / f"eval_v3_{stamp}.json").write_text(json.dumps(result, indent=1))
    (EVAL_DIR / "latest_v3.json").write_text(json.dumps(result, indent=1))
    return result
