"""Application settings (env) and business policy (config/policy.yaml)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_env_file() -> str | None:
    for candidate in (REPO_ROOT / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            return str(candidate)
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_find_env_file(), extra="ignore")

    app_mode: str = "demo"
    app_timezone: str = "Asia/Singapore"
    repair_catalog_path: str = "data/reference/repair_object_problem_database.csv"
    database_url: str = "sqlite:///./data/app.db"
    policy_path: str = "config/policy.yaml"

    llm_mode: str = "mock"  # mock | real
    llm_provider: str = "anthropic"  # anthropic | openai_compat (any /v1 chat-completions endpoint)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0

    route_mode: str = "fixture"  # fixture | osrm | estimated
    osrm_base_url: str = ""
    route_timeout_seconds: float = 8.0
    # OSRM returns free-flow driving time (no live traffic). Engineering defaults, documented in docs/decisions.md:
    # travel = ceil(osrm_seconds/60 * factor) + base (parking / building access). Set factor=1, base=0 for raw OSRM.
    osrm_duration_factor: float = 1.25
    osrm_base_minutes: int = 3
    route_cache_dir: str = "data/cache"

    # Geocoding (free-text address → coordinates): auto = OneMap when credentials exist, else Nominatim; onemap | nominatim | none
    geocode_mode: str = "auto"
    onemap_base_url: str = "https://www.onemap.gov.sg"
    onemap_token: str = ""
    onemap_email: str = ""
    onemap_password: str = ""
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_user_agent: str = "techsched-demo/0.2 (hackathon prototype)"
    geocode_timeout_seconds: float = 8.0

    risk_scan_interval_seconds: int = 60
    clock_run_sim_minutes_per_second: int = 1
    background_loop_enabled: bool = True

    # Fixed scenario day (interpreted in app_timezone). Engineering default.
    scenario_date: str = "2026-09-15"

    def resolve_path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else REPO_ROOT / path

    @property
    def catalog_path(self) -> Path:
        return self.resolve_path(self.repair_catalog_path)

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///./"):
            rel = url[len("sqlite:///./"):]
            return f"sqlite:///{(REPO_ROOT / rel).as_posix()}"
        return url

    def public_dict(self) -> dict[str, Any]:
        """Health output – never leaks secrets."""
        return {
            "app_mode": self.app_mode,
            "timezone": self.app_timezone,
            "llm_mode": self.llm_mode,
            "llm_provider": self.llm_provider if self.llm_mode == "real" else None,
            "llm_model": self.llm_model if self.llm_mode == "real" else None,
            "llm_base_url": self.llm_base_url if self.llm_mode == "real" else None,
            "llm_configured": bool(self.llm_api_key),
            "route_mode": self.route_mode,
            "osrm_base_url": self.osrm_base_url if self.route_mode == "osrm" else None,
            "osrm_duration_factor": self.osrm_duration_factor if self.route_mode == "osrm" else None,
            "osrm_base_minutes": self.osrm_base_minutes if self.route_mode == "osrm" else None,
            "geocode_mode": self.geocode_mode,
            "onemap_configured": bool(self.onemap_token or (self.onemap_email and self.onemap_password)),
            "catalog_path": str(self.repair_catalog_path),
        }


class RescheduleRule(BaseModel):
    max_affected: int | None = 0  # None = no cap (policy.yaml: `unlimited` or null)
    movable_priorities: list[str] = Field(default_factory=list)
    allow_zero_disruption_repair_if_unassigned: bool = False
    require_review_if_within_limit: bool = False
    require_review_if_affected: bool = False

    @field_validator("max_affected", mode="before")
    @classmethod
    def _unlimited(cls, v: Any) -> Any:
        return None if isinstance(v, str) and v.strip().lower() in ("unlimited", "none", "inf") else v

    @property
    def allows_moves(self) -> bool:
        return self.max_affected is None or self.max_affected > 0

    def within_limit(self, affected: int) -> bool:
        return self.max_affected is None or affected <= self.max_affected

    @property
    def limit_text(self) -> str:
        return "no limit" if self.max_affected is None else str(self.max_affected)


class ScoringWeights(BaseModel):
    skill_fit: float = 0.30
    travel: float = 0.25
    response: float = 0.20
    workload: float = 0.10
    stability: float = 0.15


class ScoringScales(BaseModel):
    travel_full_penalty_minutes: int = 60
    response_full_penalty_minutes: int = 120
    shift_full_penalty_minutes: int = 60
    affected_reference_count: int = 5


class BreakPolicy(BaseModel):
    evaluate_after_work_minutes: int = 180
    lookahead_minutes: int = 90
    escalate_after_work_minutes: int = 240
    default_break_minutes: int = 30
    slot_step_minutes: int = 15
    pre_evaluate_minutes: int = 60


class AgentBudget(BaseModel):
    max_tool_calls_per_wakeup: int = 12
    max_plan_searches_per_wakeup: int = 3
    transient_retries: int = 2


class DurationPredictionPolicy(BaseModel):
    mode: str = "shadow"
    min_samples_per_problem: int = 5
    history_window_days: int = 90


class Policy(BaseModel):
    policy_version: str = "dev"
    auto_score_threshold: float = 70.0
    threshold_operator: str = ">"
    normal_base: str = "P3"
    paid_base: str = "P1"
    paid_now_base: str = "P0"
    expedite_now_window_minutes: int = 180
    paid_can_escalate_to_p0: bool = True
    reschedule: dict[str, RescheduleRule]
    locked_statuses: list[str] = ["EN_ROUTE", "ARRIVED", "IN_PROGRESS"]
    depart_grace_minutes: int = 0
    auto_release_on_unavailable: list[str] = ["EN_ROUTE"]
    cancel_before_departure_only: bool = True
    cancellation_p0_lt: int = 30
    cancellation_p1_inclusive: tuple[int, int] = (30, 120)
    cancellation_p2_gt: int = 120
    approaching_p2_inclusive: int = 30
    scan_interval_seconds: int = 60
    standby_max: int = 3
    weights: ScoringWeights = ScoringWeights()
    scales: ScoringScales = ScoringScales()
    initial_budget_ms: int = 3000
    repair_budget_ms: int = 5000
    max_relocate_candidates: int = 40
    candidate_expiry_sim_minutes: int = 5
    breaks: BreakPolicy = BreakPolicy()
    agent: AgentBudget = AgentBudget()
    duration_prediction: DurationPredictionPolicy = DurationPredictionPolicy()

    def rule(self, priority: str) -> RescheduleRule:
        return self.reschedule.get(priority, RescheduleRule())

    def passes_threshold(self, score: float | None) -> bool:
        if score is None:
            return False
        if self.threshold_operator == ">=":
            return score >= self.auto_score_threshold
        return score > self.auto_score_threshold


def load_policy(path: Path) -> Policy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    d = raw.get("dispatch", {})
    p = raw.get("priority", {})
    r = raw.get("risk", {})
    s = raw.get("scoring", {})
    so = raw.get("solver", {})
    return Policy(
        policy_version=str(raw.get("policy_version", "dev")),
        auto_score_threshold=float(d.get("auto_score_threshold", 70)),
        threshold_operator=str(d.get("threshold_operator", ">")),
        normal_base=p.get("normal_base", "P3"),
        paid_base=p.get("paid_base", "P1"),
        paid_now_base=str(p.get("paid_now_base", "P0")),
        expedite_now_window_minutes=int(p.get("expedite_now_window_minutes", 180)),
        paid_can_escalate_to_p0=bool(p.get("paid_can_escalate_to_p0", True)),
        reschedule={k: RescheduleRule(**v) for k, v in (raw.get("reschedule") or {}).items()},
        locked_statuses=list((raw.get("execution") or {}).get("locked_statuses", ["EN_ROUTE", "ARRIVED", "IN_PROGRESS"])),
        depart_grace_minutes=int((raw.get("execution") or {}).get("depart_grace_minutes", 0)),
        auto_release_on_unavailable=list((raw.get("execution") or {}).get("auto_release_on_unavailable", ["EN_ROUTE"])),
        cancel_before_departure_only=bool((raw.get("customer_cancel") or {}).get("allowed_before_departure_only", True)),
        cancellation_p0_lt=int(r.get("cancellation_p0_remaining_minutes_less_than", 30)),
        cancellation_p1_inclusive=tuple(r.get("cancellation_p1_remaining_minutes_inclusive", [30, 120])),  # type: ignore[arg-type]
        cancellation_p2_gt=int(r.get("cancellation_p2_remaining_minutes_greater_than", 120)),
        approaching_p2_inclusive=int(r.get("approaching_deadline_p2_minutes_inclusive", 30)),
        scan_interval_seconds=int(r.get("scan_interval_seconds", 60)),
        standby_max=int((raw.get("standby") or {}).get("max_candidates", 3)),
        weights=ScoringWeights(**(s.get("weights") or {})),
        scales=ScoringScales(**(s.get("scales") or {})),
        initial_budget_ms=int(so.get("initial_budget_ms", 3000)),
        repair_budget_ms=int(so.get("repair_budget_ms", 5000)),
        max_relocate_candidates=int(so.get("max_relocate_candidates", 40)),
        candidate_expiry_sim_minutes=int((raw.get("candidate") or {}).get("expiry_sim_minutes", 5)),
        breaks=BreakPolicy(**(raw.get("breaks") or {})),
        agent=AgentBudget(**(raw.get("agent") or {})),
        duration_prediction=DurationPredictionPolicy(**(raw.get("duration_prediction") or {})),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_policy() -> Policy:
    settings = get_settings()
    return load_policy(settings.resolve_path(settings.policy_path))


def reset_config_cache() -> None:
    get_settings.cache_clear()
    get_policy.cache_clear()
    os.environ.pop("_TECHSCHED_CONFIG_RESET", None)
