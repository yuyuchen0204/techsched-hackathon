"""Priority & risk classification (section 8). Pure functions over minutes.

base: P3 normal, P1 paid. risk_priority from active risk conditions. effective = most urgent(base, risk).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Policy
from app.models.enums import Priority, RiskType


@dataclass(frozen=True)
class RiskReason:
    type: RiskType
    priority: Priority
    detail: str
    idempotency_suffix: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type.value, "priority": self.priority.value, "detail": self.detail}


def base_priority(paid_expedite: bool, policy: Policy, expedite_now: bool = False) -> Priority:
    """P0 for a paid "send someone now", P1 for a paid busy slot, otherwise the normal base."""
    if expedite_now:
        return Priority(policy.paid_now_base)
    return Priority(policy.paid_base) if paid_expedite else Priority(policy.normal_base)


def cancellation_priority(remaining_minutes: int, policy: Policy) -> Priority:
    """Technician cancellation relative to window_end: <30 → P0; 30..120 inclusive → P1; >120 → P2."""
    lo, hi = policy.cancellation_p1_inclusive
    if remaining_minutes < policy.cancellation_p0_lt:
        return Priority.P0
    if lo <= remaining_minutes <= hi:
        return Priority.P1
    return Priority.P2


def evaluate_order_risks(
    *,
    now: int,
    window_end: int,
    lifecycle_status: str,
    service_started: bool,
    predicted_start: int | None,
    has_valid_assignment: bool,
    tech_cancel_remaining: int | None,
    verified_late_complaint: bool,
    policy: Policy,
    execution_interrupted: bool = False,
) -> list[RiskReason]:
    """Return active scheduling-risk reasons for one pending order (not COMPLETED/CANCELLED)."""
    reasons: list[RiskReason] = []
    if lifecycle_status in ("COMPLETED", "CANCELLED"):
        return reasons
    remaining = window_end - now
    if not service_started:
        if now > window_end:
            reasons.append(RiskReason(RiskType.OVERDUE_NOT_STARTED, Priority.P0,
                                      f"window_end passed {now - window_end} min ago, service not started"))
        if verified_late_complaint and now > window_end:
            reasons.append(RiskReason(RiskType.LATENESS_COMPLAINT_VERIFIED, Priority.P0,
                                      "lateness complaint verified: past deadline, service not started"))
        if tech_cancel_remaining is not None:
            p = cancellation_priority(tech_cancel_remaining, policy)
            reasons.append(RiskReason(RiskType.TECHNICIAN_CANCELLED, p,
                                      f"technician cancelled with {tech_cancel_remaining} min to window_end"))
        if execution_interrupted:
            # The technician had already set off, so the customer is expecting someone imminently: this outranks the
            # remaining-minutes ladder used for an ordinary cancellation. Until now this risk was recorded but never
            # entered the priority, leaving the order at its base grade while the risk row said P0.
            reasons.append(RiskReason(RiskType.EXECUTION_INTERRUPTED, Priority.P0,
                                      "technician became unavailable after departing; customer is expecting a visit now"))
        if now <= window_end:
            if predicted_start is not None and predicted_start > window_end:
                reasons.append(RiskReason(RiskType.PREDICTED_LATE, Priority.P1,
                                          f"predicted start {predicted_start - window_end} min after window_end"))
            elif remaining <= policy.approaching_p2_inclusive:
                reasons.append(RiskReason(RiskType.APPROACHING_DEADLINE, Priority.P2,
                                          f"{remaining} min to window_end, not predicted late"))
            if predicted_start is None and not has_valid_assignment:
                reasons.append(RiskReason(RiskType.UNASSIGNED_ETA_UNKNOWN, Priority.P3,
                                          "no valid assignment; ETA unknown"))
    return reasons


def combine(base: Priority, reasons: list[RiskReason]) -> tuple[Priority, Priority]:
    """Return (risk_priority, effective_priority)."""
    risk = Priority.P3
    for r in reasons:
        risk = Priority.most_urgent(risk, r.priority)
    return risk, Priority.most_urgent(base, risk)
