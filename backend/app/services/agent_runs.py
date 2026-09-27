"""AgentRun tracing: steps, tool calls, facts, durations. No hidden chain-of-thought is stored or shown."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import utc_now
from app.models.entities import AgentRun
from app.services.ids import new_id


class RunTracer:
    def __init__(self, db: Session, run: AgentRun):
        self.db = db
        self.run = run
        self._t0 = time.monotonic()

    @contextmanager
    def step(self, name: str, agent: str | None = None, **input_refs: Any):
        t = time.monotonic()
        entry: dict[str, Any] = {"step": name, "agent": agent or self.run.agent, "input_refs": input_refs,
                                 "tool_calls": [], "summary": "", "status": "running", "started_ms": int((t - self._t0) * 1000)}
        steps = list(self.run.steps or [])
        steps.append(entry)
        self.run.steps = steps
        try:
            yield entry
            entry["status"] = "ok"
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            raise
        finally:
            entry["duration_ms"] = int((time.monotonic() - t) * 1000)
            self.run.steps = list(self.run.steps)
            self.db.flush()

    @staticmethod
    def tool(entry: dict[str, Any], name: str, **facts: Any) -> None:
        entry["tool_calls"].append({"tool": name, "facts": facts})

    def finish(self, status: str, summary: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.run.status = status
        self.run.summary = summary
        self.run.result = result or {}
        self.run.error = error
        self.run.finished_at = utc_now()
        self.run.duration_ms = int((time.monotonic() - self._t0) * 1000)
        self.db.flush()


def start_run(db: Session, *, agent: str, trigger: str, scenario_generation: int, target_order_id: str | None = None,
              input_refs: dict[str, Any] | None = None) -> RunTracer:
    run = AgentRun(id=new_id("run"), scenario_generation=scenario_generation, agent=agent, trigger=trigger,
                   target_order_id=target_order_id, status="processing", execution_mode=get_settings().llm_mode,
                   input_refs=input_refs or {}, steps=[], result={}, summary="")
    db.add(run)
    db.flush()
    return RunTracer(db, run)
