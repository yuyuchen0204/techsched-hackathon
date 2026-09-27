"""RouteProvider abstraction. All travel values are integer minutes; None = unreachable/missing (never 0)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LocationPoint:
    id: str
    name: str
    lat: float
    lon: float


@dataclass
class TravelMatrix:
    provider: str
    snapshot_id: str
    minutes: dict[tuple[str, str], int | None] = field(default_factory=dict)
    distance_km: dict[tuple[str, str], float | None] = field(default_factory=dict)  # optional, for display only
    degraded: bool = False
    degraded_reason: str | None = None

    def travel(self, a: str, b: str) -> int | None:
        if a == b:
            return 0
        return self.minutes.get((a, b))


@dataclass
class RouteGeometry:
    points: list[tuple[float, float]]  # (lat, lon)
    schematic: bool
    provider: str
    minutes: float | None = None  # provider-reported duration (before any factor), None for schematic lines
    distance_km: float | None = None


def matrix_snapshot_id(provider: str, location_ids: list[str], config: dict | None = None) -> str:
    payload = json.dumps({"p": provider, "ids": list(location_ids), "cfg": config or {}}, sort_keys=True)
    return f"{provider}:{hashlib.sha1(payload.encode()).hexdigest()[:12]}"


class RouteProvider(Protocol):
    name: str

    def travel_matrix(self, locations: list[LocationPoint]) -> TravelMatrix: ...

    def geometry(self, a: LocationPoint, b: LocationPoint) -> RouteGeometry: ...
