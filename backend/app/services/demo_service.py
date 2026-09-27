"""Scenario seeding and explicit demo reset. Synthetic technicians/customers built on the REAL catalog."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_policy
from app.models.entities import (
    AgentRun,
    Approval,
    Assignment,
    CandidatePlan,
    ChatSession,
    Evaluation,
    InboundEvent,
    Location,
    Notification,
    RiskEvent,
    ScheduleVersion,
    StandbyCandidate,
    Technician,
    WorkOrder,
)
from app.models.enums import SchedulingStatus, TechnicianStatus
from app.scheduling.domain import Assign, Snapshot
from app.scheduling.simulator import simulate_route
from app.scheduling.solver import solve_initial
from app.scheduling.validator import validate_plan
from app.services import catalog_service, order_service
from app.services.catalog_importer import make_catalog_item_id
from app.services.clock import Clock, get_clock, reset_state
from app.services.ids import new_id, next_order_id
from app.services.schedule_service import commit_plan
from app.services.snapshot import build_snapshot, get_matrix
from app.services.timeutil import local_time_utc

SCENARIO_DIR = REPO_ROOT / "data" / "scenarios"
FIXTURE_DIR = REPO_ROOT / "data" / "fixtures"


def _hhmm(clock: Clock, s: str) -> datetime:
    h, m = s.split(":")
    from app.services.timeutil import to_local
    day = to_local(clock.day_origin).date()  # type: ignore[union-attr]
    return local_time_utc(day, int(h), int(m))


def load_scenario(name: str) -> dict[str, Any]:
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"scenario {name} not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_locations(db: Session) -> None:
    raw = json.loads((FIXTURE_DIR / "locations.json").read_text(encoding="utf-8"))
    existing = {l.id for l in db.scalars(select(Location)).all()}
    for loc in raw["locations"]:
        if loc["id"] not in existing:
            db.add(Location(id=loc["id"], name=loc["name"], lat=loc["lat"], lon=loc["lon"], area=loc.get("area")))
    db.flush()


def clear_scenario_data(db: Session) -> None:
    from app.models.entities import (
        AgentTask,
        BreakBlock,
        Customer,
        CustomerAddress,
        CustomerFeedback,
        CustomerQuestion,
        DurationObservation,
        ExecutionEvent,
        HumanCase,
        RescheduleRequest,
        SafetyIncident,
        ServiceReport,
        ToolTrace,
    )
    for model in (Assignment, ScheduleVersion, RiskEvent, StandbyCandidate, CandidatePlan, Approval, AgentRun,
                  Notification, ChatSession, InboundEvent, WorkOrder, Technician, Evaluation,
                  AgentTask, ToolTrace, BreakBlock, CustomerAddress, CustomerFeedback, CustomerQuestion, DurationObservation,
                  ExecutionEvent, HumanCase, RescheduleRequest, SafetyIncident, ServiceReport, Customer):
        db.execute(delete(model))
    db.execute(delete(Location).where(Location.area == "custom"))  # map-picked points belong to the old scenario
    db.flush()


def seed_scenario(db: Session, name: str = "main", *, seed: int | None = None) -> dict[str, Any]:
    scenario = load_scenario(name)
    ensure_locations(db)
    if not catalog_service.list_active(db):
        catalog_service.reload_catalog(db)
    if not catalog_service.list_active(db):
        raise RuntimeError("Repair problem catalog is not configured; cannot seed orders from it")
    clear_scenario_data(db)
    start_h, start_m = scenario.get("sim_start", "08:30").split(":")
    clock = reset_state(db, name, seed if seed is not None else int(scenario.get("seed", 42)), int(start_h), int(start_m))
    gen = clock.scenario_generation
    loc_by_id = {l.id: l for l in db.scalars(select(Location)).all()}

    for t in scenario["technicians"]:
        home = loc_by_id[t["home"]]
        db.add(Technician(
            id=t["id"], scenario_generation=gen, name=t["name"], skills=t["skills"], certifications=t.get("certifications", []),
            home_location_id=home.id, lat=home.lat, lon=home.lon, current_location_id=home.id,
            shift_start=_hhmm(clock, t["shift"][0]), shift_end=_hhmm(clock, t["shift"][1]),
            breaks=[],  # V3: no uniform fixed lunch — rest is arranged dynamically (BreakBlock); scenario 'breaks' keys are ignored
            unavailable_intervals=[{"start": _hhmm(clock, t["leave"][0]).isoformat() + "Z", "end": _hhmm(clock, t["leave"][1]).isoformat() + "Z",
                                    "reason": "scenario: on leave"}] if t.get("leave") else [],
            status=TechnicianStatus.UNAVAILABLE if t.get("leave") else TechnicianStatus.AVAILABLE, version=1, sim_mode="auto", demo_login=t["id"],
        ))
    db.flush()

    executions: list[tuple[str, dict[str, Any]]] = []
    for o in scenario["orders"]:
        item_id = make_catalog_item_id(o["trade"], o["problem"])
        if catalog_service.get_item(db, item_id) is None:
            raise RuntimeError(f"scenario order {o['id']} references catalog entry not in the real catalog: {o['trade']} / {o['problem']}")
        order_service.create_order(
            db, clock, customer_ref=f"seed:{o['id']}", customer_name=o["customer"], contact_phone=o["phone"],
            description=o.get("description", ""), location_id=o["location"], catalog_item_id=item_id,
            window_start=_hhmm(clock, o["window"][0]), window_end=_hhmm(clock, o["window"][1]),
            paid_expedite=bool(o.get("paid", False)), order_id=o["id"],
        )
        if o.get("execution"):
            executions.append((o["id"], o["execution"]))
    db.flush()

    get_matrix(db, force=True)
    # locked, already-departed tasks: simulate from the technician's home at the actual departure time
    snap = build_snapshot(db, clock)
    locked_plan: dict[str, Assign] = {}
    for oid, ex in executions:
        tech = snap.techs[ex["technician"]]
        dep = clock.to_minutes(_hhmm(clock, ex["departed_at"]))
        tmp = Snapshot(now=dep, orders=snap.orders, techs=snap.techs, assignments={}, matrix=snap.matrix,
                       schedule_version=0, scenario_generation=gen)
        rr = simulate_route(tmp, tech, [oid], anchor=(tech.home_location_id, dep))
        if not rr.feasible:
            raise RuntimeError(f"scenario execution for {oid} infeasible: {rr.violations}")
        a = rr.assignments[0]
        a = Assign(**{**a.__dict__, "departure": dep, "arrival": dep + a.travel,
                      "waiting": max(0, a.service_start - (dep + a.travel))})  # actual departure is a fact
        if ex.get("started_at"):
            started = clock.to_minutes(_hhmm(clock, ex["started_at"]))
            a = Assign(**{**a.__dict__, "service_start": started, "service_end": started + snap.orders[oid].duration,
                          "waiting": max(0, started - a.arrival)})
        locked_plan[oid] = Assign(**{**a.__dict__, "locked": True})
    if locked_plan:
        # validation of locked facts is skipped by design; commit them as the seed's execution state
        commit_plan(db, clock, snap, locked_plan, reason="scenario_seed_locked")
        for oid, ex in executions:
            order = db.get(WorkOrder, oid)
            tech_row = db.get(Technician, ex["technician"])
            assert order and tech_row
            order.lifecycle_status = ex["status"]
            order.departed_at = _hhmm(clock, ex["departed_at"])
            if ex.get("arrived_at"):
                order.arrived_at = _hhmm(clock, ex["arrived_at"])
            if ex.get("started_at"):
                order.service_started_at = _hhmm(clock, ex["started_at"])
            tech_row.status = {"EN_ROUTE": TechnicianStatus.EN_ROUTE, "ARRIVED": TechnicianStatus.ARRIVED,
                               "IN_PROGRESS": TechnicianStatus.BUSY}[ex["status"]]
        db.flush()

    # baseline schedule for the remaining orders (verified scenario load; recorded as its own version)
    snap = build_snapshot(db, clock)
    pending = [o.id for o in snap.orders.values() if o.id not in snap.assignments and not o.closed]
    res = solve_initial(snap, pending, get_policy())
    summary: dict[str, Any] = {"scenario": name, "generation": gen, "technicians": len(scenario["technicians"]),
                               "orders": len(scenario["orders"]), "locked": list(locked_plan), "solve_status": res.status.value,
                               "unassigned": res.unassigned}
    if res.candidates:
        cand = res.candidates[0]
        v = validate_plan(snap, cand.plan, allow_missing=set(res.unassigned))
        if not v.ok:
            raise RuntimeError(f"seed baseline failed validation: {v.violations}")
        commit_plan(db, clock, snap, cand.plan, reason="scenario_seed_baseline",
                    metrics={"solve_status": res.status.value, "decision_score": cand.decision_score,
                             "unassigned": res.unassigned})
        summary["decision_score"] = cand.decision_score
        summary["assigned"] = len(cand.changed_ids)
    for oid in res.unassigned:
        order = db.get(WorkOrder, oid)
        if order:
            order.scheduling_status = SchedulingStatus.UNRESOLVED
    summary["history"] = seed_history(db, clock)
    summary["load"] = load_summary(db, clock)
    db.flush()
    return summary


def load_summary(db: Session, clock: Clock) -> dict[str, Any]:
    """Workload = (service + travel) / shift length, before dynamic rest; plus skill/window bottleneck counts."""
    snap = build_snapshot(db, clock)
    per: dict[str, float] = {}
    for tid, t in snap.techs.items():
        busy = sum(a.service_end - a.departure for a in snap.route_of(tid))
        per[tid] = round(busy / max(1, t.shift_end - t.shift_start), 2)
    unassigned = [o.id for o in snap.orders.values() if o.id not in snap.assignments and not o.closed]
    return {"utilisation_by_technician": per, "mean_utilisation": round(sum(per.values()) / max(1, len(per)), 2), "unassigned": unassigned,
            "note": "service+travel over shift; rest not deducted"}


def seed_history(db: Session, clock: Clock) -> dict[str, Any]:
    """Synthetic customer history (marked simulated): returning customers, saved addresses, ratings, repeat repairs, durations."""
    from datetime import timedelta

    from app.models.entities import CustomerFeedback, DurationObservation, ServiceReport
    from app.services import customer_service
    gen = clock.scenario_generation
    cat = {(i.trade_type, i.problem_name): i for i in catalog_service.list_active(db)}

    def past_order(cust, days_ago: int, trade: str, problem: str, tech_id: str, actual_minutes: int, actual_problem: str, resolution: str,
                   address: dict[str, Any]) -> WorkOrder:
        item = cat[(trade, problem)]
        start = clock.now - timedelta(days=days_ago, hours=2)
        o = WorkOrder(id=next_order_id(db), scenario_generation=gen, customer_ref=f"hist:{cust.id}", customer_name=cust.name, contact_phone=cust.phone,
                      description=f"(history) {problem}", location_id=address["location_id"], location_name=address["formatted_address"],
                      lat=address["latitude"], lon=address["longitude"], catalog_item_id=item.id, catalog_snapshot=catalog_service.snapshot_of(item),
                      window_start=start, window_end=start + timedelta(minutes=90), paid_expedite=False, base_priority="P3", risk_priority="P3",
                      effective_priority="P3", priority_reasons=[], lifecycle_status="COMPLETED", scheduling_status="ASSIGNED", technician_id=tech_id,
                      version=1, departed_at=start - timedelta(minutes=20), arrived_at=start, service_started_at=start,
                      completed_at=start + timedelta(minutes=actual_minutes), customer_id=cust.id, report_status="complete", tags=["simulated_history"],
                      address={k: address.get(k) for k in ("formatted_address", "postal_code", "building_name", "street_address", "unit_number",
                                                           "unit_not_applicable", "latitude", "longitude", "source")})
        db.add(o)
        db.flush()
        db.add(ServiceReport(id=new_id("rep"), order_id=o.id, technician_id=tech_id, actual_problem_text=actual_problem, resolution=resolution,
                             notes="synthetic history", actual_start_at=o.service_started_at, actual_end_at=o.completed_at, status="complete"))
        db.add(DurationObservation(id=new_id("dur"), scenario_generation=gen, order_id=o.id, catalog_item_id=item.id, problem_name=item.problem_name,
                                   complexity_level=item.complexity_level, baseline_minutes=item.repair_duration_minutes, actual_minutes=actual_minutes,
                                   quality="ok" if 5 <= actual_minutes <= item.repair_duration_minutes * 4 else "outlier", source="synthetic_history",
                                   observed_at=o.completed_at))
        return o

    def addr(cust, lat, lon, formatted, postal, unit, source="onemap") -> dict[str, Any]:
        return customer_service.upsert_address(db, clock, cust, lat=lat, lon=lon, formatted_address=formatted, postal_code=postal, building_name=None,
                                               street_address=formatted, unit_number=unit, unit_not_applicable=False, source=source, save=True)

    alice = customer_service.get_or_create_by_login(db, clock, "alice", "Alice Tan", "91110001")
    a1 = addr(alice, 1.34631, 103.95013, "125 Tampines Street 11 Singapore 521125", "521125", "#05-123")
    o1 = past_order(alice, 45, "Air Conditioning", "Water leakage", "tech_01", 38, "Blocked drain pipe cleared", "fixed", a1)
    o2 = past_order(alice, 30, "Washing Machine", "Not draining", "tech_02", 62, "Pump clogged with coins; pump replaced", "fixed", a1)
    past_order(alice, 10, "Air Conditioning", "No cooling or heating", "tech_01", 55, "Low refrigerant; topped up, possible slow leak", "partial", a1)
    db.add(CustomerFeedback(id=new_id("fb"), scenario_generation=gen, order_id=o1.id, customer_id=alice.id, technician_id="tech_01", rating=5, target="technician",
                            reasons=["punctual", "explained_clearly"], comment="Great job", sim_time=o1.completed_at))
    db.add(CustomerFeedback(id=new_id("fb"), scenario_generation=gen, order_id=o2.id, customer_id=alice.id, technician_id="tech_02", rating=2, target="technician",
                            reasons=["attitude"], comment="Was rude and left a mess", sim_time=o2.completed_at))
    bob = customer_service.get_or_create_by_login(db, clock, "bob", "Bob Lim", "91110002")
    b1 = addr(bob, 1.37249, 103.89377, "90 Hougang Avenue 10 Hougang Mall Singapore 538766", "538766", "#12-34")
    o4 = past_order(bob, 20, "Water Heater", "No hot water", "tech_05", 47, "Heating element replaced", "fixed", b1)
    db.add(CustomerFeedback(id=new_id("fb"), scenario_generation=gen, order_id=o4.id, customer_id=bob.id, technician_id="tech_05", rating=2, target="dispatch",
                            reasons=["late_arrival"], comment="Technician was fine but arrived 40 minutes after the window", sim_time=o4.completed_at))
    # duration samples for the shadow model (several problems, mixed quality)
    carol = customer_service.get_or_create_by_login(db, clock, "carol", "Carol Ng", "91110003")
    c1 = addr(carol, 1.32175, 103.76299, "210A Clementi Avenue 6 Singapore 121210", "121210", "#08-77")
    samples = [("Refrigerator", "Not cooling", "tech_07", 44), ("Refrigerator", "Not cooling", "tech_07", 58), ("Refrigerator", "Not cooling", "tech_01", 49),
               ("Refrigerator", "Not cooling", "tech_02", 61), ("Refrigerator", "Not cooling", "tech_07", 52), ("Refrigerator", "Not cooling", "tech_01", 55),
               ("Plumbing & Bathroom", "Toilet clog", "tech_03", 18), ("Plumbing & Bathroom", "Toilet clog", "tech_03", 25), ("Plumbing & Bathroom", "Toilet clog", "tech_05", 31),
               ("Plumbing & Bathroom", "Toilet clog", "tech_03", 22), ("Plumbing & Bathroom", "Toilet clog", "tech_03", 240), ("Electrical & Lighting", "Outlet no power", "tech_04", 40)]
    for i, (tr, pr, tech, mins) in enumerate(samples):
        past_order(carol, 60 - i * 4, tr, pr, tech, mins, f"(history) {pr}", "fixed", c1)
    db.flush()
    return {"customers": ["alice (returning: saved address, 5★ tech_01, 2★ tech_02 attitude, repeat AC records)", "bob (2★ dispatch/lateness)",
                          "carol (12 duration samples incl. one outlier)"], "note": "all history is synthetic and marked simulated"}


def list_scenarios() -> list[dict[str, Any]]:
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.json")):
        raw = json.loads(p.read_text(encoding="utf-8"))
        out.append({"name": raw.get("name", p.stem), "description": raw.get("description", ""), "technicians": len(raw.get("technicians", [])),
                    "orders": len(raw.get("orders", []))})
    return out


def current_summary(db: Session) -> dict[str, Any]:
    clock = get_clock(db)
    return {"scenario": clock.scenario_name, "generation": clock.scenario_generation, "seed": clock.seed}
