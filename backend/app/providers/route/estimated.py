"""EstimatedRouteProvider: haversine distance at an assumed urban speed. Explicitly an estimate, not live traffic."""
from __future__ import annotations

import math

from app.providers.route.base import LocationPoint, RouteGeometry, TravelMatrix, matrix_snapshot_id


def haversine_km(a: LocationPoint, b: LocationPoint) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


class EstimatedRouteProvider:
    name = "estimated"

    def __init__(self, speed_kmh: float = 28.0, detour_factor: float = 1.3, base_minutes: int = 4):
        self.speed_kmh = speed_kmh
        self.detour_factor = detour_factor
        self.base_minutes = base_minutes

    def minutes(self, a: LocationPoint, b: LocationPoint) -> int:
        km = haversine_km(a, b) * self.detour_factor
        return self.base_minutes + math.ceil(km / self.speed_kmh * 60)

    def travel_matrix(self, locations: list[LocationPoint]) -> TravelMatrix:
        ids = sorted(loc.id for loc in locations)
        cfg = {"speed": self.speed_kmh, "detour": self.detour_factor, "base": self.base_minutes}
        m = TravelMatrix(provider=self.name, snapshot_id=matrix_snapshot_id(self.name, ids, cfg))
        by_id = {loc.id: loc for loc in locations}
        for a in ids:
            for b in ids:
                m.minutes[(a, b)] = 0 if a == b else self.minutes(by_id[a], by_id[b])
        return m

    def geometry(self, a: LocationPoint, b: LocationPoint) -> RouteGeometry:
        return RouteGeometry(points=[(a.lat, a.lon), (b.lat, b.lon)], schematic=True, provider=self.name)
