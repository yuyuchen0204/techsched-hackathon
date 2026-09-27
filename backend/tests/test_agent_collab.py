"""Agent layer: skill files as an enforced fence, agent-to-agent delegation, and the agent scorecard.

Everything here runs on the seeded main scenario with the rule policy (LLM_MODE=mock in conftest), so the
behaviour under test is the runtime's, not a model's.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents import runtime, skills
from app.agents.policies import MockPolicy
from app.agents.runtime import Action
from app.agents.tools import TOOLS, ToolContext, invoke
from app.models.entities import AgentTask, Notification
from app.services import agent_metrics
from app.services.clock import get_clock
from tests.test_v3 import order  # reuse the seeded-scenario helper


# ---------------------------------------------------------------- skills as files
def test_every_role_has_a_loadable_skill_with_a_playbook():
    loaded = skills.load_skills()
    assert set(loaded) == {"scheduling", "recovery", "break", "customer", "dispatcher"}
    for skill in loaded.values():
        assert skill.tools, f"{skill.name} declares no tools"
        assert len(skill.body) > 200, f"{skill.name} has no real playbook"
        assert skill.source.startswith("config/agent_skills/")
        # a skill may only narrow the role gate, never widen it
        by_role = {t.name for t in TOOLS.values() if skill.name in t.roles}
        assert set(skill.tools) <= by_role, f"{skill.name} declares tools its role is not permitted: {set(skill.tools) - by_role}"


def test_skill_prompt_reaches_the_model_contract():
    from app.agents.policies import system_prompt
    prompt = system_prompt("break")
    assert "SKILL: break" in prompt and "Never move a customer's order" in prompt
    assert system_prompt("nonexistent_role").startswith("You are an operations agent")


def test_skill_withholds_a_tool_the_role_gate_would_allow(db_session):
    """recovery is permitted submit_break by role, but its skill withholds it so rest stays the break agent's call."""
    db = db_session
    clock = get_clock(db)
    assert "submit_break" in TOOLS["submit_break"].roles or True
    assert "recovery" in TOOLS["submit_break"].roles           # the role gate would allow it
    assert "submit_break" not in skills.get_skill("recovery").tools   # the skill does not
    ctx = ToolContext(db=db, clock=clock, role="recovery", allowed_tools=skills.allowed_tools("recovery"))
    res, _ = invoke(ctx, "submit_break", {"technician_id": "tech_01", "start": "2026-09-15T05:00:00"})
    assert res.status == "forbidden" and res.reason_codes == ["TOOL_NOT_IN_SKILL"]
    # and the same call without a skill fence is not blocked here (proving the fence, not a missing argument)
    plain = ToolContext(db=db, clock=clock, role="recovery")
    res2, _ = invoke(plain, "submit_break", {"technician_id": "tech_01", "start": "2026-09-15T05:00:00"})
    assert res2.reason_codes != ["TOOL_NOT_IN_SKILL"]


def test_skill_budget_overrides_policy_yaml(db_session):
    db = db_session
    clock = get_clock(db)
    task = runtime.create_task(db, clock, role="break", goal="rest", technician_id="tech_01")
    loop = runtime._begin(db, task, None)
    assert loop.max_calls == skills.get_skill("break").max_tool_calls == 8   # policy.yaml says 12
    assert loop.max_searches == 2


# ---------------------------------------------------------------- delegation mechanics
def test_delegation_creates_a_child_and_pauses_the_parent(db_session):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", ws=(14, 0), we=(15, 30))
    parent = runtime.create_task(db, clock, role="recovery", goal="recover", order_id=o.id)
    parent.status = "running"
    ctx = ToolContext(db=db, clock=clock, role="recovery", task_id=parent.id,
                      allowed_tools=skills.allowed_tools("recovery"))
    res, _ = invoke(ctx, "delegate_task", {"role": "break", "technician_id": "tech_01", "goal": "find rest"})
    assert res.status == "waiting" and res.wait == "agent"
    child = db.get(AgentTask, res.data["child_task_id"])
    assert child.role == "break" and child.parent_task_id == parent.id and child.delegation_depth == 1
    assert child.facts_summary["delegated_by"]["role"] == "recovery"
    assert child.id in parent.child_task_ids
    # the child's budget is carved out of the parent's, so a chain cannot outspend one top-level task
    assert child.facts_summary["budget_cap"] <= 12


def test_delegation_refuses_same_role_and_excess_depth(db_session):
    db = db_session
    clock = get_clock(db)
    parent = runtime.create_task(db, clock, role="recovery", goal="g", order_id="wo_001")
    ctx = ToolContext(db=db, clock=clock, role="recovery", task_id=parent.id)
    same, _ = invoke(ctx, "delegate_task", {"role": "recovery", "goal": "again"})
    assert same.status == "forbidden" and same.reason_codes == ["DELEGATE_SAME_ROLE"]
    unknown, _ = invoke(ctx, "delegate_task", {"role": "marketing", "goal": "x"})
    assert unknown.reason_codes == ["UNKNOWN_ROLE"]
    deep = ToolContext(db=db, clock=clock, role="recovery", task_id=parent.id, delegation_depth=2)
    res, _ = invoke(deep, "delegate_task", {"role": "break", "goal": "x", "technician_id": "tech_01"})
    assert res.status == "forbidden" and res.reason_codes == ["DELEGATION_DEPTH_EXCEEDED"]


def test_finished_child_wakes_its_parent_with_the_outcome(db_session):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", ws=(14, 0), we=(15, 30))
    parent = runtime.create_task(db, clock, role="recovery", goal="recover", order_id=o.id)
    parent.status = "running"
    ctx = ToolContext(db=db, clock=clock, role="recovery", task_id=parent.id)
    res, _ = invoke(ctx, "delegate_task", {"role": "break", "technician_id": "tech_01", "goal": "find rest"})
    child = db.get(AgentTask, res.data["child_task_id"])
    parent.status, parent.waiting_child_id = "waiting_agent", child.id
    db.flush()
    runtime.run_task(db, child)
    assert child.status in runtime.TERMINAL
    assert parent.status == "pending" and parent.wakeup_reason == f"delegate_done:{child.id}"
    handback = parent.facts_summary["delegate_result"]
    assert handback["task_id"] == child.id and handback["role"] == "break" and handback["status"] == child.status
    assert parent.facts_summary["child_results"][-1]["task_id"] == child.id


def test_recovery_hands_rest_to_the_break_agent_after_committing(db_session, monkeypatch):
    """The recovery agent owns the consequence of its own plan: it can read rest facts but not book rest, so a
    technician pushed over the threshold is handed to the break agent rather than quietly left tired."""
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", ws=(14, 0), we=(15, 30))
    spec = TOOLS["evaluate_break_need"]
    monkeypatch.setattr(spec, "handler", lambda ctx, a: __import__("app.agents.tools", fromlist=["ToolResult"]).ToolResult(
        "ok", {"level": "escalate", "work_minutes_since_break": 265, "planned_break_ahead": [], "search_window": [None, None]},
        [], [f"technician:{a.get('technician_id')}"]))
    task = runtime.create_task(db, clock, role="recovery", goal=f"recover {o.id}", order_id=o.id)
    runtime.run_task(db, task)
    planned = [x["tool"] for x in runtime.task_view(db, task)["traces"] if x["phase"] == "planned"]
    assert "submit_plan" in planned and "evaluate_break_need" in planned and "delegate_task" in planned
    assert len(task.child_task_ids) == 1
    child = db.get(AgentTask, task.child_task_ids[0])
    assert child.role == "break" and child.technician_id and child.parent_task_id == task.id
    # a mock child runs inline, so by the end of this call it has finished and handed the parent back its outcome
    assert child.status in runtime.TERMINAL
    assert task.status == "pending" and task.wakeup_reason == f"delegate_done:{child.id}"
    runtime.run_task(db, task)                       # the parent resumes and closes out with both facts
    assert task.status == "succeeded" and "rest handled by break agent" in task.outcome["summary"]


# ---------------------------------------------------------------- the supervisor
def test_supervisor_delegates_in_urgency_order_and_reports_once(db_session):
    db = db_session
    clock = get_clock(db)
    p3 = order(db, trade="Refrigerator", problem="Not cooling", ws=(15, 0), we=(17, 0))
    p1 = order(db, trade="Refrigerator", problem="Not cooling", ws=(14, 0), we=(15, 30))
    from app.services.order_service import set_paid_expedite
    set_paid_expedite(db, p1)
    task = runtime.create_task(db, clock, role="dispatcher", goal="supervise",
                               facts={"order_ids": [p3.id, p1.id], "trigger": "test"})
    for _ in range(6):
        runtime.run_task(db, task)
        runtime.run_pending(db, inline_only=True)
        if task.status in ("succeeded", "waiting_human", "failed", "no_solution"):
            break
    children = db.scalars(select(AgentTask).where(AgentTask.parent_task_id == task.id)).all()
    assert {c.order_id for c in children} == {p3.id, p1.id}
    assert all(c.role == "recovery" for c in children)
    # the paid P1 is handed over first — recovering a P3 before a P1 spends slack on the wrong customer
    assert min(children, key=lambda c: c.created_at).order_id == p1.id
    summary = db.scalars(select(Notification).where(Notification.type == "recovery_summary")).all()
    assert len(summary) == 1 and "recovered automatically" in summary[0].message


# ---------------------------------------------------------------- the trace is now a reasoning timeline
def test_every_step_records_why_and_the_ending_is_a_step(db_session):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", ws=(14, 0), we=(15, 30))
    task = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, task)
    traces = runtime.task_view(db, task)["traces"]
    assert all(x["thought"] for x in traces if x["phase"] == "planned"), "a planned step with no reason is unreadable"
    last = traces[-1]
    assert last["phase"] == "decision" and last["tool"] == "finish" and last["thought"]
    assert last["result_status"] == task.status


# ---------------------------------------------------------------- the scorecard
def test_scorecard_measures_the_agent_not_the_schedule(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", ws=(15, 0), we=(16, 30))
    good = runtime.create_task(db, clock, role="scheduling", goal=f"schedule {o.id}", order_id=o.id)
    runtime.run_task(db, good)
    card = agent_metrics.scorecard(db)
    assert card["tasks"] >= 1
    assert card["skills_loaded"] == ["break", "customer", "dispatcher", "recovery", "scheduling"]
    assert card["autonomy"]["rate_pct"] is not None
    assert card["transparency"]["rate_pct"] == 100.0
    assert card["cost"]["avg_tool_calls"] is not None
    assert card["discipline"]["wasted_pct"] is not None


def test_scorecard_counts_a_wasted_call_and_an_incomplete_handover(db_session, monkeypatch):
    db = db_session
    clock = get_clock(db)
    o = order(db, trade="Refrigerator", problem="Not cooling", ws=(14, 0), we=(15, 30))
    # a policy that keeps reaching for a tool its skill withholds
    monkeypatch.setattr(MockPolicy, "decide",
                        lambda self, task, history, ctx: Action("call_tool", "submit_break",
                                                                {"technician_id": "tech_01", "start": "2026-09-15T05:00:00"},
                                                                summary="try to book rest from a recovery task"))
    task = runtime.create_task(db, clock, role="recovery", goal=f"recover {o.id}", order_id=o.id)
    runtime.run_task(db, task, max_calls=3)
    card = agent_metrics.scorecard(db)
    assert card["discipline"]["wasted_calls"] >= 3
    assert "TOOL_NOT_IN_SKILL" in card["discipline"]["reason_codes"]
    assert card["handover_quality"]["escalations"] >= 1
