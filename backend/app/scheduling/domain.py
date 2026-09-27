"""Pure in-memory scheduling domain. All times are integer minutes from the scheduling day origin."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.models.enums import Priority
from app.providers.route.base import TravelMatrix

Interval = tuple[int, int]  # [start, end)


def overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


@dataclass(frozen=True)
class OrderSpec:
    id: str
    trade_type: str
    required_level: int
    duration: int
    window_start: int
    window_end: int
    location_id: str
    priority: Priority
    lifecycle_status: str
    version: int
    paid_expedite: bool = False
    recovery_target: bool = False  # overdue target: window constraint replaced by start >= now
    customer_ref: str = ""
    excluded_technicians: tuple[str, ...] = ()  # explicit customer refusal for THIS order (hard filter)

    @property
    def departed(self) -> bool:
        return self.lifecycle_status in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS")

    @property
    def closed(self) -> bool:
        return self.lifecycle_status in ("COMPLETED", "CANCELLED")

    @property
    def movable(self) -> bool:
        return not self.departed and not self.closed


@dataclass(frozen=True)
class TechSpec:
    id: str
    name: str
    skills: dict[str, int]
    shift_start: int
    shift_end: int
    breaks: tuple[Interval, ...]
    unavailable: tuple[Interval, ...]
    home_location_id: str
    current_location_id: str
    status: str
    version: int
    actual_worked_minutes: int = 0  # minutes already spent (travel+service) on completed/ongoing tasks

    def qualified(self, trade_type: str, level: int) -> bool:
        return self.skills.get(trade_type, 0) >= level

    @property
    def blocked(self) -> tuple[Interval, ...]:
        return self.breaks + self.unavailable


@dataclass(frozen=True)
class Assign:
    order_id: str
    tech_id: str
    origin_location_id: str
    departure: int
    arrival: int
    service_start: int
    service_end: int
    travel: int
    waiting: int
    locked: bool = False
    match_score: float | None = None
    score_components: dict[str, Any] = field(default_factory=dict)

    def signature(self) -> tuple[str, str, int]:
        return (self.order_id, self.tech_id, self.service_start)

    def with_score(self, score: float, components: dict[str, Any]) -> Assign:
        return replace(self, match_score=score, score_components=components)


@dataclass
class Snapshot:
    now: int
    orders: dict[str, OrderSpec]
    techs: dict[str, TechSpec]
    assignments: dict[str, Assign]  # order_id -> ACTIVE assignment (committed schedule)
    matrix: TravelMatrix
    schedule_version: int
    scenario_generation: int
    location_names: dict[str, str] = field(default_factory=dict)
    preference_penalties: dict[str, dict[str, float]] = field(default_factory=dict)  # order_id -> {tech_id: penalty 0..1} (soft)

    def route_of(self, tech_id: str, assignments: dict[str, Assign] | None = None) -> list[Assign]:
        source = self.assignments if assignments is None else assignments
        return sorted((a for a in source.values() if a.tech_id == tech_id), key=lambda a: (a.departure, a.service_start))

    def locked_prefix(self, tech_id: str) -> list[Assign]:
        return [a for a in self.route_of(tech_id) if a.locked]

    def movable_route(self, tech_id: str, assignments: dict[str, Assign] | None = None) -> list[Assign]:
        return [a for a in self.route_of(tech_id, assignments) if not a.locked]

    def anchor(self, tech_id: str) -> tuple[str, int]:
        """(location, earliest time) from which the tech's next MOVABLE task can depart."""
        tech = self.techs[tech_id]
        locked = self.locked_prefix(tech_id)
        if locked:
            last = locked[-1]
            return self.orders[last.order_id].location_id, max(last.service_end, self.now)
        return tech.current_location_id, max(self.now, tech.shift_start)

    def travel(self, a: str, b: str) -> int | None:
        return self.matrix.travel(a, b)

    def data_version(self) -> dict[str, int]:
        v: dict[str, int] = {}
        for o in self.orders.values():
            v[f"order:{o.id}"] = o.version
        for t in self.techs.values():
            v[f"tech:{t.id}"] = t.version
        return v
