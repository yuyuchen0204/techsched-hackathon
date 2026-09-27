"""FixtureRouteProvider: fixed locations + fixed asymmetric minute matrix loaded from data/fixtures/route_matrix.json.

Guarantees deterministic offline tests and demos. Geometry is a straight line marked schematic.
Pairs missing from the file are unreachable (None) — never zero.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.providers.route.base import LocationPoint, RouteGeometry, TravelMatrix, matrix_snapshot_id


class FixtureRouteProvider:
    name = "fixture"

    def __init__(self, matrix_path: Path):
        self.matrix_path = matrix_path
        raw = json.loads(matrix_path.read_text(encoding="utf-8"))
        self.version: str = raw.get("version", "unknown")
        self._minutes: dict[tuple[str, str], int | None] = {}
        for src, row in raw["minutes"].items():
            for dst, val in row.items():
                self._minutes[(src, dst)] = None if val is None else int(val)

    def travel_matrix(self, locations: list[LocationPoint]) -> TravelMatrix:
        ids = [loc.id for loc in locations]
        m = TravelMatrix(provider=self.name, snapshot_id=matrix_snapshot_id(self.name, sorted(ids), {"v": self.version}))
        for a in ids:
            for b in ids:
                if a == b:
                    m.minutes[(a, b)] = 0
                else:
                    m.minutes[(a, b)] = self._minutes.get((a, b))  # missing -> None (unreachable)
        return m

    def geometry(self, a: LocationPoint, b: LocationPoint) -> RouteGeometry:
        return RouteGeometry(points=[(a.lat, a.lon), (b.lat, b.lon)], schematic=True, provider=self.name)
