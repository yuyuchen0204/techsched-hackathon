"""Request schemas (discriminated unions for events). Clients can never set complexity, duration, priority or scores."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    customer_ref: str = Field(min_length=1, max_length=64)
    customer_name: str = Field(min_length=1, max_length=120)
    contact_phone: str = Field(min_length=3, max_length=40)
    description: str = Field(default="", max_length=2000)
    location_id: str
    catalog_item_id: str
    window_start: datetime
    window_end: datetime
    paid_expedite: bool = False
    dispatch: bool = True


class OrderPatch(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    contact_phone: str | None = Field(default=None, max_length=40)


class CancelRequest(BaseModel):
    customer_ref: str | None = None
    reason: str | None = None
    actor: str = "customer"
    idempotency_key: str | None = None


class UnavailabilityRequest(BaseModel):
    start: datetime
    end: datetime
    reason: str = "unavailable"
    idempotency_key: str | None = None


class AbortExecutionRequest(BaseModel):
    """Dispatcher releases an order whose technician already departed. A reason is mandatory: this is an audited decision."""
    reason: str = Field(min_length=1)
    outcome: Literal["reschedule", "cancel"] = "reschedule"
    actor: str = "dispatcher"
    idempotency_key: str | None = None


class ExecutionEventRequest(BaseModel):
    event: Literal["depart", "arrive", "start", "complete"]
    actor: str = "technician_panel"


class PointRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str | None = Field(default=None, max_length=120)


class InitialScheduleRequest(BaseModel):
    order_ids: list[str] | None = None


class DispatchRequest(BaseModel):
    order_id: str
    trigger: str = "manual_dispatch"


class PaidExpediteEvent(BaseModel):
    type: Literal["paid_expedite_order"]
    order_id: str
    customer_ref: str | None = None
    idempotency_key: str | None = None


class TechnicianUnavailableEvent(BaseModel):
    type: Literal["technician_unavailable"]
    technician_id: str
    start: datetime | None = None
    end: datetime | None = None
    reason: str = "unavailable"
    idempotency_key: str | None = None


class LatenessComplaintEvent(BaseModel):
    type: Literal["lateness_complaint"]
    order_id: str
    text: str = ""
    customer_ref: str | None = None
    idempotency_key: str | None = None


class NonSchedulingComplaintEvent(BaseModel):
    type: Literal["non_scheduling_complaint"]
    order_id: str
    complaint_type: Literal["attitude", "quality", "other"] = "other"
    text: str = ""
    customer_ref: str | None = None
    idempotency_key: str | None = None


class CustomerCancelEvent(BaseModel):
    type: Literal["customer_cancel"]
    order_id: str
    customer_ref: str | None = None
    reason: str | None = None
    idempotency_key: str | None = None


EventRequest = Annotated[
    PaidExpediteEvent | TechnicianUnavailableEvent | LatenessComplaintEvent | NonSchedulingComplaintEvent | CustomerCancelEvent,
    Field(discriminator="type"),
]


class ApproveRequest(BaseModel):
    actor: str = "dispatcher"
    reason: str | None = None
    idempotency_key: str | None = None
    expected_versions: dict[str, int] | None = None


class RejectRequest(BaseModel):
    actor: str = "dispatcher"
    reason: str | None = None
    idempotency_key: str | None = None


class ClockAdvanceRequest(BaseModel):
    minutes: int = Field(ge=1, le=1440)


class ClockControlRequest(BaseModel):
    running: bool


class ResetRequest(BaseModel):
    scenario: str = "main"
    seed: int | None = None


class ChatMessageRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    message: str = Field(default="", max_length=2000)
    action: str | None = None  # select_catalog | select_location | set_window | set_contact | toggle_paid | confirm | cancel_order | complaint | reset
    payload: dict | None = None


class InterpretRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class EvaluationRequest(BaseModel):
    seeds: list[int] | None = None
    scenarios: list[str] | None = None
