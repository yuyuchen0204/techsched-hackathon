"""LLM provider contract. mock and real return the SAME schemas; the system never trusts model output as fact."""
from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class CatalogChoice(BaseModel):
    catalog_item_id: str
    label: str
    score: float = 0.0


class SafetyConcern(BaseModel):
    """Model-side safety flag (§11): an input to the deterministic incident rules, never an action by itself."""
    type: str = Field(description="gas_leak | fire | electric_shock | flooding | injury | other")
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence: str = Field(default="", description="the words that indicate danger, verbatim")


class InterpretedRequest(BaseModel):
    """Structured understanding of a customer message. IDs must come from the provided catalog list."""
    catalog_item_id: str | None = Field(default=None, description="Best matching catalog item id from the provided list, or null")
    confidence: float = Field(default=0.0, ge=0, le=1)
    alternatives: list[str] = Field(default_factory=list, description="Other plausible catalog item ids from the provided list")
    customer_name: str | None = None
    contact_phone: str | None = None
    location_hint: str | None = Field(default=None, description="Area/place name mentioned by the customer, verbatim")
    time_hint: str | None = Field(default=None, description="Any appointment time mentioned, verbatim")
    urgency_mentioned: bool = False
    payment_claimed: bool = Field(default=False, description="Customer claims to have paid; NOT treated as a payment fact")
    intent: str = Field(default="new_request", description="new_request | status | cancel | complaint | expedite | smalltalk | other")
    language: str = "en"
    clarifying_question: str | None = None
    summary: str = ""
    safety_concern: SafetyConcern | None = Field(default=None, description="Set only when the message describes a CURRENT danger (gas smell, fire, sparks, shock, flooding, injury); null otherwise")


class ComplaintClassification(BaseModel):
    complaint_type: str = Field(description="lateness | attitude | quality | other")
    confidence: float = Field(ge=0, le=1)
    rationale: str = ""


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str

    def interpret(self, text: str, catalog: list[dict[str, Any]], context: dict[str, Any]) -> InterpretedRequest: ...

    def classify_complaint(self, text: str) -> ComplaintClassification: ...
