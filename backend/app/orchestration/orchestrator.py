"""Deterministic Orchestrator: received → load_snapshot → classify_priority → standby/solve → validate → score →
policy → pending_review / commit / unresolved → completed. Approval waits are persisted, never held in memory."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.agents.scheduling_agent import explain_candidate
from app.config import get_policy
from app.models.entities import CandidatePlan, WorkOrder
from app.models.enums import (
    LifecycleStatus,
    PlanStatus,
    PolicyDecision,
    Priority,
    RunStatus,
    SchedulingStatus,
)
from app.scheduling.domain import Snapshot
from app.scheduling.policy import decide
from app.scheduling.solver import Candidate, SolveResult, solve_insert
from app.services import plan_service, risk_service, standby_service
from app.services.agent_runs import start_run
from app.services.clock import get_clock
from app.services.notification_service import notify
from app.services.schedule_service import commit_plan
from app.services.snapshot import build_snapshot


@dataclass
class DispatchOutcome:
    run_id: str
    status: str
    decision: str
    target_order_id: str
    plan_ids: list[str] = field(default_factory=list)
    committed_plan_id: str | None = None
    schedule_version: int | None = None
    reason: str | None = None
    solve_status: str | None = None
    affected_count: int = 0  # affected orders of the committed plan (0 unless auto-committed)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def dispatch_key(order: WorkOrder, snap: Snapshot) -> str:
    techs = ",".join(f"{t.id}:{t.version}" for t in sorted(snap.techs.values(), key=lambda t: t.id))
    return f"{order.effective_priority}|{snap.schedule_version}|{order.version}|{hash(techs) & 0xffffffff:x}"


def _needs_dispatch(order: WorkOrder, snap: Snapshot, state: risk_service.OrderRiskState) -> tuple[bool, str]:
    if order.lifecycle_status in (LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED):
        return False, "order closed"
    if order.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"):
        return False, "order already departed; execution is locked"
    if order.id not in snap.assignments:
        return True, "no valid assignment"
    if not state.has_valid_assignment:
        return True, "assignment no longer valid"
    spec = snap.orders[order.id]
    a = snap.assignments[order.id]
    if spec.recovery_target or a.service_start > spec.window_end:
        return True, "predicted late / overdue"
    return False, "valid on-time assignment exists"


PREFERENCE_WEIGHT = 8.0  # ranking-only opportunity cost per unit of penalty (never changes the 0–100 score or the threshold)


def _pick_execution_candidate(cands: list[Candidate], penalties: dict[str, float] | None = None, target_id: str | None = None) -> Candidate | None:
    if not cands:
        return None

    def rank(c: Candidate) -> float:
        base = c.decision_score or 0.0
        if penalties and target_id and target_id in c.plan:
            base -= PREFERENCE_WEIGHT * penalties.get(c.plan[target_id].tech_id, 0.0)
        return base

    return max(cands, key=lambda c: (rank(c), -c.target_start, -c.affected.count))


def dispatch_order(db: Session, order_id: str, *, trigger: str, force_manual_if_affected: bool = False,
                   allow_relocate: bool = True) -> DispatchOutcome:
    """force_manual_if_affected: another target of the SAME event already moved other orders — a second set of moves
    must be reviewed so that split auto-commits cannot add up beyond one target's limit (brief §10.1)."""
    clock = get_clock(db)
    order = db.get(WorkOrder, order_id)
    if order is None:
        raise ValueError(f"order {order_id} not found")
    tracer = start_run(db, agent="Orchestrator", trigger=trigger, scenario_generation=clock.scenario_generation,
                       target_order_id=order_id, input_refs={"order_id": order_id, "order_version": order.version})
    run = tracer.run
    policy = get_policy()
    try:
        with tracer.step("load_snapshot", agent="SchedulingAgent") as st:
            snap = build_snapshot(db, clock)
            tracer.tool(st, "load_snapshot", schedule_version=snap.schedule_version, orders=len(snap.orders),
                        technicians=len(snap.techs), route_snapshot_id=snap.matrix.snapshot_id,
                        route_degraded=snap.matrix.degraded)
        with tracer.step("classify_priority", agent="RiskMonitoringAgent") as st:
            state = risk_service.evaluate_order(db, clock, snap, order)
            snap = build_snapshot(db, clock, matrix=snap.matrix) if state.changed else snap
            tracer.tool(st, "evaluate_risk", effective_priority=order.effective_priority,
                        reasons=[r.as_dict() for r in state.reasons], has_valid_assignment=state.has_valid_assignment)
            st["summary"] = f"effective priority {order.effective_priority}"
        priority = Priority(order.effective_priority)
        needed, why = _needs_dispatch(order, snap, state)
        if not needed:
            if priority == Priority.P2 and state.has_valid_assignment:
                with tracer.step("standby", agent="SchedulingAgent") as st:
                    rows = standby_service.refresh_standby(db, clock, snap, order)
                    tracer.tool(st, "find_standby_candidates", count=len(rows),
                                technicians=[r.technician_id for r in rows])
                    st["summary"] = f"P2 with valid assignment: kept; {len(rows)} standby candidate(s) prepared"
                tracer.finish(RunStatus.COMPLETED, f"P2 kept; {len(rows)} standby prepared", {"decision": "standby"})
                order.last_dispatch_key = dispatch_key(order, snap)
                return DispatchOutcome(run.id, RunStatus.COMPLETED, PolicyDecision.STANDBY.value, order_id, reason=why)
            tracer.finish(RunStatus.NO_ACTION, why, {"decision": "no_action"})
            order.last_dispatch_key = dispatch_key(order, snap)
            return DispatchOutcome(run.id, RunStatus.NO_ACTION, PolicyDecision.NO_ACTION.value, order_id, reason=why)

        spec = snap.orders[order_id]
        existing = snap.assignments.get(order_id)
        with tracer.step("solve", agent="SchedulingAgent") as st:
            res: SolveResult = solve_insert(snap, spec, policy, allow_relocate=allow_relocate)
            tracer.tool(st, "solve_repair" if existing or priority in (Priority.P0, Priority.P1) else "solve_insert",
                        status=res.status.value, explored=res.explored, elapsed_ms=res.elapsed_ms,
                        candidates=len(res.candidates), over_limit=len(res.over_limit), reason=res.reason)
            st["summary"] = f"{res.status.value}: {len(res.candidates)} candidate(s) in {res.elapsed_ms} ms"
        # an existing (late) assignment is only replaced by an improvement
        if existing is not None:
            res.candidates = [c for c in res.candidates if c.target_start < existing.service_start]
            if not res.candidates:
                # the current (late / recovery) assignment is already the earliest feasible option → keep it quietly
                why = "current assignment is the earliest feasible; kept"
                order.last_dispatch_key = dispatch_key(order, snap)
                tracer.finish(RunStatus.NO_ACTION, why, {"decision": "no_action", "solve_status": res.status.value})
                return DispatchOutcome(run.id, RunStatus.NO_ACTION, PolicyDecision.NO_ACTION.value, order_id, reason=why,
                                       solve_status=res.status.value)

        plan_ids: list[str] = []
        outcomes: dict[int, dict[str, Any]] = {}
        with tracer.step("validate_score_policy", agent="PolicyEngine") as st:
            for cand in res.candidates:
                outcome = decide(target_priority=priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                                 authority=cand.authority, policy=policy, has_changes=bool(cand.changed_ids),
                                 search_incomplete=res.search_incomplete)
                od = outcome.as_dict()
                if force_manual_if_affected and cand.affected.count > 0 and od["decision"] == PolicyDecision.AUTO.value:
                    od["decision"] = PolicyDecision.MANUAL.value
                    od["reasons"].append("another order of the same event already moved other orders; a second set of moves requires review")
                outcomes[id(cand)] = od
                tracer.tool(st, "validate_plan", strategy=cand.strategy, ok=cand.validation.ok, affected=cand.affected.count)
                tracer.tool(st, "score_plan", strategy=cand.strategy, decision_score=cand.decision_score)
                tracer.tool(st, "policy", strategy=cand.strategy, decision=od["decision"], reasons=od["reasons"])
            st["summary"] = ", ".join(f"{c.strategy}={outcomes[id(c)]['decision']}" for c in res.candidates) or "no candidates"

        penalties = snap.preference_penalties.get(order_id, {})
        chosen = _pick_execution_candidate(res.candidates, penalties, order_id)
        chosen_outcome = outcomes[id(chosen)] if chosen else None
        if penalties and chosen is not None:
            best_raw = max(res.candidates, key=lambda c: (c.decision_score or 0))
            if best_raw is not chosen:
                chosen_outcome = {**(chosen_outcome or {}), "preference_effect": {
                    "demoted_technician": best_raw.plan[order_id].tech_id if order_id in best_raw.plan else None,
                    "chosen_technician": chosen.plan[order_id].tech_id, "penalties": penalties,
                    "note": "customer's past negative rating applied as a ranking term; scores/threshold unchanged"}}
                outcomes[id(chosen)] = chosen_outcome
        auto = chosen is not None and chosen_outcome is not None and chosen_outcome["decision"] == PolicyDecision.AUTO.value

        # persist candidates (preview only; nothing takes effect until commit_plan)
        for cand in res.candidates:
            od = outcomes[id(cand)]
            status = PlanStatus.PENDING_REVIEW if od["decision"] == PolicyDecision.MANUAL.value else PlanStatus.PROPOSED
            if auto and cand is not chosen:
                status = PlanStatus.SUPERSEDED
            p = plan_service.persist_candidate(db, clock, snap, cand, run_id=run.id, target_id=order_id, target_priority=priority,
                                               outcome=od, status=status, explanation=explain_candidate(clock, snap, cand, order_id, od))
            plan_ids.append(p.id)
        for cand in res.over_limit:
            od = decide(target_priority=priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok,
                        authority=cand.authority, policy=policy, has_changes=True).as_dict()
            cand.strategy = "over_limit"
            # explanation only: an alert while the target has no approvable plan; superseded once another plan commits
            over_status = PlanStatus.SUPERSEDED if auto else PlanStatus.OVER_LIMIT
            p = plan_service.persist_candidate(db, clock, snap, cand, run_id=run.id, target_id=order_id, target_priority=priority,
                                               outcome=od, status=over_status,
                                               explanation="Exceeds authority: " + "; ".join(od["reasons"]) + " — shown for explanation only.")
            if auto:
                p.status_reason = "target committed by an in-authority plan"
            plan_ids.append(p.id)
            if not auto and not res.candidates:
                notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="authority_alert", order_id=order_id,
                       message=f"[simulated] {priority.value} order {order_id} only recoverable by affecting {cand.affected.count} orders "
                               f"(limit {cand.authority.max_affected if cand.authority.max_affected is not None else 'none'}). Manual handling required.",
                       dedupe_key=f"overlimit:{order_id}:{snap.schedule_version}")

        if auto and chosen is not None:
            with tracer.step("commit", agent="Orchestrator") as st:
                version = commit_plan(db, clock, snap, chosen.plan, reason=f"auto {priority.value} {trigger} {order_id}",
                                      plan_id=plan_ids[res.candidates.index(chosen)], metrics=chosen.metrics(snap))
                plan = db.get(CandidatePlan, plan_ids[res.candidates.index(chosen)])
                assert plan is not None
                plan.status = PlanStatus.COMMITTED
                plan.status_reason = "auto-committed by policy"
                plan_service._notify_commit(db, clock, snap, chosen, order_id)
                tracer.tool(st, "commit_plan", schedule_version=version.id, affected=chosen.affected.affected_ids)
                st["summary"] = f"committed schedule version {version.id}"
            order.pending_plan_run_id = None
            after = build_snapshot(db, clock, matrix=snap.matrix)
            risk_service.evaluate_order(db, clock, after, order)  # resolve risks that the new assignment addressed
            order.last_dispatch_key = dispatch_key(order, after)
            tracer.finish(RunStatus.COMPLETED, f"auto-committed {chosen.strategy} (score {chosen.decision_score:.1f})",
                          {"decision": "auto", "plan_id": plan.id, "schedule_version": version.id})
            return DispatchOutcome(run.id, RunStatus.COMPLETED, PolicyDecision.AUTO.value, order_id, plan_ids, plan.id,
                                   version.id, solve_status=res.status.value, affected_count=chosen.affected.count)

        if res.candidates:
            if existing is None:
                order.scheduling_status = SchedulingStatus.PENDING_REVIEW
            order.pending_plan_run_id = run.id
            order.last_dispatch_key = dispatch_key(order, snap)
            from app.services import (
                human_service,  # policy-required human case (§9): approval queue == human queue
            )
            human_service.flag_for_human(
                db, clock, source="POLICY_REQUIRED", category="plan_approval", urgency="high" if order.effective_priority in ("P0", "P1") else "normal",
                reason_summary=f"{order.id} ({order.effective_priority}): {chosen_outcome['reasons'][-1] if chosen_outcome and chosen_outcome.get('reasons') else 'plan needs approval'}",
                customer_id=order.customer_id, order_id=order.id, evidence_refs=[f"plan:{pid}" for pid in plan_ids] + [f"run:{run.id}"],
                suggested_next_action="approve or reject a candidate plan in the review queue")
            tracer.finish(RunStatus.PENDING_REVIEW, f"{len(res.candidates)} candidate(s) await dispatcher review",
                          {"decision": "manual", "plan_ids": plan_ids})
            return DispatchOutcome(run.id, RunStatus.PENDING_REVIEW, PolicyDecision.MANUAL.value, order_id, plan_ids,
                                   solve_status=res.status.value, reason=chosen_outcome["reasons"][-1] if chosen_outcome else None)

        if existing is None:
            order.scheduling_status = SchedulingStatus.UNRESOLVED
        order.last_dispatch_key = dispatch_key(order, snap)
        reason = res.reason or "no feasible candidate"
        # hand the hard case to the agent runtime: it investigates with tools (other windows, customer question, human)
        from app.agents import runtime as agent_runtime
        task = agent_runtime.create_task(
            db, clock, role="recovery" if ("unavailable" in trigger or "risk" in trigger or "clock" in trigger or priority in (Priority.P0, Priority.P1)) else "scheduling",
            goal=f"{priority.value} order {order_id} has no admissible plan after direct dispatch ({reason}); find a way or escalate with evidence",
            order_id=order_id, customer_id=order.customer_id, session_id=order.customer_ref if order.customer_ref.startswith("sess") else None,
            facts={"fast_path": {"status": res.status.value, "reason": reason, "over_limit": [c.affected.count for c in res.over_limit]},
                   "priority": priority.value, "trigger": trigger}, dedupe_key=f"sched:{order_id}:{snap.schedule_version}")
        task_id = task.id if task else None
        if task is not None and task.execution_mode == "mock":
            agent_runtime.run_task(db, task)
        notify(db, clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="unresolved", order_id=order_id,
               message=f"[simulated] {priority.value} order {order_id} unresolved: {reason}" + (f" — agent task {task_id} investigating" if task_id else ""),
               dedupe_key=f"unresolved:{order_id}:{snap.schedule_version}:{order.effective_priority}")
        tracer.finish(RunStatus.UNRESOLVED, reason, {"decision": "unresolved", "solve_status": res.status.value, "plan_ids": plan_ids, "agent_task_id": task_id})
        return DispatchOutcome(run.id, RunStatus.UNRESOLVED, PolicyDecision.UNRESOLVED.value, order_id, plan_ids,
                               reason=reason, solve_status=res.status.value)
    except Exception as exc:
        tracer.finish(RunStatus.FAILED, "dispatch failed", error=str(exc))
        raise


def dispatch_pending(db: Session, *, trigger: str, order_ids: list[str] | None = None,
                     force_manual_if_affected: bool = False) -> list[DispatchOutcome]:
    """Process pending targets in explicit order: most urgent first, then earliest deadline, stable id."""
    clock = get_clock(db)
    orders = risk_service.pending_orders(db, clock)
    if order_ids is not None:
        wanted = set(order_ids)
        orders = [o for o in orders if o.id in wanted]
    orders.sort(key=lambda o: (Priority(o.effective_priority).rank, o.window_end, o.id))
    out: list[DispatchOutcome] = []
    for o in orders:
        out.append(dispatch_order(db, o.id, trigger=trigger, force_manual_if_affected=force_manual_if_affected))
    return out
