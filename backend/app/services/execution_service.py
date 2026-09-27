"""Execution events (depart/arrive/start/complete), simulated clock advance, and the periodic risk scan."""
from __future__ import annotations

import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.db.base import utc_now
from app.models.entities import Assignment, CandidatePlan, Technician, WorkOrder
from app.models.enums import (
    AssignmentStatus,
    LifecycleStatus,
    PlanStatus,
    Priority,
    TechnicianStatus,
)
from app.orchestration.orchestrator import dispatch_key, dispatch_order
from app.services import risk_service
from app.services.clock import Clock, ensure_state, get_clock, set_now
from app.services.notification_service import notify
from app.services.order_service import OrderError
from app.services.schedule_service import refresh_predictions
from app.services.snapshot import build_snapshot
from app.services.timeutil import add_minutes, hhmm

# Single-process mutual exclusion for all state mutations (scan loop + API). Multi-process is out of scope.
state_lock = threading.RLock()

TRANSITIONS = {
    "depart": (LifecycleStatus.OPEN, LifecycleStatus.EN_ROUTE),
    "arrive": (LifecycleStatus.EN_ROUTE, LifecycleStatus.ARRIVED),
    "start": (LifecycleStatus.ARRIVED, LifecycleStatus.IN_PROGRESS),
    "complete": (LifecycleStatus.IN_PROGRESS, LifecycleStatus.COMPLETED),
}
ORDER_OF = ["depart", "arrive", "start", "complete"]


def _active(db: Session, order_id: str) -> Assignment | None:
    return db.scalars(select(Assignment).where(Assignment.order_id == order_id, Assignment.status == AssignmentStatus.ACTIVE)).first()


def execution_event(db: Session, order_id: str, event: str, *, at: Any = None, actor: str = "technician_panel",
                    technician_id: str | None = None) -> dict[str, Any]:
    """actor/source: simulation | technician_app | dispatcher. Facts are recorded as they happen; deviations from the plan
    or the window are flagged as anomalies (never rewritten to look compliant)."""
    if event not in TRANSITIONS:
        raise OrderError("invalid_event", f"unknown execution event {event}")
    clock = get_clock(db)
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise OrderError("not_found", f"order {order_id} not found", status=404)
    src, dst = TRANSITIONS[event]
    if order.lifecycle_status == dst or (ORDER_OF.index(event) < _stage(order.lifecycle_status)):
        return {"order_id": order_id, "lifecycle_status": order.lifecycle_status, "idempotent": True}
    if order.lifecycle_status != src:
        raise OrderError("invalid_transition", f"cannot {event} an order in {order.lifecycle_status}",
                         {"lifecycle_status": order.lifecycle_status}, status=409)
    a = _active(db, order_id)
    if a is None:
        raise OrderError("no_assignment", "order has no active assignment", status=409)
    if technician_id is not None and a.technician_id != technician_id:
        raise OrderError("forbidden", "this task belongs to another technician", status=403, kind="FORBIDDEN")
    tech = db.get(Technician, a.technician_id)
    assert tech is not None
    if actor == "simulation" and tech.sim_mode == "manual":
        raise OrderError("invalid_state", "technician is in manual mode; the simulator does not drive this task", status=409, kind="INVALID_STATE")
    now = clock.now
    anomalies: list[str] = []
    planned = {"depart": a.departure, "arrive": a.arrival, "start": a.service_start, "complete": a.service_end}[event]
    drift = int((now - planned).total_seconds() // 60)
    if event == "depart" and actor != "simulation":
        # leaving early is not a harmless anomaly: the actual time overwrites the plan and locks the task, so the
        # schedule silently shifts for a departure that has not been planned yet. The simulator never hits this —
        # it only fires a departure once it is due.
        grace = get_policy().depart_grace_minutes
        if drift < -grace:
            raise OrderError("too_early_to_depart",
                             f"departure is planned for {hhmm(planned)}; it is {hhmm(now)}"
                             + (f" (you may leave from {hhmm(add_minutes(planned, -grace))})" if grace else ""),
                             {"planned_departure": planned.isoformat() + "Z", "minutes_early": -drift,
                              "grace_minutes": grace}, status=409, kind="INVALID_STATE")
    if event == "start" and now < order.window_start:
        anomalies.append("started_before_window")
    if event == "start" and now > order.window_end:
        anomalies.append("started_after_window_end")
    if event == "depart" and drift <= -15:
        anomalies.append("departed_early")
    if drift >= 15:
        anomalies.append(f"{event}_late_vs_plan")
    if event == "complete" and order.service_started_at is not None:
        dur = int((now - order.service_started_at).total_seconds() // 60)
        base = int(order.catalog_snapshot["repair_duration_minutes"])
        if dur < base * 0.3:
            anomalies.append("service_much_shorter_than_baseline")
        if dur > base * 2:
            anomalies.append("service_much_longer_than_baseline")
    if event == "depart":
        order.lifecycle_status = LifecycleStatus.EN_ROUTE
        order.departed_at = now
        a.locked = True
        if a.departure != now:  # manual early/late departure: actual fact wins, prediction re-derived
            a.departure = now
            a.arrival = add_minutes(now, a.travel_minutes)
            a.service_start = max(a.arrival, order.window_start)
            a.service_end = add_minutes(a.service_start, int(order.catalog_snapshot["repair_duration_minutes"]))
        tech.status = TechnicianStatus.EN_ROUTE
        from app.services import plan_service
        plan_service.invalidate_plans_for_order(db, order_id, "order departed; plans involving it are void")
    elif event == "arrive":
        order.lifecycle_status = LifecycleStatus.ARRIVED
        order.arrived_at = now
        tech.status = TechnicianStatus.ARRIVED
        tech.current_location_id = order.location_id
        if now != a.arrival:  # actual arrival is a fact; predicted start re-derived (wait for window if early)
            a.arrival = now
            a.service_start = max(now, order.window_start)
            a.service_end = add_minutes(a.service_start, int(order.catalog_snapshot["repair_duration_minutes"]))
    elif event == "start":
        order.lifecycle_status = LifecycleStatus.IN_PROGRESS
        order.service_started_at = now
        tech.status = TechnicianStatus.BUSY
        a.service_start = now
        a.service_end = add_minutes(now, int(order.catalog_snapshot["repair_duration_minutes"]))
    elif event == "complete":
        order.lifecycle_status = LifecycleStatus.COMPLETED
        order.completed_at = now
        a.status = AssignmentStatus.COMPLETED
        a.service_end = now
        tech.status = TechnicianStatus.AVAILABLE
        tech.current_location_id = order.location_id
        for r in risk_service.active_risks(db, order_id):
            risk_service.resolve_risk(db, clock, order_id, __import__("app.models.enums", fromlist=["RiskType"]).RiskType(r.type))
    order.version += 1
    tech.version += 1
    from app.models.entities import ExecutionEvent
    from app.services.ids import new_id
    db.add(ExecutionEvent(id=new_id("xe"), scenario_generation=clock.scenario_generation, order_id=order_id, technician_id=tech.id,
                          event=event, sim_time=now, source=actor, anomalies=anomalies))
    if event == "complete":
        from app.services import duration_service
        duration_service.record_completion(db, clock, order, a, source="simulated" if actor == "simulation" else "manual")
    db.flush()
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type=f"execution_{event}", order_id=order_id,
           message=f"[simulated] {tech.name} {_verb(event)} order {order_id} at {hhmm(now)}.",
           dedupe_key=f"exec:{order_id}:{event}")
    return {"order_id": order_id, "lifecycle_status": order.lifecycle_status, "at": now.isoformat() + "Z", "actor": actor,
            "anomalies": anomalies}


def _stage(status: str) -> int:
    return {"OPEN": 0, "EN_ROUTE": 1, "ARRIVED": 2, "IN_PROGRESS": 3, "COMPLETED": 4}.get(status, -1)


def _verb(event: str) -> str:
    return {"depart": "departed for", "arrive": "arrived at", "start": "started work on", "complete": "completed"}[event]


def auto_progress(db: Session, clock: Clock) -> list[dict[str, Any]]:
    """Fire due execution events in order (depart → arrive → start → complete) for the current sim time."""
    fired: list[dict[str, Any]] = []
    rows = db.scalars(select(Assignment).where(Assignment.scenario_generation == clock.scenario_generation,
                                               Assignment.status == AssignmentStatus.ACTIVE)).all()
    for a in sorted(rows, key=lambda r: (r.departure, r.order_id)):
        order = db.get(WorkOrder, a.order_id)
        if order is None:
            continue
        tech = db.get(Technician, a.technician_id)
        if tech is None or tech.sim_mode == "manual":
            continue  # manual mode: the technician app is the only driver of this technician's state
        if order.lifecycle_status == LifecycleStatus.OPEN and a.departure <= clock.now:
            if _tech_blocked(tech, clock):
                continue  # unavailable technician cannot depart; risk scan surfaces it
            fired.append(execution_event(db, order.id, "depart", actor="simulation"))
        if order.lifecycle_status == LifecycleStatus.EN_ROUTE and a.arrival <= clock.now:
            fired.append(execution_event(db, order.id, "arrive", actor="simulation"))
        if order.lifecycle_status == LifecycleStatus.ARRIVED and a.service_start <= clock.now:
            fired.append(execution_event(db, order.id, "start", actor="simulation"))
        if order.lifecycle_status == LifecycleStatus.IN_PROGRESS and order.service_started_at is not None:
            end = add_minutes(order.service_started_at, int(order.catalog_snapshot["repair_duration_minutes"]))
            if end <= clock.now:
                fired.append(execution_event(db, order.id, "complete", actor="simulation"))
    return fired


def _tech_blocked(tech: Technician, clock: Clock) -> bool:
    from app.services.timeutil import parse_iso
    for iv in tech.unavailable_intervals or []:
        if parse_iso(iv["start"]) <= clock.now < parse_iso(iv["end"]):
            return True
    return False


def scan(db: Session, *, trigger: str = "risk_scan") -> dict[str, Any]:
    """One monitoring round: refresh predictions, evaluate risks, expire candidates, dispatch what changed."""
    clock = get_clock(db)
    from app.services import break_service
    break_service.progress_blocks(db, clock)
    snap = build_snapshot(db, clock)
    if refresh_predictions(db, clock, snap):
        snap = build_snapshot(db, clock, matrix=snap.matrix)
    expired = 0
    for p in db.scalars(select(CandidatePlan).where(CandidatePlan.status.in_([PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED]),
                                                    CandidatePlan.scenario_generation == clock.scenario_generation)).all():
        if clock.now > p.expires_at or p.base_schedule_version != snap.schedule_version:
            p.status = PlanStatus.EXPIRED
            p.status_reason = ("candidate validity window elapsed" if clock.now > p.expires_at
                               else f"schedule changed (v{p.base_schedule_version} → v{snap.schedule_version}); recompute")
            expired += 1
            t = db.get(WorkOrder, p.target_order_id)
            if t is not None:
                t.last_dispatch_key = None  # recompute on this scan
    from app.services import expedite_service
    expedite_refreshed = expedite_service.refresh_pending(db, clock)  # expired expedite reviews are searched again
    if expedite_refreshed:
        snap = build_snapshot(db, clock, matrix=snap.matrix)
    dispatched: list[dict[str, Any]] = []
    evaluated = 0
    for order in risk_service.pending_orders(db, clock):
        state = risk_service.evaluate_order(db, clock, snap, order)
        evaluated += 1
        if state.changed:
            snap = build_snapshot(db, clock, matrix=snap.matrix)
        key = dispatch_key(order, snap)
        if key == order.last_dispatch_key:
            continue
        has_live_plan = order.pending_plan_run_id is not None and db.scalars(select(CandidatePlan).where(
            CandidatePlan.run_id == order.pending_plan_run_id, CandidatePlan.status == PlanStatus.PENDING_REVIEW)).first() is not None
        if has_live_plan and not state.changed:
            order.last_dispatch_key = key
            continue  # dispatcher decision pending; nothing meaningful changed for this order
        if order.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
            order.last_dispatch_key = key
            continue
        if order.id in snap.assignments and state.has_valid_assignment and Priority(order.effective_priority) != Priority.P2 \
                and not snap.orders[order.id].recovery_target and snap.assignments[order.id].service_start <= snap.orders[order.id].window_end:
            order.last_dispatch_key = key
            continue
        outcome = dispatch_order(db, order.id, trigger=trigger)
        dispatched.append(outcome.as_dict())
        snap = build_snapshot(db, clock, matrix=snap.matrix)
    breaks = break_scan(db, clock)
    from app.agents import runtime as agent_runtime
    ran = agent_runtime.run_pending(db)
    st = ensure_state(db)
    st.last_scan_at = utc_now()
    db.flush()
    return {"now": clock.now.isoformat() + "Z", "evaluated": evaluated, "dispatched": dispatched, "expired_plans": expired,
            "expedite_refreshed": expedite_refreshed, "break_tasks": breaks, "agent_tasks_run": ran}


def break_scan(db: Session, clock: Clock) -> list[str]:
    """Dynamic rest (§7): create one break task per technician who reached the evaluation level and has no rest ahead."""
    from app.agents import runtime as agent_runtime
    from app.services import break_service
    created: list[str] = []
    for t in db.scalars(select(Technician).where(Technician.scenario_generation == clock.scenario_generation)).all():
        if t.status in ("UNAVAILABLE", "OFF_SHIFT", "BREAK") or clock.now >= t.shift_end:
            continue
        facts = break_service.work_facts(db, clock, t)
        if facts["level"] in ("none",) or facts["planned_break_ahead"] or facts["in_break"]:
            continue
        task = agent_runtime.create_task(db, clock, role="break", goal=f"{t.name}: {facts['work_minutes_since_break']} min of work since the last rest "
                                                                         f"(level {facts['level']}); arrange a zero-disturbance rest or escalate",
                                         technician_id=t.id, facts={"break_facts": facts}, dedupe_key=f"break:{t.id}:{facts['last_break_end']}")
        if task is not None:
            created.append(task.id)
    return created


def advance_clock(db: Session, minutes: int) -> dict[str, Any]:
    """Advance the simulation minute by minute: due execution events first, then the monitoring scan."""
    if minutes <= 0 or minutes > 24 * 60:
        raise OrderError("invalid_minutes", "minutes must be between 1 and 1440")
    clock = get_clock(db)
    fired_all: list[dict[str, Any]] = []
    dispatched_all: list[dict[str, Any]] = []
    for _ in range(minutes):
        clock = set_now(db, add_minutes(clock.now, 1))
        fired_all.extend(auto_progress(db, clock))
        result = scan(db, trigger="clock_advance")
        dispatched_all.extend(result["dispatched"])
    return {"now": clock.now.isoformat() + "Z", "fired_events": fired_all, "dispatched": dispatched_all}
