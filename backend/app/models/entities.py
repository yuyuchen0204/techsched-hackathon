from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    trade_type: Mapped[str] = mapped_column(String(120), index=True)
    problem_name: Mapped[str] = mapped_column(String(200))
    complexity_level: Mapped[int] = mapped_column(Integer)
    repair_duration_minutes: Mapped[int] = mapped_column(Integer)
    source_row: Mapped[int] = mapped_column(Integer)
    source_file_hash: Mapped[str] = mapped_column(String(64))
    catalog_version: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CatalogImport(Base):
    __tablename__ = "catalog_imports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ok: Mapped[bool] = mapped_column(Boolean)
    catalog_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)


class Location(Base):
    """Preset locations (synthetic Singapore public areas). Used for geocoding-by-selection."""
    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    area: Mapped[str | None] = mapped_column(String(60), nullable=True)


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    customer_ref: Mapped[str] = mapped_column(String(64), index=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    contact_phone: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    location_id: Mapped[str] = mapped_column(String(40))
    location_name: Mapped[str] = mapped_column(String(120))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    catalog_item_id: Mapped[str] = mapped_column(String(32))
    catalog_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    window_start: Mapped[datetime] = mapped_column(DateTime)  # UTC naive
    window_end: Mapped[datetime] = mapped_column(DateTime)
    paid_expedite: Mapped[bool] = mapped_column(Boolean, default=False)
    # paid "come now, as fast as possible" (P0) rather than paid "take this busy slot" (P1)
    expedite_now: Mapped[bool] = mapped_column(Boolean, default=False)
    base_priority: Mapped[str] = mapped_column(String(2), default="P3")
    risk_priority: Mapped[str] = mapped_column(String(2), default="P3")
    effective_priority: Mapped[str] = mapped_column(String(2), default="P3", index=True)
    priority_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    scheduling_status: Mapped[str] = mapped_column(String(20), default="UNASSIGNED", index=True)
    technician_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    departed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    service_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recovery_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    breach_recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_dispatch_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pending_plan_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # V3
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    address: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # structured address (see CustomerAddress fields)
    report_status: Mapped[str] = mapped_column(String(20), default="none")  # none | pending | complete
    excluded_technician_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)  # order-level explicit exclusions
    human_case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expedite_event_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expedite_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # post-order expedite: status / original window / messages


class Technician(Base):
    __tablename__ = "technicians"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    name: Mapped[str] = mapped_column(String(120))
    skills: Mapped[dict[str, int]] = mapped_column(JSON)  # trade_type -> level (1..5)
    certifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    home_location_id: Mapped[str] = mapped_column(String(40))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    current_location_id: Mapped[str] = mapped_column(String(40))
    shift_start: Mapped[datetime] = mapped_column(DateTime)
    shift_end: Mapped[datetime] = mapped_column(DateTime)
    breaks: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{start,end}] ISO UTC
    unavailable_intervals: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{start,end,reason}]
    status: Mapped[str] = mapped_column(String(20), default="AVAILABLE")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    # V3
    sim_mode: Mapped[str] = mapped_column(String(10), default="auto")  # auto (simulator drives) | manual (technician app drives)
    demo_login: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    technician_id: Mapped[str] = mapped_column(String(32), index=True)
    schedule_version_id: Mapped[int] = mapped_column(Integer)
    origin_location_id: Mapped[str] = mapped_column(String(40))
    departure: Mapped[datetime] = mapped_column(DateTime)
    arrival: Mapped[datetime] = mapped_column(DateTime)
    service_start: Mapped[datetime] = mapped_column(DateTime)
    service_end: Mapped[datetime] = mapped_column(DateTime)
    travel_minutes: Mapped[int] = mapped_column(Integer)
    waiting_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", index=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invalidated_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ScheduleVersion(Base):
    __tablename__ = "schedule_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    sim_now: Mapped[datetime] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    policy_version: Mapped[str] = mapped_column(String(40))
    route_snapshot_id: Mapped[str] = mapped_column(String(64))
    plan_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    snapshot: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    target_order_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    technician_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effective_time: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime)
    last_seen: Mapped[datetime] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class StandbyCandidate(Base):
    __tablename__ = "standby_candidates"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    technician_id: Mapped[str] = mapped_column(String(32))
    technician_name: Mapped[str] = mapped_column(String(120))
    earliest_start: Mapped[datetime] = mapped_column(DateTime)
    skill_match: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snapshot_version: Mapped[int] = mapped_column(Integer)
    computed_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    valid: Mapped[bool] = mapped_column(Boolean, default=True)


class CandidatePlan(Base):
    __tablename__ = "candidate_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    target_order_id: Mapped[str] = mapped_column(String(32), index=True)
    strategy: Mapped[str] = mapped_column(String(40))
    base_schedule_version: Mapped[int] = mapped_column(Integer)
    base_data_version: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # order_id/tech_id -> version
    route_snapshot_id: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(40))
    generated_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    assignments: Mapped[list[Any]] = mapped_column(JSON, default=list)
    diff: Mapped[list[Any]] = mapped_column(JSON, default=list)
    affected_order_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    policy_check: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED", index=True)
    decision: Mapped[str] = mapped_column(String(20), default="manual")
    explanation: Mapped[str] = mapped_column(Text, default="")
    status_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_versions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    agent: Mapped[str] = mapped_column(String(60))
    trigger: Mapped[str] = mapped_column(String(80))
    target_order_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="received")
    execution_mode: Mapped[str] = mapped_column(String(20), default="mock")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    steps: Mapped[list[Any]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    recipient_ref: Mapped[str] = mapped_column(String(64), index=True)
    recipient_type: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    delivery_mode: Mapped[str] = mapped_column(String(20), default="simulated")
    status: Mapped[str] = mapped_column(String(20), default="delivered")
    dedupe_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    sim_time: Mapped[datetime] = mapped_column(DateTime)


class SimulationState(Base):
    __tablename__ = "simulation_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    now: Mapped[datetime] = mapped_column(DateTime)  # UTC naive
    day_origin: Mapped[datetime] = mapped_column(DateTime)  # local midnight in UTC
    timezone: Mapped[str] = mapped_column(String(40))
    running: Mapped[bool] = mapped_column(Boolean, default=False)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0)
    scenario_name: Mapped[str] = mapped_column(String(60), default="main")
    seed: Mapped[int] = mapped_column(Integer, default=42)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    draft: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    messages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(30), default="collecting")


class InboundEvent(Base):
    __tablename__ = "inbound_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="received")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = [
    "AgentRun",
    "Approval",
    "Assignment",
    "CandidatePlan",
    "CatalogImport",
    "CatalogItem",
    "ChatSession",
    "Evaluation",
    "ForeignKey",
    "InboundEvent",
    "Location",
    "Notification",
    "RiskEvent",
    "ScheduleVersion",
    "SimulationState",
    "StandbyCandidate",
    "Technician",
    "UniqueConstraint",
    "WorkOrder",
]


# ----------------------------------------------------------------------------------------------------------------
# V3 entities (third-meeting upgrade). All additive; existing rows keep working (see app/db/migrations.py).
# ----------------------------------------------------------------------------------------------------------------


class Customer(Base):
    """Stable customer identity (demo login), separate from chat sessions."""
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(40))
    demo_login: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # identity selector handle
    notes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    location_id: Mapped[str] = mapped_column(String(40))  # routing point (preset or pt_*)
    formatted_address: Mapped[str] = mapped_column(String(300))
    postal_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    building_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    street_address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unit_number: Mapped[str | None] = mapped_column(String(40), nullable=True)  # None = not applicable / not given
    unit_not_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(30))  # onemap | nominatim | map_pin | preset | migrated
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    technician_id: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(20))  # depart | arrive | start | complete
    sim_time: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(30))  # technician_app | simulation | dispatcher
    anomalies: Mapped[list[Any]] = mapped_column(JSON, default=list)  # e.g. started_before_window, late_vs_plan
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ServiceReport(Base):
    __tablename__ = "service_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True, unique=True)
    technician_id: Mapped[str] = mapped_column(String(32))
    actual_problem_text: Mapped[str] = mapped_column(Text, default="")
    matched_catalog_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution: Mapped[str] = mapped_column(String(40), default="")  # fixed | partial | needs_parts | not_fixed
    notes: Mapped[str] = mapped_column(Text, default="")
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    interruption_minutes: Mapped[int] = mapped_column(Integer, default=0)
    anomaly_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | complete
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class CustomerFeedback(Base):
    __tablename__ = "customer_feedback"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    technician_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1..5
    target: Mapped[str] = mapped_column(String(20), default="technician")  # technician | dispatch | platform
    reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)  # structured codes
    comment: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    sim_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HumanCase(Base):
    """Human takeover item. Distinct from Approval: approving a plan never bypasses hard constraints."""
    __tablename__ = "human_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    source: Mapped[str] = mapped_column(String(30))  # CUSTOMER_REQUEST | POLICY_REQUIRED | AGENT_ESCALATION
    category: Mapped[str] = mapped_column(String(40))
    urgency: Mapped[str] = mapped_column(String(10), default="normal")  # low | normal | high | critical
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending | in_progress | waiting_customer | resolved
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    incident_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason_summary: Mapped[str] = mapped_column(Text, default="")
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    attempted_actions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    unresolved_questions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    suggested_next_action: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    assignee: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    replies: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{from, text, at}]
    escalations: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SafetyIncident(Base):
    __tablename__ = "safety_incidents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    danger_type: Mapped[str] = mapped_column(String(40))  # gas_leak | fire | electric_shock | flooding | injury | other
    description: Mapped[str] = mapped_column(Text, default="")
    known_location: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | open | contact_opened | contacted | resolved
    contact_events: Mapped[list[Any]] = mapped_column(JSON, default=list)  # [{kind, channel, at, note}]
    human_case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    detection: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # triggers, llm_assessment
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    role: Mapped[str] = mapped_column(String(30))  # customer | scheduling | recovery | break
    goal: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    technician_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    incident_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    # pending | running | succeeded | waiting_customer | waiting_human | waiting_agent | stale | no_solution | failed
    snapshot_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # agent-to-agent delegation: a task may hand a sub-problem to another role and pause until that child finishes
    parent_task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    delegation_depth: Mapped[int] = mapped_column(Integer, default=0)
    child_task_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    waiting_child_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    facts_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tried_plan_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    pending_question_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    human_case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_budget_used: Mapped[int] = mapped_column(Integer, default=0)
    search_budget_used: Mapped[int] = mapped_column(Integer, default=0)
    wakeup_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(20), default="mock")  # mock | real
    outcome: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ToolTrace(Base):
    __tablename__ = "tool_traces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(20))  # planned | called | returned | validated | submitted | error | decision
    tool: Mapped[str] = mapped_column(String(60))
    # the policy's own one-line reason for this step — without it a trace shows what happened but never why
    thought: Mapped[str | None] = mapped_column(Text, nullable=True)
    args_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reason_codes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    data_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(20), default="mock")  # mock | model
    sim_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class BreakBlock(Base):
    """Dynamic rest block (replaces the uniform fixed lunch). Committed blocks are unavailability for scheduling."""
    __tablename__ = "break_blocks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    technician_id: Mapped[str] = mapped_column(String(32), index=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    end: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(20), default="dynamic")  # dynamic | declared | migrated
    status: Mapped[str] = mapped_column(String(20), default="planned")  # planned | in_progress | done | cancelled
    reason: Mapped[str] = mapped_column(String(200), default="")
    schedule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(30), default="agent")
    idempotency_key: Mapped[str | None] = mapped_column(String(160), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class CustomerQuestion(Base):
    __tablename__ = "customer_questions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(40))  # confirm_history | choose_window | clarify_problem | confirm_address | other
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | answered | expired
    answer: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RescheduleRequest(Base):
    __tablename__ = "reschedule_requests"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    requested_window_start: Mapped[datetime] = mapped_column(DateTime)
    requested_window_end: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    human_case_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DurationObservation(Base):
    __tablename__ = "duration_observations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scenario_generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    catalog_item_id: Mapped[str] = mapped_column(String(32), index=True)
    problem_name: Mapped[str] = mapped_column(String(200))
    complexity_level: Mapped[int] = mapped_column(Integer)
    baseline_minutes: Mapped[int] = mapped_column(Integer)
    actual_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interruption_minutes: Mapped[int] = mapped_column(Integer, default=0)
    quality: Mapped[str] = mapped_column(String(30), default="ok")  # ok | missing_time | negative | outlier | ambiguous
    source: Mapped[str] = mapped_column(String(20), default="simulated")  # simulated | manual | synthetic_history
    observed_at: Mapped[datetime] = mapped_column(DateTime)
    prediction_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    predicted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    method: Mapped[str] = mapped_column(String(60))
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
