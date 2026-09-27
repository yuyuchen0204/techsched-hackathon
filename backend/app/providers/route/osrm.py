"""OSRM adapter (Table + Route services) — real road-network travel times and geometry.

Contract (OSRM HTTP API v1, verified live against https://router.project-osrm.org on 2026-09-15):
  GET {base}/table/v1/driving/{lon,lat;lon,lat;...}?annotations=duration,distance
    -> {"code":"Ok","durations":[[sec,...],...],"distances":[[m,...],...]}   (null = unreachable)
  GET {base}/route/v1/driving/{lon,lat;lon,lat}?overview=full&geometries=geojson
    -> {"code":"Ok","routes":[{"duration":sec,"distance":m,"geometry":{"coordinates":[[lon,lat],...]}}]}
Coordinates are LON,LAT in the URL. OSRM reports free-flow driving time (no live traffic); the adapter applies a
configurable factor and a fixed access allowance so the result is an honest estimate, not a live ETA.
Unreachable pairs stay None (never 0). Any failure raises RouteProviderError so the caller degrades the WHOLE matrix.
"""
from __future__ import annotations

import math
from typing import Any

import httpx

from app.providers.route.base import LocationPoint, RouteGeometry, TravelMatrix, matrix_snapshot_id

MAX_TABLE_POINTS = 100  # public demo server limit; self-hosted servers can raise --max-table-size


class RouteProviderError(RuntimeError):
    pass


class OSRMRouteProvider:
    name = "osrm"

    def __init__(self, base_url: str, timeout: float = 8.0, retries: int = 1, client: httpx.Client | None = None,
                 duration_factor: float = 1.0, base_minutes: int = 0, profile: str = "driving"):
        if not base_url:
            raise RouteProviderError("OSRM_BASE_URL is not configured")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.duration_factor = duration_factor
        self.base_minutes = base_minutes
        self.profile = profile
        self._client = client
        self._geometry_cache: dict[tuple[str, str], RouteGeometry] = {}

    def _get(self, url: str) -> dict[str, Any]:
        last_err: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                client = self._client or httpx.Client(timeout=self.timeout, headers={"User-Agent": "techsched-demo/0.2"})
                try:
                    resp = client.get(url)
                finally:
                    if self._client is None:
                        client.close()
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != "Ok":
                    raise RouteProviderError(f"OSRM returned code={data.get('code')} ({data.get('message', '')})")
                return data
            except Exception as exc:  # noqa: BLE001
                last_err = exc
        raise RouteProviderError(f"OSRM request failed: {last_err}")

    def minutes_from_seconds(self, sec: float) -> int:
        return math.ceil(sec / 60.0 * self.duration_factor) + self.base_minutes

    def travel_matrix(self, locations: list[LocationPoint]) -> TravelMatrix:
        locs = sorted(locations, key=lambda l: l.id)
        if len(locs) > MAX_TABLE_POINTS:
            raise RouteProviderError(f"{len(locs)} points exceed the OSRM table limit of {MAX_TABLE_POINTS}")
        coords = ";".join(f"{l.lon:.6f},{l.lat:.6f}" for l in locs)
        data = self._get(f"{self.base_url}/table/v1/{self.profile}/{coords}?annotations=duration,distance")
        durations = data.get("durations")
        distances = data.get("distances") or [[None] * len(locs) for _ in locs]
        if not isinstance(durations, list) or len(durations) != len(locs):
            raise RouteProviderError("OSRM table response malformed")
        cfg = {"base": self.base_url, "factor": self.duration_factor, "base_min": self.base_minutes, "profile": self.profile}
        m = TravelMatrix(provider=self.name, snapshot_id=matrix_snapshot_id(self.name, [l.id for l in locs], cfg))
        for i, a in enumerate(locs):
            row = durations[i]
            if not isinstance(row, list) or len(row) != len(locs):
                raise RouteProviderError("OSRM table row malformed")
            for j, b in enumerate(locs):
                sec = row[j]
                dist = distances[i][j] if isinstance(distances[i], list) else None
                if a.id == b.id:
                    m.minutes[(a.id, b.id)] = 0
                    m.distance_km[(a.id, b.id)] = 0.0
                elif sec is None:
                    m.minutes[(a.id, b.id)] = None  # unreachable stays None, never 0
                    m.distance_km[(a.id, b.id)] = None
                else:
                    m.minutes[(a.id, b.id)] = self.minutes_from_seconds(float(sec))
                    m.distance_km[(a.id, b.id)] = round(float(dist) / 1000.0, 2) if dist is not None else None
        return m

    def geometry(self, a: LocationPoint, b: LocationPoint) -> RouteGeometry:
        key = (a.id, b.id)
        if key in self._geometry_cache:
            return self._geometry_cache[key]
        data = self._get(f"{self.base_url}/route/v1/{self.profile}/{a.lon:.6f},{a.lat:.6f};{b.lon:.6f},{b.lat:.6f}"
                         "?overview=full&geometries=geojson")
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]
        g = RouteGeometry(points=[(float(c[1]), float(c[0])) for c in coords], schematic=False, provider=self.name,
                          minutes=round(float(route.get("duration", 0)) / 60.0, 1), distance_km=round(float(route.get("distance", 0)) / 1000.0, 2))
        self._geometry_cache[key] = g
        return g
