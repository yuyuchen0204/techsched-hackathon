"""SchedulingAgent: wraps solver/validator/scorer as tools and produces structured explanations.

Numbers in explanations come from solver metrics — never from a language model.
"""
from __future__ import annotations

from typing import Any

from app.scheduling.domain import Snapshot
from app.scheduling.solver import Candidate
from app.services.clock import Clock
from app.services.timeutil import hhmm


def explain_candidate(clock: Clock, snap: Snapshot, cand: Candidate, target_id: str, outcome: dict[str, Any]) -> str:
    target = cand.plan.get(target_id)
    parts: list[str] = []
    if target is not None:
        tech = snap.techs[target.tech_id]
        parts.append(f"{tech.name} starts {target_id} at {hhmm(clock.from_minutes(target.service_start))} "
                     f"(travel {target.travel} min, wait {target.waiting} min).")
    if cand.affected.count == 0:
        parts.append("No other order changes.")
    else:
        moved = []
        for d in cand.affected.diff:
            if d.counts:
                if d.change == "time":
                    moved.append(f"{d.order_id} start {hhmm(clock.from_minutes(d.old_start or 0))}→{hhmm(clock.from_minutes(d.new_start or 0))}")
                else:
                    moved.append(f"{d.order_id} {d.old_tech}→{d.new_tech} at {hhmm(clock.from_minutes(d.new_start or 0))}")
        parts.append(f"Affects {cand.affected.count} order(s): " + "; ".join(moved) + ".")
    if cand.decision_score is not None:
        parts.append(f"Lowest changed-assignment score {cand.decision_score:.1f} (threshold >{outcome.get('threshold', 70):g}).")
    parts.append(f"Policy: {outcome.get('decision')} — " + "; ".join(outcome.get("reasons", [])[:3]) + ".")
    return " ".join(parts)
