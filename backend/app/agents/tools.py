"""Agent tool contracts (V3 §11.2/11.3): every tool validates identity/params/authority itself, returns a structured
ToolResult, and touches business state only through the protected services. The model never bypasses these."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import AgentTask, CandidatePlan, CustomerQuestion, Technician, WorkOrder
from app.models.enums import PlanStatus, Priority, SolveStatus
from app.scheduling.policy import decide
from app.scheduling.solver import solve_insert
from app.services import (
    break_service,
    catalog_service,
    customer_service,
    human_service,
    negotiation_service,
    plan_service,
    risk_service,
    safety_service,
)
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.notification_service import notify
from app.services.order_service import OrderError
from app.services.snapshot import build_snapshot
from app.services.timeutil import iso, parse_iso

STATUS = ("ok", "infeasible", "no_solution_found", "budget_exhausted", "error", "data_incomplete", "stale", "forbidden", "waiting")


@dataclass
class ToolResult:
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    snapshot_version: int | None = None
    wait: str | None = None  # "customer" | "human" → the task pauses

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "snapshot_version": self.snapshot_version, "data": self.data,
                "reason_codes": self.reason_codes, "evidence_refs": self.evidence_refs}


@dataclass
class ToolContext:
    db: Session
    clock: Clock
    role: str                       # customer | scheduling | recovery | break | dispatcher
    task_id: str | None = None
    customer_id: str | None = None
    session_id: str | None = None
    order_id: str | None = None
    technician_id: str | None = None
    search_budget_used: int = 0
    # The role's skill file may withhold tools the role gate would allow (see agents/skills.py). None = no narrowing.
    allowed_tools: frozenset[str] | None = None
    delegation_depth: int = 0


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]          # JSON schema of args
    writes: bool
    roles: tuple[str, ...]
    handler: Callable[[ToolContext, dict[str, Any]], ToolResult]
    is_search: bool = False


def _v(snap_or_db: Any) -> int | None:
    try:
        return int(snap_or_db.schedule_version)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ read-only tools
def t_search_repair_catalog(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    q = str(a.get("query", "")).strip()
    items = catalog_service.search(ctx.db, q, limit=int(a.get("limit", 6)))
    return ToolResult("ok" if items else "data_incomplete",
                      {"items": [catalog_service.snapshot_of(i) for i in items]}, [] if items else ["NO_CATALOG_MATCH"],
                      [f"catalog:{i.id}" for i in items])


def t_search_address(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    from app.providers.geocode.factory import enabled, geocode
    q = str(a.get("query", "")).strip()
    if not q:
        return ToolResult("data_incomplete", reason_codes=["EMPTY_QUERY"])
    if not enabled():
        return ToolResult("error", reason_codes=["TOOL_UNAVAILABLE"], data={"note": "geocoding disabled"})
    out = geocode(q, limit=5)
    return ToolResult("ok" if out.results else "data_incomplete", {"candidates": [r.as_dict() for r in out.results], "provider": out.provider,
                      "degraded": out.degraded, "reason": out.reason}, [] if out.results else ["NO_ADDRESS_MATCH"])


def t_get_customer_history(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    cid = str(a.get("customer_id") or ctx.customer_id or "")
    if not cid:
        return ToolResult("data_incomplete", reason_codes=["NO_CUSTOMER"])
    if ctx.role == "customer" and cid != ctx.customer_id:
        return ToolResult("forbidden", reason_codes=["NOT_OWN_CUSTOMER"])
    try:
        hist = customer_service.customer_history(ctx.db, ctx.clock, cid, role="technician" if ctx.role == "technician" else "dispatcher")
    except OrderError as exc:
        return ToolResult("error", reason_codes=[exc.kind], data={"message": exc.message})
    return ToolResult("ok", hist, [], [f"customer:{cid}"])


def t_get_order_context(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    oid = str(a.get("order_id") or ctx.order_id or "")
    o = ctx.db.get(WorkOrder, oid)
    if o is None:
        return ToolResult("error", reason_codes=["NOT_FOUND"], data={"order_id": oid})
    if ctx.role == "customer" and not (o.customer_ref == ctx.session_id or (ctx.customer_id and o.customer_id == ctx.customer_id)):
        return ToolResult("forbidden", reason_codes=["NOT_OWN_ORDER"])
    snap = build_snapshot(ctx.db, ctx.clock)
    spec = snap.orders.get(oid)
    asg = snap.assignments.get(oid)
    risks = risk_service.active_risks(ctx.db, oid)
    plans = ctx.db.scalars(select(CandidatePlan).where(CandidatePlan.target_order_id == oid).order_by(CandidatePlan.generated_at.desc()).limit(5)).all()
    data = {"order_id": oid, "lifecycle_status": o.lifecycle_status, "scheduling_status": o.scheduling_status, "effective_priority": o.effective_priority,
            "priority_reasons": o.priority_reasons, "window": [iso(o.window_start), iso(o.window_end)], "version": o.version,
            "problem": o.catalog_snapshot, "location_id": o.location_id, "address": (o.address or {}).get("formatted_address"),
            "excluded_technicians": o.excluded_technician_ids, "recovery_target": bool(spec and spec.recovery_target),
            "assignment": None if asg is None else {"technician_id": asg.tech_id, "service_start": iso(ctx.clock.from_minutes(asg.service_start)), "locked": asg.locked},
            "risks": [{"type": r.type, "severity": r.severity, "status": r.status} for r in risks],
            "recent_plans": [{"id": p.id, "status": p.status, "decision": p.decision, "score": p.decision_score, "affected": p.affected_order_ids} for p in plans],
            "authority": get_policy().rule(o.effective_priority).model_dump(), "now": iso(ctx.clock.now)}
    return ToolResult("ok", data, [], [f"order:{oid}"], snapshot_version=snap.schedule_version)


def t_query_technicians(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    snap = build_snapshot(ctx.db, ctx.clock)
    trade, level = a.get("trade_type"), int(a.get("min_level", 1) or 1)
    rows = []
    for t in snap.techs.values():
        if trade and t.skills.get(trade, 0) < level:
            continue
        loc, when = snap.anchor(t.id)
        rows.append({"technician_id": t.id, "name": t.name, "skills": t.skills, "status": t.status, "next_free_at": iso(ctx.clock.from_minutes(when)),
                     "next_free_location": snap.location_names.get(loc, loc), "planned_tasks": len(snap.movable_route(t.id)),
                     "shift": [iso(ctx.clock.from_minutes(t.shift_start)), iso(ctx.clock.from_minutes(t.shift_end))],
                     "unavailable": [[iso(ctx.clock.from_minutes(s)), iso(ctx.clock.from_minutes(e))] for s, e in t.unavailable]})
    return ToolResult("ok" if rows else "data_incomplete", {"technicians": rows, "filter": {"trade_type": trade, "min_level": level}},
                      [] if rows else ["NO_QUALIFIED_TECHNICIAN"], snapshot_version=snap.schedule_version)


def t_get_travel_times(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    snap = build_snapshot(ctx.db, ctx.clock)
    src = str(a.get("from_location_id", ""))
    dests = list(a.get("to_location_ids") or [])
    out = {d: snap.travel(src, d) for d in dests}
    return ToolResult("ok", {"from": src, "minutes": out, "provider": snap.matrix.provider, "degraded": snap.matrix.degraded,
                             "note": "static estimate from the route provider; not live traffic"}, snapshot_version=snap.schedule_version)


def _persist_candidates(ctx: ToolContext, snap, res, order: WorkOrder, priority: Priority, run_id: str) -> list[dict[str, Any]]:
    from app.agents.scheduling_agent import explain_candidate
    out = []
    for cand in res.candidates:
        od = decide(target_priority=priority, decision_score=cand.decision_score, validation_ok=cand.validation.ok, authority=cand.authority,
                    policy=get_policy(), has_changes=bool(cand.changed_ids), search_incomplete=res.search_incomplete).as_dict()
        p = plan_service.persist_candidate(ctx.db, ctx.clock, snap, cand, run_id=run_id, target_id=order.id, target_priority=priority, outcome=od,
                                           status=PlanStatus.PROPOSED, explanation=explain_candidate(ctx.clock, snap, cand, order.id, od))
        out.append({"plan_id": p.id, "strategy": cand.strategy, "decision_score": cand.decision_score, "affected": cand.affected.affected_ids,
                    "policy": od["decision"], "target_start": iso(ctx.clock.from_minutes(cand.target_start)),
                    "technician_id": cand.plan[order.id].tech_id if order.id in cand.plan else None, "reasons": od["reasons"][:3]})
    return out


def _solve_tool(ctx: ToolContext, a: dict[str, Any], relocate: bool) -> ToolResult:
    oid = str(a.get("order_id") or ctx.order_id or "")
    o = ctx.db.get(WorkOrder, oid)
    if o is None:
        return ToolResult("error", reason_codes=["NOT_FOUND"])
    if o.lifecycle_status != "OPEN":
        return ToolResult("infeasible", reason_codes=["ORDER_NOT_OPEN"], data={"lifecycle_status": o.lifecycle_status})
    snap = build_snapshot(ctx.db, ctx.clock)
    spec = snap.orders[oid]
    priority = Priority(o.effective_priority)
    if relocate and get_policy().rule(priority.value).max_affected == 0:
        return ToolResult("forbidden", reason_codes=["NO_MOVE_AUTHORITY"], data={"priority": priority.value}, snapshot_version=snap.schedule_version)
    res = solve_insert(snap, spec, get_policy(), allow_relocate=relocate)
    if relocate is False:
        res.candidates = [c for c in res.candidates if c.affected.count == 0]
    if not res.candidates:
        codes = []
        if res.status == SolveStatus.TIMEOUT:
            codes.append("SEARCH_BUDGET_EXHAUSTED")
        elif "qualified" in (res.reason or ""):
            codes.append("NO_QUALIFIED_TECHNICIAN")
        else:
            codes.append("NO_ZERO_DISTURBANCE_SLOT" if not relocate else "NO_PLAN_WITHIN_AUTHORITY")
        if res.over_limit:
            codes.append("ONLY_OVER_LIMIT_PLANS")
        return ToolResult("budget_exhausted" if res.status == SolveStatus.TIMEOUT else "infeasible",
                          {"reason": res.reason, "explored": res.explored, "over_limit_affected": [c.affected.count for c in res.over_limit],
                           "note": "search did not find a plan in this round (not a proof of impossibility)" if res.status == SolveStatus.TIMEOUT else None},
                          codes, snapshot_version=snap.schedule_version)
    plans = _persist_candidates(ctx, snap, res, o, priority, run_id=ctx.task_id or new_id("run"))
    return ToolResult("ok", {"candidates": plans, "explored": res.explored, "elapsed_ms": res.elapsed_ms, "search_incomplete": res.search_incomplete},
                      [], [f"plan:{p['plan_id']}" for p in plans], snapshot_version=snap.schedule_version)


def t_simulate_insertion(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    return _solve_tool(ctx, a, relocate=False)


def t_search_local_repair(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    return _solve_tool(ctx, a, relocate=True)


def t_validate_plan(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    pid = str(a.get("plan_id", ""))
    plan = ctx.db.get(CandidatePlan, pid)
    if plan is None:
        return ToolResult("error", reason_codes=["NOT_FOUND"])
    snap, cand, outcome, conflict = plan_service.revalidate(ctx.db, ctx.clock, plan)
    if conflict is not None:
        return ToolResult("stale" if conflict["code"] in ("schedule_changed", "facts_changed", "plan_expired") else "infeasible",
                          conflict, [conflict["code"].upper()], snapshot_version=None)
    return ToolResult("ok", {"decision": outcome["decision"], "decision_score": cand.decision_score, "affected": cand.affected.affected_ids,
                             "authority_ok": cand.authority.ok, "reasons": outcome["reasons"], "threshold": outcome["threshold"]},
                      [], [f"plan:{pid}"], snapshot_version=snap.schedule_version)


def t_propose_alternative_windows(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    oid = a.get("order_id") or ctx.order_id
    cat, loc, excluded = a.get("catalog_item_id"), a.get("location_id"), list(a.get("excluded_technicians") or [])
    paid = bool(a.get("paid", False))
    allow_moves = bool(a.get("allow_moves", False))
    if oid:
        o = ctx.db.get(WorkOrder, str(oid))
        if o is None:
            return ToolResult("error", reason_codes=["NOT_FOUND"])
        cat, loc, excluded, paid = o.catalog_item_id, o.location_id, list(o.excluded_technician_ids or []), o.paid_expedite
    if not cat or not loc:
        return ToolResult("data_incomplete", reason_codes=["NEED_PROBLEM_AND_LOCATION"])
    pen = customer_service.preference_penalties(ctx.db, ctx.customer_id)
    try:
        res = negotiation_service.propose_windows(ctx.db, ctx.clock, catalog_item_id=str(cat), location_id=str(loc), customer_id=ctx.customer_id,
                                                  excluded_technicians=excluded, exclude_windows=[tuple(x) for x in (a.get("exclude_windows") or [])],
                                                  after=parse_iso(a["after"]) if a.get("after") else None, count=int(a.get("count", 4)), paid=paid,
                                                  penalties=pen, allow_moves=allow_moves)
    except OrderError as exc:
        return ToolResult("error", reason_codes=[exc.kind], data={"message": exc.message})
    return ToolResult("ok" if res["status"] == "ok" else "no_solution_found", res["data"], res["reason_codes"], snapshot_version=res["snapshot_version"])


def t_evaluate_break_need(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    tid = str(a.get("technician_id") or ctx.technician_id or "")
    t = ctx.db.get(Technician, tid)
    if t is None:
        return ToolResult("error", reason_codes=["NOT_FOUND"])
    return ToolResult("ok", break_service.work_facts(ctx.db, ctx.clock, t), [], [f"technician:{tid}"])


def t_simulate_break(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    tid = str(a.get("technician_id") or ctx.technician_id or "")
    try:
        if a.get("start"):
            r = break_service.simulate_break(ctx.db, ctx.clock, tid, parse_iso(str(a["start"])), int(a.get("minutes") or get_policy().breaks.default_break_minutes))
            return ToolResult("ok" if r["status"] == "feasible" else "infeasible", r["data"], r["reason_codes"], snapshot_version=r["snapshot_version"])
        r = break_service.find_break_slots(ctx.db, ctx.clock, tid, minutes=a.get("minutes"),
                                           window_start=parse_iso(str(a["window_start"])) if a.get("window_start") else None,
                                           window_end=parse_iso(str(a["window_end"])) if a.get("window_end") else None)
        return ToolResult("ok" if r["status"] == "ok" else "infeasible", r["data"], r["reason_codes"], snapshot_version=r["snapshot_version"])
    except OrderError as exc:
        return ToolResult("error", reason_codes=[exc.kind], data={"message": exc.message})


# ------------------------------------------------------------------ agent-to-agent delegation
MAX_DELEGATION_DEPTH = 2


def t_delegate_task(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """Hand a sub-problem to another role and pause until that agent finishes.

    Bounded on purpose: depth 2, never to your own role, and the child's tool budget is carved out of the parent's
    remaining budget — so a chain of delegations can never cost more than one top-level task was allowed to spend.
    """
    from app.agents import runtime as rt
    from app.agents.skills import get_skill, load_skills
    role = str(a.get("role") or "").strip()
    goal = str(a.get("goal") or "").strip()
    if role == ctx.role:
        return ToolResult("forbidden", reason_codes=["DELEGATE_SAME_ROLE"], data={"role": role})
    if get_skill(role) is None:
        return ToolResult("data_incomplete", reason_codes=["UNKNOWN_ROLE"], data={"role": role, "known": sorted(load_skills())})
    if ctx.delegation_depth >= MAX_DELEGATION_DEPTH:
        return ToolResult("forbidden", reason_codes=["DELEGATION_DEPTH_EXCEEDED"],
                          data={"depth": ctx.delegation_depth, "max": MAX_DELEGATION_DEPTH})
    parent = ctx.db.get(AgentTask, ctx.task_id) if ctx.task_id else None
    if parent is None:
        return ToolResult("error", reason_codes=["NO_PARENT_TASK"])
    remaining = max(0, (get_policy().agent.max_tool_calls_per_wakeup) - parent.tool_budget_used)
    if remaining < 2:
        return ToolResult("budget_exhausted", reason_codes=["NO_BUDGET_TO_DELEGATE"], data={"remaining": remaining})
    order_id = str(a.get("order_id") or "") or None
    technician_id = str(a.get("technician_id") or "") or None
    child = rt.create_task(
        ctx.db, ctx.clock, role=role, goal=goal or f"sub-task delegated by {ctx.role} task {parent.id}",
        order_id=order_id, technician_id=technician_id, customer_id=parent.customer_id, session_id=parent.session_id,
        facts={"delegated_by": {"task_id": parent.id, "role": ctx.role, "reason": str(a.get("reason") or "")},
               "context": a.get("context") or {}, "budget_cap": remaining},
        dedupe_key=f"delegate:{parent.id}:{role}:{order_id or technician_id or goal[:40]}")
    if child is None:
        # an equivalent task already owns this sub-problem — that is a valid answer, not an error
        return ToolResult("ok", {"delegated": False, "note": "an equivalent task is already live for this sub-problem"},
                          ["DELEGATE_ALREADY_LIVE"])
    child.parent_task_id = parent.id
    child.delegation_depth = ctx.delegation_depth + 1
    parent.child_task_ids = list(parent.child_task_ids or []) + [child.id]
    ctx.db.flush()
    return ToolResult("waiting", {"delegated": True, "child_task_id": child.id, "role": role, "goal": child.goal,
                                  "budget_cap": remaining},
                      [], [f"task:{child.id}"], wait="agent")


# ------------------------------------------------------------------ writing tools (protected)
def t_create_customer_question(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    if not ctx.session_id:
        return ToolResult("data_incomplete", reason_codes=["NO_SESSION"])
    q = CustomerQuestion(id=new_id("q"), scenario_generation=ctx.clock.scenario_generation, session_id=ctx.session_id, customer_id=ctx.customer_id,
                         order_id=a.get("order_id") or ctx.order_id, task_id=ctx.task_id, kind=str(a.get("kind", "other")), question=str(a.get("question", "")),
                         options=list(a.get("options") or []))
    ctx.db.add(q)
    ctx.db.flush()
    notify(ctx.db, ctx.clock, recipient_ref=ctx.session_id, recipient_type="customer", type="question", order_id=q.order_id,
           message=f"[simulated] {q.question}", dedupe_key=f"q:{q.id}")
    return ToolResult("waiting", {"question_id": q.id}, [], [f"question:{q.id}"], wait="customer")


def t_flag_for_human(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    urgency = str(a.get("urgency") or "normal").lower()
    if urgency not in human_service.URGENCY_RANK:  # the model may write a priority ("P3") here — normalise, never trust
        urgency = "high" if urgency in ("p0", "p1", "urgent") else "normal"
    try:
        case, created = human_service.flag_for_human(
            ctx.db, ctx.clock, source="AGENT_ESCALATION", category=str(a.get("category", "agent_escalation")), urgency=urgency,
            reason_summary=str(a.get("reason_summary", "")), customer_id=ctx.customer_id, session_id=ctx.session_id,
            order_id=a.get("order_id") or ctx.order_id, incident_id=a.get("incident_id"), evidence_refs=list(a.get("evidence_refs") or []),
            attempted_actions=list(a.get("attempted_actions") or []), unresolved_questions=list(a.get("unresolved_questions") or []),
            suggested_next_action=str(a.get("suggested_next_action", "")), idempotency_key=a.get("idempotency_key"))
    except OrderError as exc:
        return ToolResult("error", reason_codes=[exc.kind], data={"message": exc.message})
    return ToolResult("waiting", {"human_case_id": case.id, "created": created, "status": case.status}, [], [f"human_case:{case.id}"], wait="human")


def t_create_safety_incident(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    try:
        inc = safety_service.create_incident(ctx.db, ctx.clock, danger_type=str(a.get("danger_type", "other")), description=str(a.get("description", "")),
                                             customer_id=ctx.customer_id, session_id=ctx.session_id, order_id=a.get("order_id") or ctx.order_id,
                                             known_location=a.get("known_location") or {}, detection={"by": "agent_tool"}, status="draft" if not a.get("known_location") else "open")
    except OrderError as exc:
        return ToolResult("error", reason_codes=[exc.kind], data={"message": exc.message})
    return ToolResult("ok", safety_service.incident_view(inc), [], [f"incident:{inc.id}"])


def _target_technician(plan: CandidatePlan) -> str | None:
    """Who the plan actually puts on the target order — the workload this decision creates belongs to them."""
    for row in plan.assignments or []:
        if isinstance(row, dict) and row.get("order_id") == plan.target_order_id:
            return str(row.get("technician_id") or "") or None
    return None


def t_submit_plan(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    """The model only names a plan; the PolicyEngine decides commit vs approval, after full re-validation."""
    pid = str(a.get("plan_id", ""))
    expected = a.get("expected_version")
    plan = ctx.db.get(CandidatePlan, pid)
    if plan is None:
        return ToolResult("error", reason_codes=["NOT_FOUND"])
    if expected is not None and int(expected) != plan.base_schedule_version:
        return ToolResult("stale", reason_codes=["VERSION_CONFLICT"], data={"expected": expected, "plan_base": plan.base_schedule_version})
    snap, cand, outcome, conflict = plan_service.revalidate(ctx.db, ctx.clock, plan)
    if conflict is not None:
        plan.status = PlanStatus.EXPIRED if conflict["code"] in ("plan_expired", "schedule_changed", "facts_changed") else PlanStatus.INVALIDATED
        plan.status_reason = conflict["message"]
        return ToolResult("stale", conflict, [conflict["code"].upper()])
    if not cand.validation.ok or not cand.authority.ok:
        plan.status = PlanStatus.INVALIDATED
        plan.status_reason = "re-validation failed at submit"
        return ToolResult("forbidden", {"validation": cand.validation.as_dict(), "authority": cand.authority.as_dict()}, ["POLICY_VIOLATION"])
    order = ctx.db.get(WorkOrder, plan.target_order_id)
    assert order is not None
    from app.models.enums import SchedulingStatus
    if outcome["decision"] == "auto":
        from app.services.schedule_service import commit_plan
        version = commit_plan(ctx.db, ctx.clock, snap, cand.plan, reason=f"agent submit {plan.id}", plan_id=plan.id, metrics=cand.metrics(snap))
        plan.status = PlanStatus.COMMITTED
        plan.status_reason = "auto-committed by policy (agent submit)"
        plan_service.supersede_siblings(ctx.db, plan)
        plan_service._notify_commit(ctx.db, ctx.clock, snap, cand, order.id)
        order.pending_plan_run_id = None
        risk_service.evaluate_order(ctx.db, ctx.clock, build_snapshot(ctx.db, ctx.clock, matrix=snap.matrix), order)
        return ToolResult("ok", {"submitted": "committed", "schedule_version": version.id, "affected": cand.affected.affected_ids,
                                 "technician_id": _target_technician(plan), "decision_score": cand.decision_score},
                          [], [f"plan:{pid}", f"schedule:v{version.id}"], snapshot_version=version.id)
    if outcome["decision"] == "manual":
        plan.status = PlanStatus.PENDING_REVIEW
        plan.decision = "manual"
        plan.policy_check = outcome
        if order.scheduling_status != SchedulingStatus.ASSIGNED:
            order.scheduling_status = SchedulingStatus.PENDING_REVIEW
        order.pending_plan_run_id = plan.run_id
        notify(ctx.db, ctx.clock, recipient_ref="dispatcher", recipient_type="dispatcher", type="review_required", order_id=order.id,
               message=f"[simulated] Plan {plan.id} for {order.id} needs approval: {'; '.join(outcome['reasons'][:2])}", dedupe_key=f"review:{plan.id}")
        return ToolResult("ok", {"submitted": "pending_review", "plan_id": pid, "reasons": outcome["reasons"], "affected": cand.affected.affected_ids,
                                 "technician_id": _target_technician(plan)}, [], [f"plan:{pid}"], snapshot_version=snap.schedule_version)
    return ToolResult("forbidden", {"decision": outcome["decision"], "reasons": outcome["reasons"]}, ["POLICY_VIOLATION"])


def t_submit_break(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    tid = str(a.get("technician_id") or ctx.technician_id or "")
    try:
        r = break_service.submit_break(ctx.db, ctx.clock, tid, parse_iso(str(a["start"])), int(a.get("minutes") or get_policy().breaks.default_break_minutes),
                                       expected_version=a.get("expected_version"), created_by="agent", idempotency_key=a.get("idempotency_key"),
                                       reason=str(a.get("reason", "dynamic rest")))
    except OrderError as exc:
        return ToolResult("stale" if exc.kind == "VERSION_CONFLICT" else "forbidden" if exc.kind == "POLICY_VIOLATION" else "error",
                          {"message": exc.message, **({} if not isinstance(exc.details, dict) else exc.details)}, [exc.kind])
    except KeyError:
        return ToolResult("data_incomplete", reason_codes=["START_REQUIRED"])
    return ToolResult("ok", r, [], [f"break:{r['data']['id']}"], snapshot_version=r.get("schedule_version"))


# Back-office roles have no customer session, so their only inbox is the dispatcher's.
BACK_OFFICE_ROLES = ("scheduling", "recovery", "break", "dispatcher")


def t_notify_in_app(ctx: ToolContext, a: dict[str, Any]) -> ToolResult:
    ref = str(a.get("recipient_ref") or ctx.session_id or ctx.customer_id or "")
    if not ref and ctx.role in BACK_OFFICE_ROLES:
        ref = "dispatcher"            # the operator inbox: the only recipient these roles can address
    if not ref:
        # say what a valid recipient looks like, so the next attempt can be right rather than another guess
        return ToolResult("data_incomplete", reason_codes=["NO_RECIPIENT"],
                          data={"recipient_ref": "required here: a customer session id, a customer id, or 'dispatcher'",
                                "recipient_type": ["customer", "technician", "dispatcher"]})
    n = notify(ctx.db, ctx.clock, recipient_ref=ref, recipient_type=str(a.get("recipient_type", "customer")), type=str(a.get("type", "agent_message")),
               message=f"[simulated] {a.get('message', '')}", order_id=a.get("order_id") or ctx.order_id, dedupe_key=a.get("idempotency_key"))
    return ToolResult("ok", {"notification_id": n.id if n else None, "deduplicated": n is None})


# ------------------------------------------------------------------ registry
def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


ALL_ROLES = ("customer", "scheduling", "recovery", "break", "dispatcher")
TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("search_repair_catalog", "Search the repair catalog (trade, problem, complexity, fixed duration).", _schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]), False, ALL_ROLES, t_search_repair_catalog),
    ToolSpec("search_address", "Geocode a typed address / postal code / landmark into candidates.", _schema({"query": {"type": "string"}}, ["query"]), False, ALL_ROLES, t_search_address),
    ToolSpec("get_customer_history", "Authorised history: orders, actual problems, feedback, negative technicians.", _schema({"customer_id": {"type": "string"}}), False, ALL_ROLES, t_get_customer_history),
    ToolSpec("get_order_context", "Current order facts: status, priority, window, risks, assignment, recent plans, authority.", _schema({"order_id": {"type": "string"}}), False, ALL_ROLES, t_get_order_context),
    ToolSpec("query_technicians", "Technicians with skills, status, next free time/location.", _schema({"trade_type": {"type": "string"}, "min_level": {"type": "integer"}}), False, ("scheduling", "recovery", "break", "dispatcher"), t_query_technicians),
    ToolSpec("get_travel_times", "Static travel-time estimates between locations.", _schema({"from_location_id": {"type": "string"}, "to_location_ids": {"type": "array", "items": {"type": "string"}}}, ["from_location_id", "to_location_ids"]), False, ("scheduling", "recovery", "break", "dispatcher"), t_get_travel_times),
    ToolSpec("simulate_insertion", "Zero-disturbance insertion trial for an order (read-only; candidates are stored as proposals).", _schema({"order_id": {"type": "string"}}), False, ("scheduling", "recovery", "dispatcher"), t_simulate_insertion, is_search=True),
    ToolSpec("search_local_repair", "Bounded repair search within the order's authority (P1: any number of undeparted P3, P0: ≤5 P2/P3).", _schema({"order_id": {"type": "string"}}), False, ("scheduling", "recovery", "dispatcher"), t_search_local_repair, is_search=True),
    ToolSpec("validate_plan", "Re-validate a stored plan against current facts; returns the policy decision.", _schema({"plan_id": {"type": "string"}}, ["plan_id"]), False, ("scheduling", "recovery", "dispatcher"), t_validate_plan),
    ToolSpec("propose_alternative_windows", "Feasible appointment windows (read-only trials, not reservations).", _schema({"order_id": {"type": "string"}, "catalog_item_id": {"type": "string"}, "location_id": {"type": "string"}, "excluded_technicians": {"type": "array", "items": {"type": "string"}}, "exclude_windows": {"type": "array"}, "after": {"type": "string"}, "count": {"type": "integer"}, "paid": {"type": "boolean"}, "allow_moves": {"type": "boolean"}}), False, ALL_ROLES, t_propose_alternative_windows, is_search=True),
    ToolSpec("evaluate_break_need", "Work-since-last-rest facts and the rest level (none/pre_evaluate/evaluate/escalate).", _schema({"technician_id": {"type": "string"}}), False, ("break", "recovery", "dispatcher"), t_evaluate_break_need),
    ToolSpec("simulate_break", "Zero-disturbance rest trial: one start (start,minutes) or a window search.", _schema({"technician_id": {"type": "string"}, "start": {"type": "string"}, "minutes": {"type": "integer"}, "window_start": {"type": "string"}, "window_end": {"type": "string"}}), False, ("break", "recovery", "dispatcher"), t_simulate_break),
    ToolSpec("create_customer_question", "Ask the customer a structured question; the task waits for the answer.", _schema({"kind": {"type": "string"}, "question": {"type": "string"}, "options": {"type": "array"}, "order_id": {"type": "string"}}, ["question"]), True, ALL_ROLES, t_create_customer_question),
    ToolSpec("flag_for_human", "Escalate to a human with evidence; the task waits.", _schema({"category": {"type": "string"}, "urgency": {"type": "string"}, "reason_summary": {"type": "string"}, "evidence_refs": {"type": "array"}, "attempted_actions": {"type": "array"}, "unresolved_questions": {"type": "array"}, "suggested_next_action": {"type": "string"}, "order_id": {"type": "string"}, "incident_id": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["category", "reason_summary"]), True, ALL_ROLES, t_flag_for_human),
    ToolSpec("create_safety_incident", "Record a safety incident / draft (never reports anything externally).", _schema({"danger_type": {"type": "string"}, "description": {"type": "string"}, "known_location": {"type": "object"}, "order_id": {"type": "string"}}, ["danger_type", "description"]), True, ALL_ROLES, t_create_safety_incident),
    ToolSpec("submit_plan", "Submit a stored plan by id; the PolicyEngine commits or opens an approval.", _schema({"plan_id": {"type": "string"}, "expected_version": {"type": "integer"}}, ["plan_id"]), True, ("scheduling", "recovery", "dispatcher"), t_submit_plan),
    ToolSpec("submit_break", "Commit a zero-disturbance rest block (re-validated on submit).", _schema({"technician_id": {"type": "string"}, "start": {"type": "string"}, "minutes": {"type": "integer"}, "expected_version": {"type": "integer"}, "idempotency_key": {"type": "string"}, "reason": {"type": "string"}}, ["start"]), True, ("break", "recovery", "dispatcher"), t_submit_break),
    ToolSpec("delegate_task", "Hand a sub-problem to another agent role and wait for its outcome (depth \u2264 2, never your own role).", _schema({"role": {"type": "string"}, "goal": {"type": "string"}, "order_id": {"type": "string"}, "technician_id": {"type": "string"}, "reason": {"type": "string"}, "context": {"type": "object"}}, ["role", "goal"]), True, ("scheduling", "recovery", "dispatcher"), t_delegate_task),
    ToolSpec("notify_in_app", "Create an in-app notification (idempotent with idempotency_key). recipient_ref is a customer session id, a customer id, or 'dispatcher' for the operator inbox; recipient_type is customer | technician | dispatcher. A back-office role that omits recipient_ref addresses the dispatcher.", _schema({"recipient_ref": {"type": "string"}, "recipient_type": {"type": "string"}, "type": {"type": "string"}, "message": {"type": "string"}, "order_id": {"type": "string"}, "idempotency_key": {"type": "string"}}, ["message"]), True, ALL_ROLES, t_notify_in_app),
]}


def check_args(spec: ToolSpec, args: dict[str, Any]) -> list[str]:
    """Light schema validation (required keys + primitive types) — a model cannot widen authority through args."""
    errors: list[str] = []
    props = spec.schema.get("properties", {})
    for k in spec.schema.get("required", []):
        if k not in args or args[k] in (None, ""):
            errors.append(f"missing:{k}")
    for k, v in args.items():
        if k not in props:
            errors.append(f"unknown:{k}")
            continue
        t = props[k].get("type")
        if t == "string" and not isinstance(v, str):
            errors.append(f"type:{k}")
        if t == "integer" and not isinstance(v, int):
            errors.append(f"type:{k}")
        if t == "boolean" and not isinstance(v, bool):
            errors.append(f"type:{k}")
        if t == "array" and not isinstance(v, list):
            errors.append(f"type:{k}")
        if t == "object" and not isinstance(v, dict):
            errors.append(f"type:{k}")
    return errors


def invoke(ctx: ToolContext, name: str, args: dict[str, Any]) -> tuple[ToolResult, int]:
    """Run a tool with role/arg checks. Returns (result, duration_ms)."""
    spec = TOOLS.get(name)
    t0 = time.monotonic()
    if spec is None:
        return ToolResult("error", reason_codes=["UNKNOWN_TOOL"], data={"tool": name}), 0
    if ctx.role not in spec.roles:
        return ToolResult("forbidden", reason_codes=["ROLE_NOT_ALLOWED"], data={"tool": name, "role": ctx.role}), 0
    if ctx.allowed_tools is not None and name not in ctx.allowed_tools:
        # the role could, the skill says it may not — e.g. recovery may not book rest, it must delegate it
        return ToolResult("forbidden", reason_codes=["TOOL_NOT_IN_SKILL"],
                          data={"tool": name, "role": ctx.role, "skill_tools": sorted(ctx.allowed_tools)}), 0
    errs = check_args(spec, args)
    if errs:
        return ToolResult("data_incomplete", reason_codes=["INVALID_ARGS"], data={"errors": errs}), 0
    try:
        res = spec.handler(ctx, args)
    except OrderError as exc:
        res = ToolResult("error", reason_codes=[exc.kind], data={"message": exc.message, "code": exc.code})
    except Exception as exc:  # noqa: BLE001
        res = ToolResult("error", reason_codes=["TOOL_UNAVAILABLE"], data={"message": str(exc)[:200]})
    return res, int((time.monotonic() - t0) * 1000)
