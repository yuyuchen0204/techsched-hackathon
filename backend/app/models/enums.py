from __future__ import annotations

from enum import StrEnum


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

    @property
    def rank(self) -> int:  # lower = more urgent
        return int(self.value[1])

    @staticmethod
    def most_urgent(*values: Priority) -> Priority:
        return min(values, key=lambda p: p.rank)


class LifecycleStatus(StrEnum):
    DRAFT = "DRAFT"
    NEEDS_INFO = "NEEDS_INFO"
    OPEN = "OPEN"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


DEPARTED_STATUSES = {LifecycleStatus.EN_ROUTE, LifecycleStatus.ARRIVED, LifecycleStatus.IN_PROGRESS}
TERMINAL_STATUSES = {LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED}


class SchedulingStatus(StrEnum):
    UNASSIGNED = "UNASSIGNED"
    PROPOSED = "PROPOSED"
    PENDING_REVIEW = "PENDING_REVIEW"
    ASSIGNED = "ASSIGNED"
    UNRESOLVED = "UNRESOLVED"


class AssignmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"


class TechnicianStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    BUSY = "BUSY"
    UNAVAILABLE = "UNAVAILABLE"
    OFF_SHIFT = "OFF_SHIFT"
    BREAK = "BREAK"


class PlanStatus(StrEnum):
    PROPOSED = "PROPOSED"
    PENDING_REVIEW = "PENDING_REVIEW"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    OVER_LIMIT = "OVER_LIMIT"
    SUPERSEDED = "SUPERSEDED"


class PolicyDecision(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"
    FORBIDDEN = "forbidden"
    NO_ACTION = "no_action"
    UNRESOLVED = "unresolved"
    NEEDS_INFO = "needs_info"
    STANDBY = "standby"


class RiskType(StrEnum):
    OVERDUE_NOT_STARTED = "OVERDUE_NOT_STARTED"
    PREDICTED_LATE = "PREDICTED_LATE"
    APPROACHING_DEADLINE = "APPROACHING_DEADLINE"
    TECHNICIAN_CANCELLED = "TECHNICIAN_CANCELLED"
    LATENESS_COMPLAINT_VERIFIED = "LATENESS_COMPLAINT_VERIFIED"
    NON_SCHEDULING_COMPLAINT = "NON_SCHEDULING_COMPLAINT"
    EXECUTION_INTERRUPTED = "EXECUTION_INTERRUPTED"
    UNASSIGNED_ETA_UNKNOWN = "UNASSIGNED_ETA_UNKNOWN"


class RiskStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    MANUAL = "manual"


class RunStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    UNRESOLVED = "unresolved"
    FAILED = "failed"
    NO_ACTION = "no_action"


class SolveStatus(StrEnum):
    FEASIBLE = "feasible"
    PARTIAL = "partial"
    NO_SOLUTION_FOUND = "no_solution_found"
    TIMEOUT = "timeout"
    ERROR = "error"
