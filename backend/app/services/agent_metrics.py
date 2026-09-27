"""Agent scorecard: how well the agent layer itself behaved, which no other report in this system measures.

`evaluation_service` grades the *scheduling* outcome (would a different policy have placed this order better?).
This grades the *agent*: did it get there on its own, how much did it spend, how often did it walk into a fence it
was told about, and when it gave up did it hand over something a person can act on. Everything here is derived from
rows the runtime already writes (AgentTask, ToolTrace) — no extra instrumentation, so it cannot drift from reality.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.skills import load_skills
from app.config import get_policy
from app.models.entities import AgentTask, ToolTrace
from app.services.clock import get_clock

# A tool call that never had a chance: the agent was told the rule and walked into it anyway.
WASTED_CODES = {"ROLE_NOT_ALLOWED", "TOOL_NOT_IN_SKILL", "INVALID_ARGS", "UNKNOWN_TOOL", "SEARCH_BUDGET_EXHAUSTED",
                "DELEGATE_SAME_ROLE", "DELEGATION_DEPTH_EXCEEDED", "UNKNOWN_ROLE"}
# What a hand-over to a human must contain to be actionable rather than a shrug.
EVIDENCE_FIELDS = ("evidence_refs", "attempted_actions", "suggested_next_action")
RESOLVED = ("succeeded",)
ESCALATED = ("waiting_human", "no_solution", "failed")


def _pct(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(100.0 * part / whole, 1)


def scorecard(db: Session, *, generation: int | None = None) -> dict[str, Any]:
    clock = get_clock(db)
    gen = clock.scenario_generation if generation is None else generation
    tasks = db.scalars(select(AgentTask).where(AgentTask.scenario_generation == gen)).all()
    if not tasks:
        return {"scenario_generation": gen, "tasks": 0, "note": "no agent tasks in this scenario yet"}
    ids = [t.id for t in tasks]
    by_id = {t.id: t for t in tasks}
    traces = db.scalars(select(ToolTrace).where(ToolTrace.task_id.in_(ids)).order_by(ToolTrace.seq)).all()

    finished = [t for t in tasks if t.status not in ("pending", "running")]
    settled = [t for t in finished if t.status in RESOLVED + ESCALATED]
    resolved = [t for t in settled if t.status in RESOLVED]
    escalated = [t for t in settled if t.status in ESCALATED]
    budget_dead = [t for t in tasks if "budget" in str((t.outcome or {}).get("summary", ""))]
    degraded = [t for t in tasks if (t.outcome or {}).get("degraded")]

    calls = [x for x in traces if x.phase != "planned" and x.tool != "finish"]
    wasted = [x for x in calls if set(x.reason_codes or []) & WASTED_CODES]
    repeats = _repeated_searches(traces)

    escalations = [x for x in traces if x.tool == "flag_for_human" and x.phase != "planned"]
    complete = [x for x in escalations if all((x.args_summary or {}).get(f) for f in EVIDENCE_FIELDS)]

    delegations = [x for x in traces if x.tool == "delegate_task" and x.phase != "planned"]
    children = [t for t in tasks if t.parent_task_id]
    thoughts = [x for x in traces if x.phase == "planned"]

    by_role = Counter(t.role for t in tasks)
    by_status = Counter(t.status for t in tasks)
    unused = sorted(set(load_skills()) - set(by_role))

    return {
        "scenario_generation": gen,
        "tasks": len(tasks),
        "by_role": dict(sorted(by_role.items())),
        "by_status": dict(sorted(by_status.items())),
        "skills_loaded": sorted(load_skills()),
        "skills_never_used": unused,
        "autonomy": {
            "label": "Settled without a human",
            "resolved": len(resolved), "escalated": len(escalated),
            "rate_pct": _pct(len(resolved), len(settled)),
        },
        "cost": {
            "label": "Tool calls per finished task",
            "avg_tool_calls": round(sum(t.tool_budget_used for t in finished) / len(finished), 2) if finished else None,
            "avg_searches": round(sum(t.search_budget_used for t in finished) / len(finished), 2) if finished else None,
            "tool_call_budget": get_policy().agent.max_tool_calls_per_wakeup,
            "budget_exhausted": len(budget_dead),
            "budget_exhausted_pct": _pct(len(budget_dead), len(tasks)),
        },
        "discipline": {
            "label": "Calls that could never have worked",
            "total_calls": len(calls), "wasted_calls": len(wasted),
            "wasted_pct": _pct(len(wasted), len(calls)),
            "repeated_searches": repeats,
            "reason_codes": dict(Counter(c for x in wasted for c in (x.reason_codes or [])).most_common()),
        },
        "handover_quality": {
            "label": "Escalations carrying evidence, attempts and a next step",
            "escalations": len(escalations), "complete": len(complete),
            "rate_pct": _pct(len(complete), len(escalations)),
            "missing_fields": dict(Counter(f for x in escalations for f in EVIDENCE_FIELDS
                                           if not (x.args_summary or {}).get(f)).most_common()),
        },
        "collaboration": {
            "label": "Work handed from one agent to another",
            "delegations": len(delegations), "child_tasks": len(children),
            "max_depth": max([t.delegation_depth for t in tasks], default=0),
            "pairs": dict(Counter(f"{by_id[t.parent_task_id].role if t.parent_task_id in by_id else '?'} → {t.role}"
                                  for t in children).most_common()),
        },
        "transparency": {
            "label": "Steps that recorded why, not just what",
            "steps": len(thoughts), "with_reason": sum(1 for x in thoughts if x.thought),
            "rate_pct": _pct(sum(1 for x in thoughts if x.thought), len(thoughts)),
        },
        "execution_mode": {
            "label": "Who decided",
            "by_mode": dict(Counter(t.execution_mode for t in tasks)),
            "decided_by": dict(Counter(x.decided_by for x in traces)),
            "degraded_tasks": len(degraded),
        },
    }


def _repeated_searches(traces: Sequence[ToolTrace]) -> int:
    """Identical search, identical args, same task — the one thing every skill file explicitly forbids."""
    seen: set[tuple[str, str, str]] = set()
    repeats = 0
    for x in traces:
        if x.phase != "planned" or x.tool not in ("simulate_insertion", "search_local_repair", "propose_alternative_windows"):
            continue
        key = (x.task_id, x.tool, str(sorted((x.args_summary or {}).items())))
        if key in seen:
            repeats += 1
        seen.add(key)
    return repeats
