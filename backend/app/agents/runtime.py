"""Agent task runtime (V3 §11–12): a bounded tool loop driven by a decision policy (model or labelled mock).

The policy only picks the next tool + args (or finishes); tools validate authority and write through protected
services. Tasks persist their state and pause on customer/human waits; events wake them and they re-read facts."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.skills import allowed_tools, get_skill
from app.agents.tools import TOOLS, ToolContext, ToolResult, invoke
from app.config import get_policy, get_settings
from app.db.base import utc_now
from app.models.entities import AgentTask, CustomerQuestion, ToolTrace, WorkOrder
from app.services.clock import Clock, get_clock
from app.services.ids import new_id
from app.services.timeutil import iso

log = logging.getLogger(__name__)
TERMINAL = ("succeeded", "waiting_customer", "waiting_human", "waiting_agent", "stale", "no_solution", "failed")
LIVE = ("pending", "running", "waiting_customer", "waiting_human", "waiting_agent")


@dataclass
class Action:
    kind: str                     # call_tool | finish
    tool: str | None = None
    args: dict[str, Any] | None = None
    status: str | None = None     # for finish
    summary: str = ""
    decided_by: str = "mock"


# ------------------------------------------------------------------ task creation / lookup
def create_task(db: Session, clock: Clock, *, role: str, goal: str, order_id: str | None = None, technician_id: str | None = None,
                customer_id: str | None = None, session_id: str | None = None, incident_id: str | None = None,
                facts: dict[str, Any] | None = None, dedupe_key: str | None = None) -> AgentTask | None:
    """Returns None when an equivalent live task already exists (dedupe of recovery work)."""
    if dedupe_key:
        live = db.scalars(select(AgentTask).where(AgentTask.dedupe_key == dedupe_key,
                                                  AgentTask.status.in_(LIVE))).first()
        if live is not None:
            return None
    if order_id and role in ("scheduling", "recovery"):
        # a task already waiting on this order's customer or on a human owns the case: no second investigation per version bump
        waiting = db.scalars(select(AgentTask).where(AgentTask.order_id == order_id, AgentTask.role.in_(["scheduling", "recovery"]),
                                                     AgentTask.status.in_(LIVE))).first()
        if waiting is not None:
            return None
    t = AgentTask(id=new_id("task"), scenario_generation=clock.scenario_generation, role=role, goal=goal, order_id=order_id,
                  technician_id=technician_id, customer_id=customer_id, session_id=session_id, incident_id=incident_id, status="pending",
                  facts_summary=facts or {}, execution_mode="real" if get_settings().llm_mode == "real" else "mock", dedupe_key=dedupe_key)
    db.add(t)
    db.flush()
    return t


def wake_task_for_question(db: Session, q: CustomerQuestion) -> None:
    if not q.task_id:
        return
    t = db.get(AgentTask, q.task_id)
    if t is not None and t.status == "waiting_customer":
        t.status = "pending"
        t.wakeup_reason = f"question_answered:{q.id}"
        t.pending_question_id = None
        t.facts_summary = {**(t.facts_summary or {}), "customer_answer": q.answer}
        db.flush()


def _trace(db: Session, task: AgentTask, seq: int, phase: str, tool: str, *, args: dict[str, Any] | None = None, result: ToolResult | None = None,
           duration_ms: int | None = None, decided_by: str = "mock", clock: Clock | None = None, thought: str | None = None,
           status: str | None = None) -> None:
    data: dict[str, Any] = {}
    if result is not None:
        raw = json.dumps(result.data, default=str)
        data = result.data if len(raw) < 3000 else {"truncated": raw[:3000]}
    db.add(ToolTrace(id=new_id("tt"), task_id=task.id, seq=seq, phase=phase, tool=tool, args_summary=_redact(args or {}),
                     thought=(thought or None), result_status=result.status if result else status,
                     reason_codes=result.reason_codes if result else [],
                     data_summary=data, evidence_refs=result.evidence_refs if result else [], duration_ms=duration_ms, decided_by=decided_by,
                     sim_time=clock.now if clock else None))
    db.flush()


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in args.items():
        s = json.dumps(v, default=str)
        out[k] = v if len(s) <= 300 else s[:300] + "…"
    return out


# ------------------------------------------------------------------ run loop
@dataclass
class TaskStub:
    """Plain copy of the task fields a policy reads — safe to use outside a session / the state lock."""
    id: str
    role: str
    goal: str
    order_id: str | None
    technician_id: str | None
    customer_id: str | None
    session_id: str | None
    facts_summary: dict[str, Any]
    tool_budget_used: int
    search_budget_used: int
    delegation_depth: int = 0


def _stub(t: AgentTask) -> TaskStub:
    return TaskStub(t.id, t.role, t.goal, t.order_id, t.technician_id, t.customer_id, t.session_id, dict(t.facts_summary or {}),
                    t.tool_budget_used, t.search_budget_used, t.delegation_depth)


@dataclass
class _Loop:
    """Per-task running state kept between steps (in memory; the durable parts are on the AgentTask / ToolTrace rows)."""
    policy: Any
    max_calls: int
    history: list[dict[str, Any]]
    seq: int
    max_searches: int = 3
    decided_by: str = "mock"
    transient: int = 0
    stale_retries: int = 0
    degraded: str | None = None


def _begin(db: Session, task: AgentTask, max_calls: int | None) -> _Loop | None:
    """Mark the task running and choose the policy. Returns None when the task belongs to an old scenario."""
    from app.agents.policies import MockPolicy, ModelPolicy, PolicyUnavailable
    clock = get_clock(db)
    if task.scenario_generation != clock.scenario_generation:
        task.status = "stale"
        task.outcome = {"reason": "scenario reset"}
        db.flush()
        return None
    budget = get_policy().agent
    task.status = "running"
    task.tool_budget_used = 0
    task.search_budget_used = 0
    db.flush()
    policy: Any
    degraded = None
    if task.execution_mode == "real":
        try:
            policy = ModelPolicy()
        except PolicyUnavailable as exc:
            policy = MockPolicy()
            degraded = f"model policy unavailable ({exc}); rule policy used"
    else:
        policy = MockPolicy()
    seq = len(db.scalars(select(ToolTrace).where(ToolTrace.task_id == task.id)).all())
    # the skill file may set a tighter budget than policy.yaml; a delegated child inherits a cap from its parent so
    # one disruption cannot cost more tool calls than a single top-level task would have
    skill = get_skill(task.role)
    limit = skill.max_tool_calls if skill and skill.max_tool_calls else budget.max_tool_calls_per_wakeup
    searches = skill.max_searches if skill and skill.max_searches else budget.max_plan_searches_per_wakeup
    cap = (task.facts_summary or {}).get("budget_cap")
    if isinstance(cap, int) and cap > 0:
        limit = min(limit, cap)
    return _Loop(policy=policy, max_calls=max_calls or limit, history=[], seq=seq, max_searches=searches, degraded=degraded)


def _decide(loop: _Loop, stub: TaskStub, role: str, session_id: str | None) -> Action:
    """The policy decision — the only slow part for a real model. Never holds the state lock."""
    from app.agents.policies import MockPolicy, PolicyUnavailable
    ctx = ToolContext(db=None, clock=None, role=role, task_id=stub.id, customer_id=stub.customer_id, session_id=session_id,  # type: ignore[arg-type]
                      order_id=stub.order_id, technician_id=stub.technician_id, allowed_tools=allowed_tools(role),
                      delegation_depth=stub.delegation_depth)
    try:
        return loop.policy.decide(stub, loop.history, ctx)
    except PolicyUnavailable as exc:
        loop.policy = MockPolicy()
        loop.degraded = f"model policy failed mid-task ({exc}); rule policy used"
        return loop.policy.decide(stub, loop.history, ctx)


def _step(db: Session, task: AgentTask, loop: _Loop, action: Action) -> bool:
    """Apply one decided action under the state lock (tool call + traces + state). Returns True when the task is finished."""
    clock = get_clock(db)
    loop.decided_by = action.decided_by
    ctx = ToolContext(db=db, clock=clock, role=task.role, task_id=task.id, customer_id=task.customer_id, session_id=task.session_id,
                      order_id=task.order_id, technician_id=task.technician_id, allowed_tools=allowed_tools(task.role),
                      delegation_depth=task.delegation_depth)
    if task.tool_budget_used >= loop.max_calls:
        return _finish(db, task, loop, "budget_exhausted", f"tool budget ({loop.max_calls}) exhausted")
    if action.kind == "finish":
        return _finish(db, task, loop, action.status or "succeeded", action.summary)
    tool = action.tool or ""
    args = action.args or {}
    spec = TOOLS.get(tool)
    if spec and spec.is_search and task.search_budget_used >= loop.max_searches:
        loop.history.append({"tool": tool, "args": args, "result": {"status": "budget_exhausted", "reason_codes": ["SEARCH_BUDGET_EXHAUSTED"], "data": {}}})
        loop.seq += 1
        _trace(db, task, loop.seq, "error", tool, args=args, result=ToolResult("budget_exhausted", reason_codes=["SEARCH_BUDGET_EXHAUSTED"]),
               decided_by=action.decided_by, clock=clock, thought=action.summary)
        task.tool_budget_used += 1
        db.flush()
        return False
    loop.seq += 1
    _trace(db, task, loop.seq, "planned", tool, args=args, decided_by=action.decided_by, clock=clock, thought=action.summary)
    res, ms = invoke(ctx, tool, args)
    task.tool_budget_used += 1
    if spec and spec.is_search:
        task.search_budget_used += 1
    phase = "error" if res.status in ("error", "forbidden", "stale", "data_incomplete") else \
        "submitted" if tool.startswith("submit_") and res.status == "ok" else \
        "validated" if tool == "validate_plan" and res.status == "ok" else "returned"
    loop.seq += 1
    _trace(db, task, loop.seq, phase, tool, args=args, result=res, duration_ms=ms, decided_by=action.decided_by, clock=clock)
    task.evidence_refs = list(dict.fromkeys(list(task.evidence_refs or []) + res.evidence_refs))
    if tool in ("simulate_insertion", "search_local_repair") and res.status == "ok":
        task.tried_plan_ids = list(task.tried_plan_ids or []) + [c["plan_id"] for c in res.data.get("candidates", [])]
    loop.history.append({"tool": tool, "args": args, "result": res.as_dict()})
    if res.wait == "customer":
        task.status = "waiting_customer"
        task.pending_question_id = res.data.get("question_id")
        task.outcome = {"summary": "waiting for the customer's answer", "question_id": task.pending_question_id, "degraded": loop.degraded}
        db.flush()
        return True
    if res.wait == "human":
        task.status = "waiting_human"
        task.human_case_id = res.data.get("human_case_id")
        task.outcome = {"summary": "escalated to a human", "human_case_id": task.human_case_id, "degraded": loop.degraded}
        db.flush()
        return True
    if res.wait == "agent":
        child_id = res.data.get("child_task_id")
        task.status = "waiting_agent"
        task.waiting_child_id = child_id
        task.outcome = {"summary": f"delegated to a {res.data.get('role')} agent", "child_task_id": child_id, "degraded": loop.degraded}
        db.flush()
        _run_child(db, child_id)
        return True
    if res.status == "stale":
        loop.stale_retries += 1
        if loop.stale_retries > 1:
            return _finish(db, task, loop, "stale", "facts changed twice during the task; re-plan on the next wake-up")
    if res.status == "error" and "TOOL_UNAVAILABLE" in res.reason_codes:
        loop.transient += 1
        if loop.transient > get_policy().agent.transient_retries:
            return _finish(db, task, loop, "failed", "tool unavailable after bounded retries")
    db.flush()
    return False


def _finish(db: Session, task: AgentTask, loop: _Loop, final_status: str, summary: str) -> bool:
    """Make the ending handleable: budget exhaustion / no solution always become an evidenced human case."""
    clock = get_clock(db)
    ctx = ToolContext(db=db, clock=clock, role=task.role, task_id=task.id, customer_id=task.customer_id, session_id=task.session_id,
                      order_id=task.order_id, technician_id=task.technician_id, delegation_depth=task.delegation_depth)
    attempted = [h["tool"] for h in loop.history]
    if final_status == "budget_exhausted":
        args: dict[str, Any] = {"category": "agent_budget_exhausted", "urgency": "normal",
                "reason_summary": f"{task.role} task {task.id} used its tool budget without a result: {task.goal[:120]}",
                "evidence_refs": list(task.evidence_refs or []), "attempted_actions": attempted,
                "suggested_next_action": "review the tool trace and decide manually",
                "idempotency_key": f"budget:{task.id}"}
        res, ms = invoke(ctx, "flag_for_human", args)
        loop.seq += 1
        _trace(db, task, loop.seq, "returned", "flag_for_human", args=args, result=res, duration_ms=ms,
               decided_by=loop.decided_by, clock=clock, thought=summary)
        task.status = "waiting_human"
        task.human_case_id = res.data.get("human_case_id")
        task.outcome = {"summary": summary, "human_case_id": task.human_case_id, "degraded": loop.degraded}
    elif final_status == "no_solution" and not task.human_case_id:
        args = {"category": "no_solution", "urgency": "high" if _is_urgent(db, task) else "normal",
                "reason_summary": summary or f"no admissible plan found for {task.order_id}",
                "evidence_refs": list(task.evidence_refs or []), "attempted_actions": attempted,
                "suggested_next_action": "coordinate with the customer (reschedule / other day) or add resources",
                "idempotency_key": f"nosol:{task.id}"}
        res, ms = invoke(ctx, "flag_for_human", args)
        loop.seq += 1
        _trace(db, task, loop.seq, "returned", "flag_for_human", args=args, result=res, duration_ms=ms,
               decided_by=loop.decided_by, clock=clock, thought=summary)
        task.status = "waiting_human"
        task.human_case_id = res.data.get("human_case_id")
        task.outcome = {"summary": summary, "human_case_id": task.human_case_id, "degraded": loop.degraded}
    else:
        task.status = final_status if final_status in TERMINAL else "failed"
        task.outcome = {"summary": summary, "degraded": loop.degraded, "tool_calls": task.tool_budget_used, "searches": task.search_budget_used}
    # the ending is a step too, and it is the last one: a trace that stops at the final tool call never says how it ended
    loop.seq += 1
    _trace(db, task, loop.seq, "decision", "finish", clock=clock, thought=summary, status=task.status, decided_by=loop.decided_by)
    db.flush()
    _wake_parent(db, task)
    return True


def _run_child(db: Session, child_id: str | None) -> None:
    """Start the delegated task. Mock children run inline so a demo shows the whole hand-off in one request; real
    children are left pending for the worker thread, which keeps model latency off the state lock."""
    if not child_id:
        return
    child = db.get(AgentTask, child_id)
    if child is None or child.status != "pending":
        return
    if child.execution_mode == "real":
        _pending_signal.set()
        return
    run_task(db, child)


def _wake_parent(db: Session, child: AgentTask) -> None:
    """A finished child hands its outcome back to whoever delegated it; the parent re-reads facts on its next run."""
    if not child.parent_task_id:
        return
    parent = db.get(AgentTask, child.parent_task_id)
    if parent is None or parent.status != "waiting_agent" or parent.waiting_child_id != child.id:
        return
    result = {"task_id": child.id, "role": child.role, "status": child.status, "order_id": child.order_id,
              "technician_id": child.technician_id, "summary": (child.outcome or {}).get("summary"),
              "human_case_id": child.human_case_id}
    facts = dict(parent.facts_summary or {})
    history = [r for r in (facts.get("child_results") or []) if r.get("task_id") != child.id]
    parent.status = "pending"
    parent.waiting_child_id = None
    parent.wakeup_reason = f"delegate_done:{child.id}"
    parent.facts_summary = {**facts, "delegate_result": result, "child_results": [*history, result]}
    db.flush()


def run_task(db: Session, task: AgentTask, *, max_calls: int | None = None) -> AgentTask:
    """Inline run (mock policy, tests, explicit /run): decide → step until finished, all in the caller's session/lock.
    Real-model tasks normally go through the worker (`worker_step`), which keeps model latency outside the state lock."""
    loop = _begin(db, task, max_calls)
    if loop is None:
        return task
    for _ in range(loop.max_calls + 2):
        action = _decide(loop, _stub(task), task.role, task.session_id)
        if _step(db, task, loop, action):
            break
    else:
        _finish(db, task, loop, "budget_exhausted", f"tool budget ({loop.max_calls}) exhausted")
    return task


def _is_urgent(db: Session, task: AgentTask) -> bool:
    if not task.order_id:
        return False
    o = db.get(WorkOrder, task.order_id)
    return bool(o and o.effective_priority in ("P0", "P1"))


# ------------------------------------------------------------------ pending tasks: inline (mock) or worker thread (real)
_worker_lock = threading.Lock()
_pending_signal = threading.Event()


def run_pending(db: Session, *, limit: int = 5, inline_only: bool = False) -> list[str]:
    """Run pending tasks for the current generation. Mock tasks run inline (fast, deterministic); real-model tasks are
    handed to the worker thread so the sim clock / API never blocks on model latency."""
    clock = get_clock(db)
    ran: list[str] = []
    # bounded outer loop: a delegated child finishing puts its parent back to `pending`, and the caller should not
    # have to wait for the next tick to see the hand-off complete
    for _round in range(3):
        rows = db.scalars(select(AgentTask).where(AgentTask.status == "pending", AgentTask.scenario_generation == clock.scenario_generation)
                          .order_by(AgentTask.created_at).limit(limit)).all()
        fresh = [t for t in rows if t.id not in ran]
        if not fresh:
            break
        for t in fresh:
            if t.execution_mode == "real" and not inline_only:
                _pending_signal.set()  # picked up by the worker
                ran.append(t.id)
                continue
            run_task(db, t)
            ran.append(t.id)
    return ran


def worker_step() -> int:
    """Run ONE pending real-mode task to its next pause. The model decision runs outside the state lock; each tool call
    is its own short locked transaction, so the sim clock, the dashboard and the apps never wait on model latency."""
    from app.db.session import session_scope
    from app.services.execution_service import state_lock
    if not _worker_lock.acquire(blocking=False):
        return 0
    try:
        with state_lock, session_scope() as db:
            clock = get_clock(db)
            t = db.scalars(select(AgentTask).where(AgentTask.status == "pending", AgentTask.execution_mode == "real",
                                                   AgentTask.scenario_generation == clock.scenario_generation).order_by(AgentTask.created_at)).first()
            if t is None:
                _pending_signal.clear()
                return 0
            loop = _begin(db, t, None)
            if loop is None:
                return 1
            task_id, role, session_id, stub = t.id, t.role, t.session_id, _stub(t)
        for _ in range(loop.max_calls + 2):
            action = _decide(loop, stub, role, session_id)          # slow (model) — no lock held
            with state_lock, session_scope() as db:
                t = db.get(AgentTask, task_id)
                clock = get_clock(db)
                if t is None or t.scenario_generation != clock.scenario_generation or t.status != "running":
                    if t is not None and t.status == "running":
                        t.status, t.outcome = "stale", {"reason": "scenario reset during the task"}
                    return 1
                if _step(db, t, loop, action):
                    return 1
                stub = _stub(t)
        with state_lock, session_scope() as db:
            t = db.get(AgentTask, task_id)
            if t is not None and t.status == "running":
                _finish(db, t, loop, "budget_exhausted", f"tool budget ({loop.max_calls}) exhausted")
        return 1
    finally:
        _worker_lock.release()


_stop_worker = threading.Event()


def start_worker_thread() -> threading.Thread:
    """Dedicated daemon thread for model-driven tasks: wakes on the pending signal (or every 5 s), independent of the
    clock/scan tick so a slow model never delays the simulation."""
    def loop() -> None:
        while not _stop_worker.is_set():
            _pending_signal.wait(timeout=5.0)
            if _stop_worker.is_set():
                break
            try:
                while worker_step() and not _stop_worker.is_set():
                    pass
            except Exception:
                log.exception("agent worker step failed")
                _pending_signal.clear()
    th = threading.Thread(target=loop, name="agent-worker", daemon=True)
    th.start()
    return th


def stop_worker_thread() -> None:
    _stop_worker.set()
    _pending_signal.set()


def _skill_view(role: str) -> dict[str, Any] | None:
    sk = get_skill(role)
    return None if sk is None else {"name": sk.name, "title": sk.title, "source": sk.source, "tools": list(sk.tools)}


def task_view(db: Session, t: AgentTask, with_traces: bool = True) -> dict[str, Any]:
    out = {"id": t.id, "role": t.role, "goal": t.goal, "status": t.status, "order_id": t.order_id, "technician_id": t.technician_id,
           "customer_id": t.customer_id, "session_id": t.session_id, "incident_id": t.incident_id, "execution_mode": t.execution_mode,
           "tool_budget_used": t.tool_budget_used, "search_budget_used": t.search_budget_used, "wakeup_reason": t.wakeup_reason,
           "human_case_id": t.human_case_id, "pending_question_id": t.pending_question_id, "tried_plan_ids": t.tried_plan_ids,
           "evidence_refs": t.evidence_refs, "outcome": t.outcome, "facts_summary": t.facts_summary,
           "parent_task_id": t.parent_task_id, "child_task_ids": list(t.child_task_ids or []), "delegation_depth": t.delegation_depth,
           "waiting_child_id": t.waiting_child_id, "skill": _skill_view(t.role),
           "created_at": iso(t.created_at), "updated_at": iso(t.updated_at)}
    if with_traces:
        out["traces"] = [{"seq": x.seq, "phase": x.phase, "tool": x.tool, "thought": x.thought, "args": x.args_summary, "result_status": x.result_status,
                          "reason_codes": x.reason_codes, "data": x.data_summary, "evidence_refs": x.evidence_refs, "duration_ms": x.duration_ms,
                          "decided_by": x.decided_by, "sim_time": iso(x.sim_time), "at": iso(x.created_at)}
                         for x in db.scalars(select(ToolTrace).where(ToolTrace.task_id == t.id).order_by(ToolTrace.seq)).all()]
    return out


def utc() -> Any:
    return utc_now()
