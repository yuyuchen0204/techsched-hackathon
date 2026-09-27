from __future__ import annotations

from typing import Any

from app.models.entities import (
    AgentRun,
    Assignment,
    CandidatePlan,
    CatalogItem,
    Notification,
    RiskEvent,
    ScheduleVersion,
    StandbyCandidate,
    Technician,
    WorkOrder,
)
from app.services.clock import Clock
from app.services.timeutil import iso, parse_iso


def catalog_item(c: CatalogItem) -> dict[str, Any]:
    return {"id": c.id, "trade_type": c.trade_type, "problem_name": c.problem_name, "complexity_level": c.complexity_level,
            "repair_duration_minutes": c.repair_duration_minutes, "catalog_version": c.catalog_version, "source_row": c.source_row}


def order(o: WorkOrder, assignment: Assignment | None = None, tech_name: str | None = None) -> dict[str, Any]:
    return {
        "id": o.id, "customer_ref": o.customer_ref, "customer_name": o.customer_name, "contact_phone": o.contact_phone,
        "description": o.description, "location": {"id": o.location_id, "name": o.location_name, "lat": o.lat, "lon": o.lon},
        "catalog_item_id": o.catalog_item_id, "catalog_snapshot": o.catalog_snapshot,
        "window_start": iso(o.window_start), "window_end": iso(o.window_end), "paid_expedite": o.paid_expedite,
        "expedite_now": bool(o.expedite_now),
        "base_priority": o.base_priority, "risk_priority": o.risk_priority, "effective_priority": o.effective_priority,
        "priority_reasons": o.priority_reasons, "lifecycle_status": o.lifecycle_status, "scheduling_status": o.scheduling_status,
        "technician_id": o.technician_id, "technician_name": tech_name, "version": o.version,
        "departed_at": iso(o.departed_at), "arrived_at": iso(o.arrived_at), "service_started_at": iso(o.service_started_at),
        "completed_at": iso(o.completed_at), "cancelled_at": iso(o.cancelled_at), "cancel_reason": o.cancel_reason,
        "recovery_start": iso(o.recovery_start), "breach_recorded_at": iso(o.breach_recorded_at),
        "can_cancel": o.lifecycle_status == "OPEN", "locked": o.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"),
        "can_abort_execution": o.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS"),
        "assignment": assignment_row(assignment) if assignment else None,
        "pending_plan_run_id": o.pending_plan_run_id, "created_at": iso(o.created_at), "updated_at": iso(o.updated_at),
        "customer_id": o.customer_id, "address": o.address or {}, "report_status": o.report_status,
        "excluded_technician_ids": o.excluded_technician_ids or [], "human_case_id": o.human_case_id,
    }


def assignment_row(a: Assignment) -> dict[str, Any]:
    return {
        "id": a.id, "order_id": a.order_id, "technician_id": a.technician_id, "schedule_version_id": a.schedule_version_id,
        "origin_location_id": a.origin_location_id, "departure": iso(a.departure), "arrival": iso(a.arrival),
        "service_start": iso(a.service_start), "service_end": iso(a.service_end), "travel_minutes": a.travel_minutes,
        "waiting_minutes": a.waiting_minutes, "status": a.status, "locked": a.locked, "match_score": a.match_score,
        "score_components": a.score_components,
    }


def technician(t: Technician) -> dict[str, Any]:
    return {
        "id": t.id, "name": t.name, "skills": t.skills, "certifications": t.certifications,
        "home_location_id": t.home_location_id, "current_location_id": t.current_location_id, "lat": t.lat, "lon": t.lon,
        "shift_start": iso(t.shift_start), "shift_end": iso(t.shift_end),
        "breaks": [{"start": iso(parse_iso(b["start"])), "end": iso(parse_iso(b["end"]))} for b in (t.breaks or [])],
        "unavailable_intervals": [{"start": iso(parse_iso(u["start"])), "end": iso(parse_iso(u["end"])), "reason": u.get("reason")}
                                  for u in (t.unavailable_intervals or [])],
        "status": t.status, "version": t.version, "sim_mode": t.sim_mode, "demo_login": t.demo_login,
    }


def risk(r: RiskEvent) -> dict[str, Any]:
    return {"id": r.id, "type": r.type, "target_order_id": r.target_order_id, "technician_id": r.technician_id,
            "severity": r.severity, "status": r.status, "payload": r.payload, "effective_time": iso(r.effective_time),
            "first_seen": iso(r.first_seen), "last_seen": iso(r.last_seen), "resolved_at": iso(r.resolved_at), "run_id": r.run_id}


def standby(s: StandbyCandidate) -> dict[str, Any]:
    return {"id": s.id, "order_id": s.order_id, "technician_id": s.technician_id, "technician_name": s.technician_name,
            "earliest_start": iso(s.earliest_start), "skill_match": s.skill_match, "snapshot_version": s.snapshot_version,
            "computed_at": iso(s.computed_at), "expires_at": iso(s.expires_at), "valid": s.valid}


def plan(p: CandidatePlan, clock: Clock | None = None) -> dict[str, Any]:
    rows = []
    for r in p.assignments or []:
        row = dict(r)
        if clock is not None:
            for k in ("departure", "arrival", "service_start", "service_end"):
                row[f"{k}_local"] = iso(clock.from_minutes(int(r[k])))
        rows.append(row)
    diff = []
    for d in p.diff or []:
        dd = dict(d)
        if clock is not None:
            dd["old_start_local"] = iso(clock.from_minutes(d["old_start"])) if d.get("old_start") is not None else None
            dd["new_start_local"] = iso(clock.from_minutes(d["new_start"])) if d.get("new_start") is not None else None
        diff.append(dd)
    metrics = dict(p.metrics or {})
    if clock is not None and metrics.get("target_service_start") is not None:
        metrics["target_service_start_local"] = iso(clock.from_minutes(int(metrics["target_service_start"])))
    return {
        "id": p.id, "run_id": p.run_id, "target_order_id": p.target_order_id, "strategy": p.strategy,
        "strategy_tags": metrics.get("strategy_tags", [p.strategy]), "status": p.status, "status_reason": p.status_reason,
        "decision": p.decision, "decision_score": p.decision_score, "scores": p.scores, "affected_order_ids": p.affected_order_ids,
        "assignments": rows, "diff": diff, "policy_check": p.policy_check, "validation": p.validation, "metrics": metrics,
        "base_schedule_version": p.base_schedule_version, "base_data_version": p.base_data_version,
        "route_snapshot_id": p.route_snapshot_id, "policy_version": p.policy_version,
        "generated_at": iso(p.generated_at), "expires_at": iso(p.expires_at), "explanation": p.explanation,
        "approvable": p.status == "PENDING_REVIEW",
    }


def agent_run(r: AgentRun) -> dict[str, Any]:
    return {"id": r.id, "agent": r.agent, "trigger": r.trigger, "target_order_id": r.target_order_id, "status": r.status,
            "execution_mode": r.execution_mode, "started_at": iso(r.started_at), "finished_at": iso(r.finished_at),
            "duration_ms": r.duration_ms, "input_refs": r.input_refs, "steps": r.steps, "result": r.result,
            "summary": r.summary, "error": r.error}


def notification(n: Notification) -> dict[str, Any]:
    return {"id": n.id, "recipient_ref": n.recipient_ref, "recipient_type": n.recipient_type, "type": n.type,
            "message": n.message, "order_id": n.order_id, "delivery_mode": n.delivery_mode, "status": n.status,
            "sim_time": iso(n.sim_time), "created_at": iso(n.created_at)}


def schedule_version(v: ScheduleVersion) -> dict[str, Any]:
    return {"id": v.id, "parent_id": v.parent_id, "reason": v.reason, "created_at": iso(v.created_at), "sim_now": iso(v.sim_now),
            "active": v.active, "policy_version": v.policy_version, "route_snapshot_id": v.route_snapshot_id,
            "plan_id": v.plan_id, "metrics": v.metrics, "assignment_count": len(v.snapshot or [])}
