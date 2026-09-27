from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DB, locked
from app.db.base import utc_now
from app.models.entities import Evaluation
from app.schemas.api import EvaluationRequest
from app.services.evaluation_service import run_evaluation
from app.services.ids import new_id
from app.services.order_service import OrderError

router = APIRouter(prefix="/api")


@router.post("/evaluations")
@locked
def start_evaluation(body: EvaluationRequest, db: Session) -> dict[str, Any]:
    ev = Evaluation(id=new_id("eval"), status="running", config={"seeds": body.seeds, "scenarios": body.scenarios})
    db.add(ev)
    db.flush()
    try:
        result = run_evaluation(seeds=body.seeds, scenario=(body.scenarios or ["main"])[0])
        ev.results = result
        ev.status = "completed"
    except Exception as exc:  # noqa: BLE001
        ev.status = "failed"
        ev.error = str(exc)
    ev.finished_at = utc_now()
    return {"id": ev.id, "status": ev.status, "summary": ev.results.get("summary") if ev.results else None, "error": ev.error}


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str, db: Session = DB) -> dict[str, Any]:
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise OrderError("not_found", "evaluation not found", status=404)
    return {"id": ev.id, "status": ev.status, "config": ev.config, "results": ev.results, "error": ev.error}


@router.get("/evaluations")
def list_evaluations(db: Session = DB) -> list[dict[str, Any]]:
    return [{"id": e.id, "status": e.status, "created_at": e.created_at.isoformat(), "summary": (e.results or {}).get("summary")}
            for e in db.scalars(select(Evaluation).order_by(Evaluation.created_at.desc()).limit(20)).all()]


@router.post("/evaluations/v3")
@locked
def start_v3_evaluation(body: EvaluationRequest, db: Session) -> dict[str, Any]:
    """§18.4: fast path vs agent orchestration and fixed lunch vs dynamic rest, per load scenario (offline, fixture routes)."""
    from app.services.evaluation_service import run_v3_evaluation
    ev = Evaluation(id=new_id("eval"), status="running", config={"kind": "v3", "seeds": body.seeds, "scenarios": body.scenarios})
    db.add(ev)
    db.flush()
    try:
        ev.results = run_v3_evaluation(seeds=body.seeds, scenarios=body.scenarios)
        ev.status = "completed"
    except Exception as exc:  # noqa: BLE001
        ev.status = "failed"
        ev.error = str(exc)
    ev.finished_at = utc_now()
    return {"id": ev.id, "status": ev.status, "results": ev.results, "error": ev.error}
