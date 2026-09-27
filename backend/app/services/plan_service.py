"""Candidate plan persistence and approval/rejection/recompute with transactional re-validation."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import Approval, CandidatePlan, WorkOrder
from app.models.enums import LifecycleStatus, PlanStatus, PolicyDecision, Priority, SchedulingStatus
from app.scheduling.domain import Assign, Snapshot
from app.scheduling.policy import decide
from app.scheduling.solver import Candidate, evaluate_plan
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.notification_service import notify
from app.services.order_service import OrderError
from app.services.schedule_service import commit_plan
from app.services.snapshot import build_snapshot


def assignment_dict(clock: Clock, a: Assign, snap: Snapshot) -> dict[str, Any]:
    return {
        "order_id": a.order_id, "technician_id": a.tech_id, "technician_name": snap.techs[a.tech_id].name,
        "origin_location_id": a.origin_location_id, "departure": a.departure, "arrival": a.arrival,
        "service_start": a.service_start, "service_end": a.service_end, "travel_minutes": a.travel,
        "waiting_minutes": a.waiting, "locked": a.locked, "match_score": a.match_score,
        "score_components": a.score_components, "departure_at": clock.from_minutes(a.departure).isoformat() + "Z",
        "service_start_at": clock.from_minutes(a.service_start).isoformat() + "Z",
        "service_end_at": clock.from_minutes(a.service_end).isoformat() + "Z",
    }


def _same_assignment(base: Assign | None, a: Assign) -> bool:
    return base is not None and (base.tech_id, base.origin_location_id, base.departure, base.arrival, base.service_start,
                                 base.service_end, base.travel) == (a.tech_id, a.origin_location_id, a.departure, a.arrival,
                                                                    a.service_start, a.service_end, a.travel)


def plan_from_rows(rows: list[dict[str, Any]]) -> dict[str, Assign]:
    out: dict[str, Assign] = {}
    for r in rows:
        out[r["order_id"]] = Assign(
            order_id=r["order_id"], tech_id=r["technician_id"], origin_location_id=r["origin_location_id"],
            departure=int(r["departure"]), arrival=int(r["arrival"]), service_start=int(r["service_start"]),
            service_end=int(r["service_end"]), travel=int(r["travel_minutes"]), waiting=int(r["waiting_minutes"]),
            locked=bool(r.get("locked")), match_score=r.get("match_score"), score_components=r.get("score_components") or {},
        )
    return out


def persist_candidate(db: Session, clock: Clock, snap: Snapshot, cand: Candidate, *, run_id: str, target_id: str,
                      target_priority: Priority, outcome: dict[str, Any], status: PlanStatus, explanation: str) -> CandidatePlan:
    policy = get_policy()
    changed = set(cand.changed_ids)
    # store every assignment that differs from the base in ANY field (including travel-only successor updates),
    # so that re-validation at approval time reconstructs exactly the plan that was scored
    modified = {oid for oid, a in cand.plan.items() if not _same_assignment(snap.assignments.get(oid), a)}
    rows = [assignment_dict(clock, a, snap) for oid, a in sorted(cand.plan.items()) if oid in modified]
    plan = CandidatePlan(
        id=new_id("plan"), run_id=run_id, scenario_generation=clock.scenario_generation, target_order_id=target_id,
        strategy=cand.strategy or "single", base_schedule_version=snap.schedule_version, base_data_version=snap.data_version(),
        route_snapshot_id=snap.matrix.snapshot_id, policy_version=policy.policy_version, generated_at=clock.now,
        expires_at=clock.from_minutes(clock.now_minutes + policy.candidate_expiry_sim_minutes),
        assignments=[{**r, "changed": r["order_id"] in changed} for r in rows], diff=[d.as_dict() for d in cand.affected.diff if d.change != "unchanged"],
        affected_order_ids=list(cand.affected.affected_ids), policy_check=outcome, validation=cand.validation.as_dict(),
        decision_score=cand.decision_score, scores={oid: cand.plan[oid].match_score for oid in changed},
        metrics={**cand.metrics(snap), "strategy_tags": cand.strategy_tags, "target_priority": target_priority.value},
        status=status, decision=outcome.get("decision", "manual"), explanation=explanation,
    )
    db.add(plan)
    db.flush()
    return plan


def get_plan(db: Session, plan_id: str) -> CandidatePlan:
    plan = db.get(CandidatePlan, plan_id)
    if plan is None:
        raise OrderError("not_found", f"plan {plan_id} not found", status=404)
    return plan


def _existing_approval(db: Session, key: str | None) -> Approval | None:
    if not key:
        return None
    return db.scalars(select(Approval).where(Approval.idempotency_key == key)).first()


def revalidate(db: Session, clock: Clock, plan: CandidatePlan) -> tuple[Snapshot, Candidate, dict[str, Any], dict[str, Any] | None]:
    """Re-run validation/affected/authority/score for a stored plan against CURRENT facts. Returns conflict when stale."""
    policy = get_policy()
    if plan.scenario_generation != clock.scenario_generation:
        return None, None, {}, {"code": "scenario_reset", "message": "plan belongs to a previous scenario generation"}  # type: ignore[return-value]
    if plan.status not in (PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED):
        return None, None, {}, {"code": "plan_not_pending", "message": f"plan status is {plan.status}"}  # type: ignore[return-value]
    if clock.now > plan.expires_at:
        return None, None, {}, {"code": "plan_expired", "message": "candidate expired; recompute required"}  # type: ignore[return-value]
    target = db.get(WorkOrder, plan.target_order_id)
    if target is None or target.lifecycle_status in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
        return None, None, {}, {"code": "target_closed", "message": "target order is cancelled or completed"}  # type: ignore[return-value]
    if target.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
        return None, None, {}, {"code": "target_departed", "message": "target order already departed"}  # type: ignore[return-value]
    snap = build_snapshot(db, clock)
    if snap.schedule_version != plan.base_schedule_version:
        return None, None, {}, {"code": "schedule_changed", "message": f"schedule version moved from {plan.base_schedule_version} to {snap.schedule_version}"}  # type: ignore[return-value]
    # Orders in the plan (and the target) must be unchanged since generation; technician facts (availability,
    # actual execution timing) are re-checked by validate_plan against the current snapshot below.
    current_versions = snap.data_version()
    involved_orders = {r["order_id"] for r in plan.assignments} | {plan.target_order_id}
    stale = [k for k, v in (plan.base_data_version or {}).items() if current_versions.get(k) != v
             and k.startswith("order:") and k.split(":", 1)[1] in involved_orders]
    if stale:
        return None, None, {}, {"code": "facts_changed", "message": "underlying facts changed", "details": {"stale": stale}}  # type: ignore[return-value]
    full = dict(snap.assignments)
    full.update(plan_from_rows(plan.assignments))
    target_priority = Priority(target.effective_priority)
    cand = evaluate_plan(snap, full, {plan.target_order_id}, target_priority, policy)
    outcome = decide(target_priority=target_priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                     authority=cand.authority, policy=policy, has_changes=bool(cand.changed_ids)).as_dict()
    return snap, cand, outcome, None


def approve_plan(db: Session, clock: Clock, plan_id: str, *, actor: str, reason: str | None,
                 idempotency_key: str | None, expected_versions: dict[str, Any] | None) -> dict[str, Any]:
    prior = _existing_approval(db, idempotency_key)
    if prior is not None:
        return prior.result
    plan = get_plan(db, plan_id)
    if plan.status == PlanStatus.OVER_LIMIT:
        raise OrderError("over_limit", "plan exceeds the priority's authority and cannot be approved", status=409)
    if expected_versions:
        current = build_snapshot(db, clock).data_version()
        mismatch = {k: (v, current.get(k)) for k, v in expected_versions.items() if current.get(k) != v}
        if mismatch:
            raise OrderError("version_conflict", "expected versions do not match current state", {"mismatch": mismatch}, status=409)
    # an expedite / restore plan carries the window it was searched with: it applies together with the plan (and only then)
    expedite_window = (plan.metrics or {}).get("expedite_window")
    target = db.get(WorkOrder, plan.target_order_id)
    saved_window = None
    if expedite_window and target is not None:
        from app.services.timeutil import parse_iso
        saved_window = (target.window_start, target.window_end)
        target.window_start, target.window_end = parse_iso(expedite_window["start"]), parse_iso(expedite_window["end"])
        db.flush()

    def restore_window() -> None:
        if saved_window is not None and target is not None:
            target.window_start, target.window_end = saved_window
            db.flush()

    snap, cand, outcome, conflict = revalidate(db, clock, plan)
    if conflict is not None:
        restore_window()
        if conflict["code"] in ("plan_expired", "schedule_changed", "facts_changed"):
            plan.status = PlanStatus.EXPIRED
            plan.status_reason = conflict["message"]
        elif conflict["code"] in ("target_closed", "target_departed"):
            plan.status = PlanStatus.INVALIDATED
            plan.status_reason = conflict["message"]
        raise OrderError(conflict["code"], conflict["message"], conflict.get("details"), status=409)
    if not cand.validation.ok or not cand.authority.ok:
        restore_window()
        plan.status = PlanStatus.INVALIDATED
        plan.status_reason = "re-validation failed at approval time"
        raise OrderError("revalidation_failed", "plan no longer satisfies hard constraints or authority",
                         {"validation": cand.validation.as_dict(), "authority": cand.authority.as_dict()}, status=409)
    if outcome["decision"] == PolicyDecision.FORBIDDEN.value:
        restore_window()
        raise OrderError("forbidden", "policy forbids this plan", outcome, status=409)
    version = commit_plan(db, clock, snap, cand.plan, reason=f"approved plan {plan.id} by {actor}", plan_id=plan.id,
                          metrics=cand.metrics(snap))
    plan.status = PlanStatus.COMMITTED
    plan.status_reason = f"approved by {actor}"
    supersede_siblings(db, plan)
    if target is not None:
        target.pending_plan_run_id = None
        from app.services import risk_service
        risk_service.evaluate_order(db, clock, build_snapshot(db, clock, matrix=snap.matrix), target)
    result = {"plan_id": plan.id, "status": plan.status, "schedule_version": version.id, "decision": outcome,
              "affected_order_ids": plan.affected_order_ids}
    _close_approval_case(db, clock, plan.target_order_id, f"plan {plan.id} approved by {actor}")
    db.add(Approval(id=new_id("apr"), plan_id=plan.id, actor=actor, decision="approve", reason=reason,
                    expected_versions=expected_versions or {}, idempotency_key=idempotency_key, result=result))
    if expedite_window and target is not None:
        from app.services import expedite_service
        expedite_service.on_plan_committed(db, clock, snap, cand, plan, target)
    else:
        _notify_commit(db, clock, snap, cand, plan.target_order_id)
    db.flush()
    return result


def reject_plan(db: Session, clock: Clock, plan_id: str, *, actor: str, reason: str | None, idempotency_key: str | None) -> dict[str, Any]:
    prior = _existing_approval(db, idempotency_key)
    if prior is not None:
        return prior.result
    plan = get_plan(db, plan_id)
    if plan.status not in (PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED, PlanStatus.OVER_LIMIT):
        raise OrderError("plan_not_pending", f"plan status is {plan.status}", status=409)
    plan.status = PlanStatus.REJECTED
    plan.status_reason = f"rejected by {actor}: {reason or ''}".strip()
    siblings_pending = [p for p in db.scalars(select(CandidatePlan).where(
        CandidatePlan.run_id == plan.run_id, CandidatePlan.id != plan.id,
        CandidatePlan.status.in_([PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED]))).all()]
    target = db.get(WorkOrder, plan.target_order_id)
    if target is not None and not siblings_pending and target.scheduling_status == SchedulingStatus.PENDING_REVIEW:
        target.scheduling_status = SchedulingStatus.UNRESOLVED
        target.pending_plan_run_id = None
    result = {"plan_id": plan.id, "status": plan.status, "remaining_pending": [p.id for p in siblings_pending]}
    if not siblings_pending:
        _close_approval_case(db, clock, plan.target_order_id, f"plan {plan.id} rejected by {actor}: {reason or ''}".strip())
        if (plan.metrics or {}).get("expedite_window") and target is not None:
            from app.services import expedite_service
            expedite_service.on_plan_rejected(db, clock, plan, target)
    db.add(Approval(id=new_id("apr"), plan_id=plan.id, actor=actor, decision="reject", reason=reason,
                    idempotency_key=idempotency_key, result=result))
    db.flush()
    return result


def supersede_siblings(db: Session, plan: CandidatePlan) -> None:
    for p in db.scalars(select(CandidatePlan).where(CandidatePlan.run_id == plan.run_id, CandidatePlan.id != plan.id)).all():
        if p.status in (PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED, PlanStatus.OVER_LIMIT):
            p.status = PlanStatus.SUPERSEDED
            p.status_reason = f"sibling {plan.id} committed"


def invalidate_plans_for_order(db: Session, order_id: str, reason: str) -> int:
    """Candidates/approvals containing (or targeting) an order become invalid, e.g. after customer cancel."""
    n = 0
    for p in db.scalars(select(CandidatePlan).where(CandidatePlan.status.in_(
            [PlanStatus.PENDING_REVIEW, PlanStatus.PROPOSED, PlanStatus.OVER_LIMIT]))).all():
        involved = p.target_order_id == order_id or order_id in (p.affected_order_ids or []) \
            or any(r["order_id"] == order_id for r in (p.assignments or []))
        if involved:
            p.status = PlanStatus.INVALIDATED
            p.status_reason = reason
            n += 1
            t = db.get(WorkOrder, p.target_order_id)
            if t is not None and t.id != order_id and t.scheduling_status == SchedulingStatus.PENDING_REVIEW:
                t.scheduling_status = SchedulingStatus.UNRESOLVED if t.technician_id is None else t.scheduling_status
                t.pending_plan_run_id = None
    db.flush()
    return n


def _notify_commit(db: Session, clock: Clock, snap: Snapshot, cand: Candidate, target_id: str) -> None:
    from app.services.timeutil import hhmm
    for oid in cand.changed_ids:
        a = cand.plan[oid]
        order = db.get(WorkOrder, oid)
        if order is None:
            continue
        eta = hhmm(clock.from_minutes(a.service_start))
        kind = "eta_update" if oid != target_id else "assignment"
        notify(db, clock, recipient_ref=order.customer_ref, recipient_type="customer", type=kind, order_id=oid,
               message=f"[simulated] {snap.techs[a.tech_id].name} is scheduled to start at {eta} for order {oid}.",
               dedupe_key=f"{oid}:{a.tech_id}:{a.service_start}:{snap.schedule_version}")
        notify(db, clock, recipient_ref=a.tech_id, recipient_type="technician", type="task_update", order_id=oid,
               message=f"[simulated] New task {oid} at {snap.location_names.get(snap.orders[oid].location_id, snap.orders[oid].location_id)}, start {eta}.",
               dedupe_key=f"tech:{oid}:{a.tech_id}:{a.service_start}:{snap.schedule_version}")


def _close_approval_case(db: Session, clock: Clock, order_id: str, resolution: str) -> None:
    """The review queue and the human queue are one thing: deciding the plan resolves the policy-required case."""
    from app.models.entities import HumanCase
    for c in db.scalars(select(HumanCase).where(HumanCase.order_id == order_id, HumanCase.category == "plan_approval",
                                                HumanCase.status.in_(["pending", "in_progress", "waiting_customer"]))).all():
        c.status, c.resolution, c.resolved_at, c.updated_at = "resolved", resolution, clock.now, clock.now
    db.flush()
