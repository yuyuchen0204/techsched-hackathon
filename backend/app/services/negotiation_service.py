"""Appointment negotiation (V3 §4.4): read-only trial scheduling of feasible windows; reschedule requests go to a human."""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import RescheduleRequest, WorkOrder
from app.models.enums import Priority
from app.scheduling.domain import OrderSpec, Snapshot
from app.scheduling.policy import decide
from app.scheduling.solver import solve_insert
from app.services import catalog_service, human_service
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.notification_service import notify
from app.services.order_service import OrderError
from app.services.snapshot import build_snapshot
from app.services.timeutil import hhmm, iso, local_time_utc, to_local

WINDOW_MINUTES = 90


def _trial_spec(snap: Snapshot, *, oid: str, item: dict[str, Any], location_id: str, ws: int, we: int, priority: Priority,
                excluded: tuple[str, ...]) -> OrderSpec:
    return OrderSpec(id=oid, trade_type=item["trade_type"], required_level=int(item["complexity_level"]), duration=int(item["repair_duration_minutes"]),
                     window_start=ws, window_end=we, location_id=location_id, priority=priority, lifecycle_status="OPEN", version=0,
                     excluded_technicians=excluded)


def propose_windows(db: Session, clock: Clock, *, catalog_item_id: str, location_id: str, customer_id: str | None = None,
                    excluded_technicians: list[str] | None = None, exclude_windows: list[tuple[str, str]] | None = None,
                    after: datetime | None = None, count: int = 4, paid: bool = False, penalties: dict[str, float] | None = None,
                    allow_moves: bool = False) -> dict[str, Any]:
    """Trials over candidate windows. Zero-disturbance by default; a proposal is NOT a reservation, the chosen window is
    re-validated. `allow_moves=True` additionally lets the solver move other undeparted P3 orders (no cap; each stays
    inside its own window) at P1 priority for a paid-expedite window — those results carry `paid_required=True` and a
    policy `decision`."""
    item_row = catalog_service.get_item(db, catalog_item_id)
    if item_row is None:
        raise OrderError("invalid_catalog_item", "unknown catalog item", kind="DATA_INCOMPLETE")
    item = catalog_service.snapshot_of(item_row)
    snap = build_snapshot(db, clock)
    if location_id not in snap.location_names and location_id not in {o.location_id for o in snap.orders.values()}:
        from app.models.entities import Location
        if db.get(Location, location_id) is None:
            raise OrderError("invalid_location", "unknown location", kind="DATA_INCOMPLETE")
    day = to_local(clock.day_origin).date()  # type: ignore[union-attr]
    local_now = to_local(clock.now)
    assert local_now is not None
    first = max(((local_now.hour * 60 + local_now.minute + 29) // 30) * 30 + 30, 8 * 60 + 30)
    if after is not None:
        after_local = to_local(after)
        assert after_local is not None
        first = max(first, ((after_local.hour * 60 + after_local.minute + 29) // 30) * 30)
    skip = {(a, b) for a, b in (exclude_windows or [])}
    excluded = tuple(excluded_technicians or ())
    policy = get_policy()
    priority = Priority(policy.paid_base if (paid or allow_moves) else policy.normal_base)
    tried = 0
    results: list[dict[str, Any]] = []
    rejections: dict[str, int] = {}
    for m in range(first, 17 * 60 + 1, 30):
        ws_dt = local_time_utc(day, m // 60, m % 60)
        we_dt = ws_dt + timedelta(minutes=WINDOW_MINUTES)
        if (iso(ws_dt), iso(we_dt)) in skip:
            continue
        tried += 1
        spec = _trial_spec(snap, oid="__trial__", item=item, location_id=location_id, ws=clock.to_minutes(ws_dt), we=clock.to_minutes(we_dt),
                           priority=priority, excluded=excluded)
        trial = Snapshot(now=snap.now, orders={**snap.orders, spec.id: spec}, techs=snap.techs, assignments=dict(snap.assignments),
                         matrix=snap.matrix, schedule_version=snap.schedule_version, scenario_generation=snap.scenario_generation,
                         location_names=snap.location_names)
        if allow_moves:
            res = solve_insert(trial, spec, policy, allow_relocate=True, budget_ms=600)
            cands = [c for c in res.candidates if c.authority.ok]
        else:
            res = solve_insert(trial, spec, policy, allow_relocate=False, budget_ms=400)
            cands = [c for c in res.candidates if c.affected.count == 0]
        if not cands:
            rejections[res.status.value] = rejections.get(res.status.value, 0) + 1
            continue
        pen = penalties or {}
        best = max(cands, key=lambda c: (c.decision_score or 0) - 8.0 * pen.get(c.plan[spec.id].tech_id, 0.0))
        a = best.plan[spec.id]
        row = {"window_start": iso(ws_dt), "window_end": iso(we_dt), "earliest_start": iso(clock.from_minutes(a.service_start)),
              "technician_id": a.tech_id, "technician_name": snap.techs[a.tech_id].name, "score": round(best.decision_score or 0, 1),
              "travel_minutes": a.travel, "preference_penalty": pen.get(a.tech_id, 0.0)}
        if allow_moves:
            outcome = decide(target_priority=priority, decision_score=best.decision_score, validation_ok=best.validation.ok,
                             authority=best.authority, policy=policy, has_changes=bool(best.changed_ids), search_incomplete=res.search_incomplete)
            row.update({"paid_required": True, "affected_count": best.affected.count, "technician_changes": best.affected.technician_changes,
                       "decision": outcome.decision.value})
        else:
            row.update({"paid_required": False, "affected_count": 0})
        results.append(row)
        if len(results) >= count:
            break
    return {"status": "ok" if results else "no_window", "snapshot_version": snap.schedule_version,
            "reason_codes": [] if results else ["NO_FEASIBLE_WINDOW_TODAY"],
            "data": {"windows": results, "tried": tried, "rejections": rejections, "excluded_technicians": list(excluded),
                     "note": "read-only trial; not a reservation — the chosen window is validated again on submit"}}


def request_reschedule(db: Session, clock: Clock, order: WorkOrder, *, window_start: datetime, window_end: datetime, reason: str,
                       customer_id: str | None, session_id: str | None) -> dict[str, Any]:
    """Existing orders: a reschedule request awaits human confirmation; the current window/assignment stays valid."""
    if order.lifecycle_status != "OPEN":
        raise OrderError("invalid_state", "only undeparted orders can be rescheduled", status=409, kind="INVALID_STATE")
    if window_end <= window_start or window_end <= clock.now:
        raise OrderError("invalid_window", "requested window must be in the future", kind="DATA_INCOMPLETE")
    req = RescheduleRequest(id=new_id("rsq"), order_id=order.id, requested_window_start=window_start, requested_window_end=window_end,
                            reason=reason, status="pending")
    db.add(req)
    db.flush()
    case, _ = human_service.flag_for_human(
        db, clock, source="CUSTOMER_REQUEST", category="reschedule_request", urgency="normal",
        reason_summary=f"Customer asks to move {order.id} to {hhmm(window_start)}–{hhmm(window_end)}: {reason or 'no reason given'}",
        customer_id=customer_id, session_id=session_id, order_id=order.id, evidence_refs=[f"reschedule:{req.id}"],
        suggested_next_action="Validate the new window with a trial insertion, then approve (re-dispatch) or reply to the customer.",
        idempotency_key=f"reschedule:{req.id}")
    req.human_case_id = case.id
    db.flush()
    return {"request_id": req.id, "human_case_id": case.id, "status": req.status,
            "note": "pending human confirmation; the existing appointment stays in place until approved"}


def _pending_request(db: Session, request_id: str) -> tuple[RescheduleRequest, WorkOrder]:
    req = db.get(RescheduleRequest, request_id)
    if req is None:
        raise OrderError("not_found", "reschedule request not found", status=404)
    if req.status != "pending":
        raise OrderError("invalid_state", f"request is {req.status}", status=409, kind="INVALID_STATE")
    order = db.get(WorkOrder, req.order_id)
    if order is None or order.lifecycle_status != "OPEN":
        raise OrderError("invalid_state", "order is no longer undeparted", status=409, kind="INVALID_STATE")
    return req, order


def _supersede_siblings(db: Session, order_id: str, keep_id: str) -> list[str]:
    """Close the other pending requests for the same order.

    A customer who asks twice gets one case (flag_for_human merges by order + category), so a decision on one request
    settles the others too. They are marked here, with no extra customer message: the decision itself was already
    announced, and a second "we kept your original time" on top of "moved to 11:00" is a contradiction.
    """
    others = db.scalars(select(RescheduleRequest).where(RescheduleRequest.order_id == order_id,
                                                        RescheduleRequest.status == "pending",
                                                        RescheduleRequest.id != keep_id)).all()
    for o in others:
        o.status = "superseded"
    db.flush()
    return [o.id for o in others]


def preview_reschedule(db: Session, clock: Clock, request_id: str) -> dict[str, Any]:
    """Read-only: could the order actually be served in the window the customer asked for?

    The dispatcher was told to "validate with a trial insertion" but had nothing to validate with, so approving was a
    leap of faith that could trade a good confirmed slot for an impossible one. This answers the question first.
    """
    req, order = _pending_request(db, request_id)
    snap = build_snapshot(db, clock)
    if order.id not in snap.orders:
        return {"request_id": req.id, "feasible": False, "reason": "order is not in today's schedule"}
    policy = get_policy()
    priority = Priority(order.effective_priority)
    ws, we = clock.to_minutes(req.requested_window_start), clock.to_minutes(req.requested_window_end)
    spec = replace(snap.orders[order.id], window_start=ws, window_end=we, priority=priority, recovery_target=False)
    trial = replace(snap, orders={**snap.orders, order.id: spec}, assignments=dict(snap.assignments))
    current = snap.assignments.get(order.id)
    out: dict[str, Any] = {"request_id": req.id, "order_id": order.id,
                           "current_window": [iso(order.window_start), iso(order.window_end)],
                           "current_start": iso(clock.from_minutes(current.service_start)) if current else None,
                           "current_technician": snap.techs[current.tech_id].name if current else None,
                           "requested_window": [iso(req.requested_window_start), iso(req.requested_window_end)]}
    for allow_moves in (False, True):          # zero-disturbance first; only then consider moving other appointments
        res = solve_insert(trial, spec, policy, allow_relocate=allow_moves, budget_ms=600)
        cands = [c for c in res.candidates if c.authority.ok] if allow_moves else [c for c in res.candidates if c.affected.count == 0]
        if not cands:
            continue
        best = max(cands, key=lambda c: (c.decision_score or 0))
        a = best.plan[order.id]
        outcome = decide(target_priority=priority, decision_score=best.decision_score, validation_ok=best.validation.ok,
                         authority=best.authority, policy=policy, has_changes=bool(best.changed_ids),
                         search_incomplete=res.search_incomplete)
        return {**out, "feasible": True, "disturbs_others": best.affected.count > 0,
                "new_start": iso(clock.from_minutes(a.service_start)), "technician_name": snap.techs[a.tech_id].name,
                "technician_id": a.tech_id, "affected_count": best.affected.count,
                "affected_order_ids": sorted(best.affected.affected_ids), "decision_score": round(best.decision_score or 0, 1),
                "decision": outcome.decision.value,
                "note": "trial only — approving runs the real dispatch again and may land differently"}
    return {**out, "feasible": False,
            "reason": f"no technician can serve {order.id} in {hhmm(req.requested_window_start)}–{hhmm(req.requested_window_end)} "
                      f"within the authority of a {priority.value} order"}


def approve_reschedule(db: Session, clock: Clock, request_id: str, *, author: str, force: bool = False) -> dict[str, Any]:
    """Move the order to the requested window. Refuses when a trial says it cannot be served there, because the
    original confirmed appointment is released first and an infeasible move would leave the customer with nothing."""
    req, order = _pending_request(db, request_id)
    trial = preview_reschedule(db, clock, request_id)
    if not trial.get("feasible") and not force:
        raise OrderError("reschedule_infeasible", trial.get("reason") or "the requested window cannot be served",
                         {"preview": trial}, status=409, kind="POLICY_VIOLATION")
    from app.orchestration.orchestrator import dispatch_order
    from app.services import plan_service
    from app.services.schedule_service import invalidate_assignment
    invalidate_assignment(db, clock, order.id, f"rescheduled by {author} ({request_id})")
    plan_service.invalidate_plans_for_order(db, order.id, "window changed by reschedule")
    order.window_start, order.window_end = req.requested_window_start, req.requested_window_end
    order.version += 1
    req.status = "approved"
    superseded = _supersede_siblings(db, order.id, req.id)
    outcome = dispatch_order(db, order.id, trigger="reschedule_approved")
    if outcome.decision == "unresolved" and not force:
        # the caller's transaction is rolled back by the `locked` wrapper, so the old appointment survives intact
        raise OrderError("reschedule_infeasible", f"the real dispatch found no plan for the new window ({outcome.reason or 'no candidate'}); "
                         "the original appointment was kept", {"preview": trial}, status=409, kind="POLICY_VIOLATION")
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="rescheduled", order_id=order.id,
           message=f"[simulated] Your appointment for {order.id} was moved to "
                   f"{hhmm(req.requested_window_start)}–{hhmm(req.requested_window_end)} as you asked.")
    if req.human_case_id:
        human_service.resolve(db, clock, req.human_case_id, resolution=f"reschedule approved; dispatch {outcome.decision}", author=author)
    return {"request_id": req.id, "status": req.status, "dispatch": outcome.as_dict(), "preview": trial,
            "superseded_request_ids": superseded}


def decline_reschedule(db: Session, clock: Clock, request_id: str, *, author: str, reason: str = "") -> dict[str, Any]:
    """Keep the original appointment. Without this the dispatcher could only close the case, leaving the request
    `pending` forever and the customer with no answer."""
    req, order = _pending_request(db, request_id)
    req.status = "rejected"
    superseded = _supersede_siblings(db, order.id, req.id)
    note = (reason or "").strip() or "the requested time could not be arranged"
    notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type="reschedule_declined", order_id=order.id,
           message=f"[simulated] We kept your appointment for {order.id} at {hhmm(order.window_start)}–{hhmm(order.window_end)}: {note}")
    if req.human_case_id:
        human_service.resolve(db, clock, req.human_case_id, resolution=f"reschedule declined: {note}", author=author)
    return {"request_id": req.id, "status": req.status, "kept_window": [iso(order.window_start), iso(order.window_end)],
            "superseded_request_ids": superseded}


def day_grid(db: Session, clock: Clock, *, catalog_item_id: str, location_id: str,
             excluded_technicians: list[str] | None = None, penalties: dict[str, float] | None = None,
             total_budget_ms: int = 4000) -> dict[str, Any]:
    """Every 30-minute slot left today, each marked available / paid / impossible (4th meeting, 加急逻辑 §2).

    The customer used to see only the handful of slots that happened to be free, which hides the actual choice: a slot
    that is busy is not unavailable, it is *purchasable* — taking it shifts a few undeparted P3 jobs, and that is what
    the P1 paid price pays for. So every slot is shown:
      available  — a zero-disturbance fit exists, pick it for free
      paid       — only reachable by moving other appointments, allowed at P1 and priced
      impossible — no technician can serve it at all (skill, shift, travel); shown greyed, never silently dropped

    Each slot costs up to two solver trials, so budgets are deliberately small and a whole-call budget stops the search
    rather than letting a chat turn hang; slots past the budget come back as `unknown` and say so.
    """
    item_row = catalog_service.get_item(db, catalog_item_id)
    if item_row is None:
        raise OrderError("invalid_catalog_item", "unknown catalog item", kind="DATA_INCOMPLETE")
    item = catalog_service.snapshot_of(item_row)
    snap = build_snapshot(db, clock)
    policy = get_policy()
    day = to_local(clock.day_origin).date()  # type: ignore[union-attr]
    local_now = to_local(clock.now)
    assert local_now is not None
    first = max(((local_now.hour * 60 + local_now.minute + 29) // 30) * 30 + 30, 8 * 60 + 30)
    excluded = tuple(excluded_technicians or ())
    pen = penalties or {}
    started = time.monotonic()
    slots: list[dict[str, Any]] = []

    def trial(ws_min: int, we_min: int, priority: Priority, allow_moves: bool, budget: int):
        spec = _trial_spec(snap, oid="__trial__", item=item, location_id=location_id, ws=ws_min, we=we_min,
                           priority=priority, excluded=excluded)
        t = Snapshot(now=snap.now, orders={**snap.orders, spec.id: spec}, techs=snap.techs, assignments=dict(snap.assignments),
                     matrix=snap.matrix, schedule_version=snap.schedule_version, scenario_generation=snap.scenario_generation,
                     location_names=snap.location_names)
        res = solve_insert(t, spec, policy, allow_relocate=allow_moves, budget_ms=budget)
        cands = [c for c in res.candidates if c.authority.ok] if allow_moves else [c for c in res.candidates if c.affected.count == 0]
        if not cands:
            return None, spec, res
        best = max(cands, key=lambda c: (c.decision_score or 0) - 8.0 * pen.get(c.plan[spec.id].tech_id, 0.0))
        return best, spec, res

    for m in range(first, 17 * 60 + 1, 30):
        ws_dt = local_time_utc(day, m // 60, m % 60)
        we_dt = ws_dt + timedelta(minutes=WINDOW_MINUTES)
        row: dict[str, Any] = {"window_start": iso(ws_dt), "window_end": iso(we_dt),
                               "label": f"{hhmm(ws_dt)}–{hhmm(we_dt)}"}
        if (time.monotonic() - started) * 1000 > total_budget_ms:
            row.update({"state": "unknown", "paid_required": False, "affected_count": 0,
                        "note": "not checked — the search budget for this turn ran out"})
            slots.append(row)
            continue
        ws_min, we_min = clock.to_minutes(ws_dt), clock.to_minutes(we_dt)
        free, spec, _ = trial(ws_min, we_min, Priority(policy.normal_base), False, 150)
        if free is not None:
            a = free.plan[spec.id]
            row.update({"state": "available", "paid_required": False, "affected_count": 0,
                        "earliest_start": iso(clock.from_minutes(a.service_start)), "technician_id": a.tech_id,
                        "technician_name": snap.techs[a.tech_id].name, "score": round(free.decision_score or 0, 1)})
            slots.append(row)
            continue
        paid_best, spec, res = trial(ws_min, we_min, Priority(policy.paid_base), True, 250)
        if paid_best is not None:
            a = paid_best.plan[spec.id]
            outcome = decide(target_priority=Priority(policy.paid_base), decision_score=paid_best.decision_score,
                             validation_ok=paid_best.validation.ok, authority=paid_best.authority, policy=policy,
                             has_changes=bool(paid_best.changed_ids), search_incomplete=res.search_incomplete)
            row.update({"state": "paid", "paid_required": True, "affected_count": paid_best.affected.count,
                        "earliest_start": iso(clock.from_minutes(a.service_start)), "technician_id": a.tech_id,
                        "technician_name": snap.techs[a.tech_id].name, "score": round(paid_best.decision_score or 0, 1),
                        "technician_changes": paid_best.affected.technician_changes, "decision": outcome.decision.value})
        else:
            row.update({"state": "impossible", "paid_required": False, "affected_count": 0,
                        "note": "no technician can serve this slot today (skill, shift or travel)"})
        slots.append(row)
    return {"status": "ok", "snapshot_version": snap.schedule_version,
            "data": {"slots": slots, "available": sum(1 for s in slots if s["state"] == "available"),
                     "paid": sum(1 for s in slots if s["state"] == "paid"),
                     "elapsed_ms": int((time.monotonic() - started) * 1000),
                     "note": "read-only trial; not a reservation — the chosen slot is validated again on submit"}}


def earliest_now(db: Session, clock: Clock, *, catalog_item_id: str, location_id: str,
                 excluded_technicians: list[str] | None = None) -> dict[str, Any]:
    """"Send someone now": the soonest start a P0 order can actually get, and what that costs the rest of the day."""
    item_row = catalog_service.get_item(db, catalog_item_id)
    if item_row is None:
        raise OrderError("invalid_catalog_item", "unknown catalog item", kind="DATA_INCOMPLETE")
    item = catalog_service.snapshot_of(item_row)
    snap = build_snapshot(db, clock)
    policy = get_policy()
    horizon = policy.expedite_now_window_minutes
    ws_min = snap.now
    we_min = snap.now + horizon
    spec = _trial_spec(snap, oid="__now__", item=item, location_id=location_id, ws=ws_min, we=we_min,
                       priority=Priority(policy.paid_now_base), excluded=tuple(excluded_technicians or ()))
    trial = Snapshot(now=snap.now, orders={**snap.orders, spec.id: spec}, techs=snap.techs, assignments=dict(snap.assignments),
                     matrix=snap.matrix, schedule_version=snap.schedule_version, scenario_generation=snap.scenario_generation,
                     location_names=snap.location_names)
    res = solve_insert(trial, spec, policy, allow_relocate=True, budget_ms=1200)
    cands = [c for c in res.candidates if c.authority.ok]
    if not cands:
        return {"possible": False, "window_start": iso(clock.from_minutes(ws_min)), "window_end": iso(clock.from_minutes(we_min)),
                "reason": "nobody with the right skill can reach you within the next "
                          f"{horizon // 60}h — a coordinator has to arrange this by hand"}
    best = min(cands, key=lambda c: (c.target_start, c.affected.count))
    a = best.plan[spec.id]
    return {"possible": True, "window_start": iso(clock.from_minutes(ws_min)), "window_end": iso(clock.from_minutes(we_min)),
            "earliest_start": iso(clock.from_minutes(a.service_start)), "technician_id": a.tech_id,
            "technician_name": snap.techs[a.tech_id].name, "affected_count": best.affected.count,
            "score": round(best.decision_score or 0, 1)}
