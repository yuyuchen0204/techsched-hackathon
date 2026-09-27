"""Initial batch scheduling through the PolicyEngine (first version: any low score → whole batch to review)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import WorkOrder
from app.models.enums import PlanStatus, PolicyDecision, Priority, RunStatus, SchedulingStatus
from app.scheduling.policy import decide
from app.scheduling.solver import solve_initial
from app.services import plan_service, risk_service
from app.services.agent_runs import start_run
from app.services.clock import get_clock
from app.services.notification_service import notify
from app.services.schedule_service import commit_plan
from app.services.snapshot import build_snapshot


def run_initial(db: Session, order_ids: list[str] | None = None) -> dict[str, Any]:
    clock = get_clock(db)
    policy = get_policy()
    tracer = start_run(db, agent="SchedulingAgent", trigger="initial_schedule", scenario_generation=clock.scenario_generation)
    snap = build_snapshot(db, clock)
    targets = order_ids or [o.id for o in snap.orders.values() if o.id not in snap.assignments and not o.closed
                            and o.lifecycle_status == "OPEN"]
    targets = [t for t in targets if t in snap.orders and t not in snap.assignments and not snap.orders[t].closed]
    with tracer.step("solve_initial") as st:
        res = solve_initial(snap, targets, policy)
        tracer.tool(st, "solve_initial", status=res.status.value, placed=res.explored, unassigned=res.unassigned,
                    elapsed_ms=res.elapsed_ms, reasons=res.unassigned_reasons)
        st["summary"] = f"{res.status.value}: {res.explored} placed, {len(res.unassigned)} unassigned in {res.elapsed_ms} ms"
    if not res.candidates:
        for oid in res.unassigned:
            o = db.get(WorkOrder, oid)
            if o:
                o.scheduling_status = SchedulingStatus.UNRESOLVED
        tracer.finish(RunStatus.UNRESOLVED, res.reason or "nothing placed", {"unassigned": res.unassigned, "reasons": res.unassigned_reasons})
        return {"run_id": tracer.run.id, "status": res.status.value, "decision": "unresolved", "unassigned": res.unassigned,
                "unassigned_reasons": res.unassigned_reasons, "plan_id": None}
    cand = res.candidates[0]
    # authority for a batch: zero disturbance of committed orders, so the P3 rule is the right check
    outcome = decide(target_priority=Priority.P3, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                     authority=cand.authority, policy=policy, has_changes=bool(cand.changed_ids), search_incomplete=res.search_incomplete).as_dict()
    if outcome["decision"] == PolicyDecision.MANUAL.value:
        outcome["reasons"].append("initial batch contains a low-score assignment: whole batch goes to review")
    status = PlanStatus.PENDING_REVIEW if outcome["decision"] == PolicyDecision.MANUAL.value else PlanStatus.PROPOSED
    explanation = f"Initial schedule: {len(cand.changed_ids)} order(s) placed, {len(res.unassigned)} unassigned. " + \
        (f"Lowest assignment score {cand.decision_score:.1f}. " if cand.decision_score is not None else "") + \
        "Policy: " + outcome["decision"] + " — " + "; ".join(outcome["reasons"][-2:])
    plan = plan_service.persist_candidate(db, clock, snap, cand, run_id=tracer.run.id, target_id="batch", target_priority=Priority.P3,
                                          outcome=outcome, status=status, explanation=explanation)
    plan.metrics = {**plan.metrics, "unassigned": res.unassigned, "unassigned_reasons": res.unassigned_reasons, "batch": True}
    for oid in res.unassigned:
        o = db.get(WorkOrder, oid)
        if o:
            o.scheduling_status = SchedulingStatus.UNRESOLVED
    if outcome["decision"] == PolicyDecision.AUTO.value:
        version = commit_plan(db, clock, snap, cand.plan, reason="initial schedule (auto)", plan_id=plan.id, metrics=cand.metrics(snap))
        plan.status = PlanStatus.COMMITTED
        plan.status_reason = "auto-committed by policy"
        plan_service._notify_commit(db, clock, snap, cand, "batch")
        after = build_snapshot(db, clock, matrix=snap.matrix)
        for oid in cand.changed_ids:
            o = db.get(WorkOrder, oid)
            if o:
                risk_service.evaluate_order(db, clock, after, o)
        tracer.finish(RunStatus.COMPLETED, f"auto-committed initial schedule v{version.id}", {"plan_id": plan.id, "schedule_version": version.id})
        return {"run_id": tracer.run.id, "status": res.status.value, "decision": "auto", "plan_id": plan.id, "schedule_version": version.id,
                "unassigned": res.unassigned, "unassigned_reasons": res.unassigned_reasons}
    for oid in cand.changed_ids:
        o = db.get(WorkOrder, oid)
        if o:
            o.scheduling_status = SchedulingStatus.PENDING_REVIEW
            o.pending_plan_run_id = tracer.run.id
    notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="review_required",
           message=f"[simulated] Initial schedule needs review (lowest score {cand.decision_score:.1f}).", dedupe_key=f"initial:{tracer.run.id}")
    tracer.finish(RunStatus.PENDING_REVIEW, "initial schedule awaits review", {"plan_id": plan.id})
    return {"run_id": tracer.run.id, "status": res.status.value, "decision": "manual", "plan_id": plan.id,
            "unassigned": res.unassigned, "unassigned_reasons": res.unassigned_reasons}
