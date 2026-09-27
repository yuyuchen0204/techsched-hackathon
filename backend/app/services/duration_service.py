"""Repair-duration data capture and shadow prediction (V3 §13). Official durations stay on the CSV snapshot."""
from __future__ import annotations

import statistics
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import Assignment, DurationObservation, ModelVersion, ServiceReport, WorkOrder
from app.services.clock import Clock
from app.services.ids import new_id
from app.services.timeutil import iso


def record_completion(db: Session, clock: Clock, order: WorkOrder, assignment: Assignment, *, source: str) -> DurationObservation:
    """Actual service = service start → completion; travel and early waiting never count. Quality flags instead of guesses."""
    snap = order.catalog_snapshot or {}
    base = int(snap.get("repair_duration_minutes", 0))
    actual: int | None = None
    quality = "ok"
    if order.service_started_at is None or order.completed_at is None:
        quality = "missing_time"
    else:
        actual = int((order.completed_at - order.service_started_at).total_seconds() // 60)
        if actual < 0:
            quality, actual = "negative", None
        elif actual < 5 or (base and actual > base * 4):
            quality = "outlier"
    existing = db.scalars(select(DurationObservation).where(DurationObservation.order_id == order.id)).first()
    obs = existing or DurationObservation(id=new_id("dur"), scenario_generation=clock.scenario_generation, order_id=order.id,
                                          catalog_item_id=order.catalog_item_id, problem_name=str(snap.get("problem_name", "")),
                                          complexity_level=int(snap.get("complexity_level", 0)), baseline_minutes=base, observed_at=clock.now)
    obs.actual_minutes = actual
    obs.quality = quality
    obs.source = source
    obs.observed_at = clock.now
    # shadow prediction made with data available NOW (before this observation is added)
    pred, version = predict(db, clock, order.catalog_item_id, base, before=clock.now, exclude_order_id=order.id)
    obs.prediction_minutes = pred
    obs.prediction_version = version
    obs.predicted_at = clock.now
    if existing is None:
        db.add(obs)
    if order.report_status == "none":
        order.report_status = "pending"
    if db.scalars(select(ServiceReport).where(ServiceReport.order_id == order.id)).first() is None:
        db.add(ServiceReport(id=new_id("rep"), order_id=order.id, technician_id=assignment.technician_id, status="pending",
                             actual_start_at=order.service_started_at, actual_end_at=order.completed_at))
    db.flush()
    return obs


def apply_report(db: Session, order: WorkOrder, report: ServiceReport) -> None:
    """Interruptions reported by the technician reduce the effective actual duration; ambiguous data is flagged."""
    obs = db.scalars(select(DurationObservation).where(DurationObservation.order_id == order.id)).first()
    if obs is None:
        return
    obs.interruption_minutes = report.interruption_minutes
    if obs.actual_minutes is not None and report.interruption_minutes:
        if report.interruption_minutes >= obs.actual_minutes:
            obs.quality = "ambiguous"
        elif obs.quality == "ok":
            obs.actual_minutes = obs.actual_minutes - report.interruption_minutes
    if "manual" in (report.anomaly_flags or []):
        obs.source = "manual"
    db.flush()


def predict(db: Session, clock: Clock, catalog_item_id: str, baseline: int, *, before, exclude_order_id: str | None = None) -> tuple[int, str]:
    """Median of qualifying history for the same catalog item, using only observations completed before `before`.
    Falls back to the CSV baseline below min_samples. Returns (minutes, version_label)."""
    pol = get_policy().duration_prediction
    since = before - timedelta(days=pol.history_window_days)
    rows = db.scalars(select(DurationObservation).where(DurationObservation.catalog_item_id == catalog_item_id,
                                                        DurationObservation.quality == "ok", DurationObservation.observed_at < before,
                                                        DurationObservation.observed_at >= since)).all()
    vals = [r.actual_minutes - r.interruption_minutes for r in rows if r.order_id != exclude_order_id and r.actual_minutes is not None]
    if len(vals) < pol.min_samples_per_problem:
        return baseline, f"csv_baseline(n={len(vals)}<{pol.min_samples_per_problem})"
    return round(statistics.median(vals)), f"median_by_problem(n={len(vals)})"


def build_model_version(db: Session, clock: Clock) -> ModelVersion:
    """Time-split evaluation: for each observation, predict from strictly earlier observations; compare with the CSV baseline."""
    pol = get_policy().duration_prediction
    rows = sorted(db.scalars(select(DurationObservation)).all(), key=lambda r: r.observed_at)
    valid = [r for r in rows if r.quality == "ok" and r.actual_minutes is not None]
    errs_model: list[int] = []
    errs_base: list[int] = []
    fallback = 0
    per_problem: dict[str, dict[str, Any]] = {}
    for r in valid:
        pred, ver = predict(db, clock, r.catalog_item_id, r.baseline_minutes, before=r.observed_at, exclude_order_id=r.order_id)
        actual = (r.actual_minutes or 0) - r.interruption_minutes
        if ver.startswith("csv_baseline"):
            fallback += 1
        errs_model.append(abs(pred - actual))
        errs_base.append(abs(r.baseline_minutes - actual))
        pp = per_problem.setdefault(r.problem_name, {"n": 0, "mae_model": 0.0, "mae_csv": 0.0, "baseline": r.baseline_minutes, "actuals": []})
        pp["n"] += 1
        pp["mae_model"] += abs(pred - actual)
        pp["mae_csv"] += abs(r.baseline_minutes - actual)
        pp["actuals"].append(actual)
    for pp in per_problem.values():
        n = max(1, pp["n"])
        pp["mae_model"] = round(pp["mae_model"] / n, 1)
        pp["mae_csv"] = round(pp["mae_csv"] / n, 1)
        pp["median_actual"] = statistics.median(pp["actuals"])
        del pp["actuals"]
    metrics = {
        "observations_total": len(rows), "observations_valid": len(valid),
        "quality_counts": {q: len([r for r in rows if r.quality == q]) for q in ("ok", "missing_time", "negative", "outlier", "ambiguous")},
        "source_counts": {s: len([r for r in rows if r.source == s]) for s in ("simulated", "manual", "synthetic_history")},
        "mae_shadow_model": round(statistics.mean(errs_model), 2) if errs_model else None,
        "mae_csv_baseline": round(statistics.mean(errs_base), 2) if errs_base else None,
        "fallback_ratio": round(fallback / len(valid), 3) if valid else None,
        "per_problem": per_problem, "mode": pol.mode, "note": "shadow only — official scheduling durations unchanged",
    }
    mv = ModelVersion(id=new_id("mv"), method="median_by_problem_time_split", config=pol.model_dump(), metrics=metrics, samples=len(valid),
                      notes="Predictions use only observations completed before each evaluated one; <min_samples falls back to the CSV.")
    db.add(mv)
    db.flush()
    return mv


def latest_report(db: Session) -> dict[str, Any] | None:
    mv = db.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc())).first()
    if mv is None:
        return None
    return {"id": mv.id, "created_at": iso(mv.created_at), "method": mv.method, "config": mv.config, "metrics": mv.metrics,
            "samples": mv.samples, "notes": mv.notes}
