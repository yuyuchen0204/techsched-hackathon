"""Decision policies for the agent runtime. ModelPolicy asks the configured LLM for the next action (JSON action
protocol, validated); MockPolicy is a rule-based stand-in that still reacts to tool results — clearly labelled."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.agents.runtime import Action
from app.agents.skills import get_skill
from app.agents.tools import TOOLS, ToolContext
from app.config import get_policy, get_settings
from app.models.entities import AgentTask
from app.providers.llm.base import LLMError


class PolicyUnavailable(RuntimeError):
    pass


class ActionSchema(BaseModel):
    action: str = Field(description="call_tool | finish")
    tool: str | None = None
    args: dict[str, Any] | None = None
    status: str | None = Field(default=None, description="finish only: succeeded | no_solution | waiting_customer | waiting_human | failed")
    summary: str = ""


# Behaviour that is true for every role lives here; everything role-specific lives in config/agent_skills/*.md
# (see agents/skills.py) so a playbook can be reviewed and changed without touching Python.
SYSTEM_RULES = (
    "Rules: use only listed tools with schema-valid args; read tool results carefully — status/reason_codes tell you why something failed; "
    "do not repeat a search with identical args; budget_exhausted/timeout means 'not found this round', not impossible; you cannot change "
    "windows, priorities, locks or authority limits; submitting is only via submit_plan/submit_break; a tool your skill withholds returns "
    "TOOL_NOT_IN_SKILL — that is a boundary, not a bug, so delegate or escalate instead of retrying; when you cannot make progress, "
    "flag_for_human with concrete evidence_refs instead of guessing."
)


SYSTEM = (
    "You are an operations agent inside a technician-scheduling system. You decide the NEXT action only, as one JSON object: "
    '{"action":"call_tool","tool":"<name>","args":{...},"summary":"why"} or {"action":"finish","status":"succeeded|no_solution|waiting_customer|waiting_human|failed","summary":"..."}. '
    + SYSTEM_RULES +
    " The `summary` is read by a human in the reasoning timeline: one short sentence saying why this step, not what the tool does. "
    "Reply with JSON only."
)


def system_prompt(role: str) -> str:
    """The base contract plus the role's own playbook, so a skill edit reaches the model without a code change."""
    skill = get_skill(role)
    if skill is None:
        return SYSTEM
    return f"{SYSTEM}\n\n--- SKILL: {skill.name} ({skill.source}) ---\n{skill.prompt_text()}"


class ModelPolicy:
    name = "model"

    def __init__(self) -> None:
        s = get_settings()
        if s.llm_mode != "real":
            raise PolicyUnavailable("LLM_MODE is not real")
        from app.providers.llm.factory import get_llm
        self.llm = get_llm()
        if getattr(self.llm.provider, "name", "mock") == "mock":
            raise PolicyUnavailable("mock LLM provider")

    def decide(self, task: AgentTask, history: list[dict[str, Any]], ctx: ToolContext) -> Action:
        tools = [{"name": t.name, "description": t.description, "schema": t.schema, "writes": t.writes}
                 for t in TOOLS.values() if ctx.role in t.roles and (ctx.allowed_tools is None or t.name in ctx.allowed_tools)]
        budget = get_policy().agent
        skill = get_skill(task.role)
        max_calls = (skill.max_tool_calls if skill and skill.max_tool_calls else budget.max_tool_calls_per_wakeup)
        max_searches = (skill.max_searches if skill and skill.max_searches else budget.max_plan_searches_per_wakeup)
        payload = {"role": task.role, "goal": task.goal, "skill": skill.name if skill else None, "facts": task.facts_summary,
                   "ids": {"order_id": task.order_id, "technician_id": task.technician_id, "customer_id": task.customer_id, "session_id": task.session_id},
                   "budget": {"tool_calls_left": max_calls - task.tool_budget_used,
                              "searches_left": max_searches - task.search_budget_used},
                   "tools": tools, "history": _compact_history(history)}
        provider = self.llm.provider
        structured = getattr(provider, "structured", None)
        if structured is None:
            raise PolicyUnavailable("provider has no structured() call")
        try:
            out = structured(system_prompt(task.role), json.dumps(payload, ensure_ascii=False, default=str), ActionSchema)
        except LLMError as exc:
            raise PolicyUnavailable(str(exc)) from exc
        act = out if isinstance(out, ActionSchema) else ActionSchema.model_validate(out)
        if act.action == "call_tool" and act.tool in TOOLS:
            return Action("call_tool", act.tool, act.args or {}, summary=act.summary, decided_by="model")
        if act.action == "finish":
            st = act.status if act.status in ("succeeded", "no_solution", "waiting_customer", "waiting_human", "failed") else "failed"
            return Action("finish", status=st, summary=act.summary, decided_by="model")
        raise PolicyUnavailable(f"model returned an invalid action: {act.model_dump()}")


def _compact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for h in history[-8:]:
        r = h.get("result", {})
        data = json.dumps(r.get("data", {}), default=str)
        out.append({"tool": h["tool"], "args": h.get("args"), "status": r.get("status"), "reason_codes": r.get("reason_codes"),
                    "data": data if len(data) < 1200 else data[:1200] + "…"})
    return out


class MockPolicy:
    """Deterministic but result-driven stand-in (labelled 'mock' in every trace)."""
    name = "mock"

    def decide(self, task: AgentTask, history: list[dict[str, Any]], ctx: ToolContext) -> Action:
        done = [h["tool"] for h in history]

        def res_of(tool: str) -> dict[str, Any] | None:
            for h in reversed(history):
                if h["tool"] == tool:
                    return h["result"]
            return None

        if task.role in ("scheduling", "recovery"):
            if "get_order_context" not in done:
                return Action("call_tool", "get_order_context", {}, summary="read current facts")
            octx = res_of("get_order_context") or {}
            if octx.get("status") != "ok":
                return Action("finish", status="failed", summary=f"order context unavailable: {octx.get('reason_codes')}")
            od = octx.get("data", {})
            if od.get("lifecycle_status") != "OPEN":
                return Action("finish", status="succeeded", summary=f"order is {od.get('lifecycle_status')}; nothing to schedule")
            if od.get("assignment") and not od.get("recovery_target"):
                # a wake-up after a delegation lands here: the plan this task committed is now the assignment, so
                # close out naming both halves of the work rather than losing the hand-off in a generic sentence
                deleg = task.facts_summary.get("delegate_result") if isinstance(task.facts_summary, dict) else None
                if deleg:
                    return Action("finish", status="succeeded",
                                  summary=f"{task.order_id} assigned to {od['assignment'].get('technician_id')}; "
                                          f"rest handled by {deleg.get('role')} agent {deleg.get('task_id')} ({deleg.get('status')})")
                return Action("finish", status="succeeded", summary="order already has a valid assignment")
            sim = res_of("simulate_insertion")
            if sim is None:
                return Action("call_tool", "simulate_insertion", {}, summary="try zero-disturbance insertion")
            best = _best_candidate(sim)
            if best and "submit_plan" not in done:
                return Action("call_tool", "submit_plan", {"plan_id": best["plan_id"], "expected_version": sim.get("snapshot_version")},
                              summary=f"submit {best['strategy']} (score {best['decision_score']:.1f}, {best['policy']})")
            sub = res_of("submit_plan")
            if sub and sub.get("status") == "ok":
                # Own the consequence of the decision: the technician who just absorbed this job may now be over the
                # rest threshold. The skill withholds simulate_break/submit_break from this role deliberately — the
                # rest decision belongs to the break agent, so hand it over instead of quietly doing it here.
                if task.role == "recovery":
                    tech = _assigned_technician(sub)
                    if tech and "evaluate_break_need" not in done:
                        return Action("call_tool", "evaluate_break_need", {"technician_id": tech},
                                      summary=f"plan committed; check whether {tech} is now overdue for rest")
                    ev = res_of("evaluate_break_need") or {}
                    level = (ev.get("data") or {}).get("level")
                    if tech and level in ("evaluate", "escalate") and not (ev.get("data") or {}).get("planned_break_ahead") \
                            and "delegate_task" not in done:
                        return Action("call_tool", "delegate_task",
                                      {"role": "break", "technician_id": tech,
                                       "goal": f"{tech} is at rest level {level} after absorbing {task.order_id}; find a zero-disturbance rest",
                                       "reason": "recovery may not book rest; the break agent owns that decision"},
                                      summary=f"{tech} is at rest level {level} — hand rest to the break agent")
                    deleg = task.facts_summary.get("delegate_result") if isinstance(task.facts_summary, dict) else None
                    if deleg:
                        return Action("finish", status="succeeded",
                                      summary=f"plan {sub['data'].get('submitted')}; rest handled by {deleg.get('role')} agent "
                                              f"{deleg.get('task_id')} ({deleg.get('status')})")
                return Action("finish", status="succeeded", summary=f"plan {sub['data'].get('submitted')}")
            if sub and sub.get("status") == "stale" and done.count("simulate_insertion") < 2:
                return Action("call_tool", "simulate_insertion", {}, summary="facts changed; re-run the trial")
            codes = set(sim.get("reason_codes", []))
            authority = od.get("authority", {})
            if "NO_QUALIFIED_TECHNICIAN" in codes:
                return Action("call_tool", "flag_for_human", {"category": "no_qualified_technician", "urgency": "high" if od.get("effective_priority") in ("P0", "P1") else "normal",
                                                             "reason_summary": f"No technician is qualified for {od.get('problem', {}).get('trade_type')} level {od.get('problem', {}).get('complexity_level')}",
                                                             "evidence_refs": [f"order:{task.order_id}"], "attempted_actions": done,
                                                             "suggested_next_action": "add a qualified technician or reschedule", "idempotency_key": f"noqual:{task.order_id}"},
                              summary="skills insufficient everywhere → human")
            if (authority.get("max_affected") is None or authority.get("max_affected", 0) > 0) and "search_local_repair" not in done:
                return Action("call_tool", "search_local_repair", {}, summary="zero-disturbance failed; use bounded repair within authority")
            rep = res_of("search_local_repair")
            if rep and rep.get("status") == "ok":
                best = _best_candidate(rep)
                if best and "submit_plan" not in done:
                    return Action("call_tool", "submit_plan", {"plan_id": best["plan_id"], "expected_version": rep.get("snapshot_version")},
                                  summary=f"submit repair plan {best['strategy']} ({best['policy']})")
            if ctx.session_id and "propose_alternative_windows" not in done:
                return Action("call_tool", "propose_alternative_windows", {"order_id": task.order_id, "count": 3}, summary="look for other feasible windows to offer")
            win = res_of("propose_alternative_windows")
            if win and win.get("status") == "ok" and ctx.session_id and "create_customer_question" not in done:
                opts = [{"label": f"{w['window_start'][11:16]}–{w['window_end'][11:16]} ({w['technician_name']})", "window_start": w["window_start"], "window_end": w["window_end"]}
                        for w in win["data"].get("windows", [])[:3]]
                return Action("call_tool", "create_customer_question", {"kind": "choose_window", "order_id": task.order_id,
                                                                        "question": "Your requested window is not feasible today. Would one of these work?", "options": opts},
                              summary="ask the customer to pick a feasible window")
            return Action("finish", status="no_solution", summary=f"no admissible plan this round ({', '.join(sorted(codes)) or 'no candidates'})")
        if task.role == "break":
            if "evaluate_break_need" not in done:
                return Action("call_tool", "evaluate_break_need", {}, summary="read work-since-rest facts")
            ev = res_of("evaluate_break_need") or {}
            data = ev.get("data", {})
            if data.get("level") in ("none",) or data.get("planned_break_ahead"):
                return Action("finish", status="succeeded", summary=f"no rest needed now (level {data.get('level')}, planned ahead: {len(data.get('planned_break_ahead', []))})")
            if "simulate_break" not in done:
                w = data.get("search_window") or [None, None]
                return Action("call_tool", "simulate_break", {"window_start": w[0], "window_end": w[1]} if w[0] else {}, summary="search zero-disturbance rest slots")
            sim = res_of("simulate_break") or {}
            slots = (sim.get("data") or {}).get("slots") or []
            if slots and "submit_break" not in done:
                return Action("call_tool", "submit_break", {"start": slots[0]["start"], "minutes": slots[0]["minutes"], "expected_version": sim.get("snapshot_version"),
                                                           "idempotency_key": f"break:{task.technician_id}:{slots[0]['start']}"}, summary="commit the earliest feasible rest")
            sub = res_of("submit_break")
            if sub and sub.get("status") == "ok":
                return Action("finish", status="succeeded", summary="rest scheduled")
            if sub and sub.get("status") == "stale" and done.count("simulate_break") < 2:
                return Action("call_tool", "simulate_break", {}, summary="schedule changed; search again")
            return Action("call_tool", "flag_for_human", {"category": "rest_conflict", "urgency": "high" if data.get("level") == "escalate" else "normal",
                                                         "reason_summary": f"{task.technician_id}: {data.get('work_minutes_since_break')} min since last rest; no zero-disturbance slot ({(sim.get('data') or {}).get('rejections')})",
                                                         "evidence_refs": [f"technician:{task.technician_id}"], "attempted_actions": done,
                                                         "suggested_next_action": "consider moving a P3 or shortening the day", "idempotency_key": f"rest:{task.technician_id}:{data.get('last_break_end')}"},
                          summary="no slot → human")
        if task.role == "dispatcher":
            return self._supervise(task, history, done)
        return Action("finish", status="failed", summary=f"no mock policy for role {task.role}")

    def _supervise(self, task: AgentTask, history: list[dict[str, Any]], done: list[str]) -> Action:
        """Supervisor over a multi-order disruption: size it, delegate each order in urgency order, report once.

        It never plans an order itself — one owner per order — so its whole contribution is the ordering and the
        consolidated result a human would otherwise have to reassemble from scattered notifications."""
        facts = task.facts_summary if isinstance(task.facts_summary, dict) else {}
        targets: list[str] = [str(x) for x in (facts.get("order_ids") or [])]
        results: list[dict[str, Any]] = list(facts.get("child_results") or [])
        if not targets:
            return Action("finish", status="succeeded", summary="no affected orders to supervise")
        # an order counts as handed once a child owns it — including the case where delegate_task found an
        # equivalent task already live, which is a valid answer and must not be retried until the budget dies
        handed = {r.get("order_id") for r in results if r.get("order_id")}
        handed |= {str((h.get("args") or {}).get("order_id")) for h in history if h["tool"] == "delegate_task" and (h.get("args") or {}).get("order_id")}
        remaining = [oid for oid in targets if oid not in handed]
        # read the damage before planning anything: priority and window end decide who is recovered first, and a
        # wake-up after a delegation starts with an empty history, so only the orders still open are re-read
        for oid in remaining:
            if not any(h["tool"] == "get_order_context" and (h["result"].get("data") or {}).get("order_id") == oid for h in history):
                return Action("call_tool", "get_order_context", {"order_id": oid}, summary=f"read {oid} before deciding the order of work")
        for oid in sorted(remaining, key=lambda o: _urgency_key(history, o)):
            return Action("call_tool", "delegate_task",
                          {"role": "recovery", "order_id": oid,
                           "goal": f"recover {oid} after {facts.get('trigger', 'a disruption')}",
                           "reason": "supervisor fan-out; one owner per order"},
                          summary=f"hand {oid} to a recovery agent ({len(handed) + 1}/{len(targets)}, most urgent first)")
        auto = [r for r in results if r.get("status") == "succeeded"]
        human = [r for r in results if r.get("status") in ("waiting_human", "no_solution", "failed")]
        if "notify_in_app" not in done:
            return Action("call_tool", "notify_in_app",
                          {"recipient_ref": "dispatcher", "recipient_type": "dispatcher", "type": "recovery_summary",
                           "message": f"[simulated] {len(auto)}/{len(results)} order(s) recovered automatically; "
                                      f"{len(human)} need a person" + (f" ({', '.join(str(r.get('order_id') or r.get('task_id')) for r in human)})" if human else ""),
                           "idempotency_key": f"supervise:{task.id}"},
                          summary="report one consolidated outcome instead of N scattered notifications")
        return Action("finish", status="succeeded" if not human else "waiting_human",
                      summary=f"{len(auto)}/{len(results)} recovered automatically, {len(human)} escalated")


_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _urgency_key(history: list[dict[str, Any]], order_id: str) -> tuple[int, str, str]:
    """Most urgent priority first, then earliest window end, then id — the same order a dispatcher would work in."""
    for h in reversed(history):
        if h["tool"] == "get_order_context" and (h["result"].get("data") or {}).get("order_id") == order_id:
            data = h["result"]["data"]
            return (_RANK.get(str(data.get("effective_priority")), 9), str((data.get("window") or ["", ""])[1]), order_id)
    return (9, "", order_id)


def _assigned_technician(submit_result: dict[str, Any]) -> str | None:
    """The technician a committed plan put on the target order — the one who now carries the extra workload."""
    data = submit_result.get("data") or {}
    for key in ("technician_id", "assigned_technician_id"):
        if data.get(key):
            return str(data[key])
    for row in data.get("assignments") or []:
        if isinstance(row, dict) and row.get("technician_id"):
            return str(row["technician_id"])
    return None


def _best_candidate(res: dict[str, Any]) -> dict[str, Any] | None:
    cands = [c for c in (res.get("data") or {}).get("candidates", []) if c.get("policy") in ("auto", "manual")]
    if not cands:
        return None
    return max(cands, key=lambda c: (c.get("policy") == "auto", c.get("decision_score") or 0))
