from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.config import REPO_ROOT, get_settings
from app.providers.route.base import (
    LocationPoint,
    RouteGeometry,
    RouteProvider,
    TravelMatrix,
    matrix_snapshot_id,
)
from app.providers.route.estimated import EstimatedRouteProvider
from app.providers.route.fixture import FixtureRouteProvider
from app.providers.route.osrm import OSRMRouteProvider, RouteProviderError

log = logging.getLogger(__name__)
FIXTURE_MATRIX_PATH = REPO_ROOT / "data" / "fixtures" / "route_matrix.json"


@lru_cache(maxsize=1)
def fixture_provider() -> FixtureRouteProvider:
    return FixtureRouteProvider(FIXTURE_MATRIX_PATH)


@lru_cache(maxsize=1)
def estimated_provider() -> EstimatedRouteProvider:
    return EstimatedRouteProvider()


@lru_cache(maxsize=1)
def _osrm_provider() -> OSRMRouteProvider:
    s = get_settings()
    return OSRMRouteProvider(s.osrm_base_url, timeout=s.route_timeout_seconds, duration_factor=s.osrm_duration_factor,
                             base_minutes=s.osrm_base_minutes)


def build_provider() -> RouteProvider:
    s = get_settings()
    if s.route_mode == "osrm":
        return _osrm_provider()
    if s.route_mode == "estimated":
        return estimated_provider()
    return fixture_provider()


def supports_arbitrary_points() -> bool:
    """fixture can only price preset locations; osrm/estimated accept any coordinate."""
    return get_settings().route_mode in ("osrm", "estimated")


def _cache_path(snapshot_id: str) -> Path:
    d = get_settings().resolve_path(get_settings().route_cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"matrix_{snapshot_id.replace(':', '_')}.json"


def _load_cached(snapshot_id: str) -> TravelMatrix | None:
    p = _cache_path(snapshot_id)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
        m = TravelMatrix(provider=raw["provider"], snapshot_id=raw["snapshot_id"])
        for k, v in raw["minutes"].items():
            a, b = k.split("|")
            m.minutes[(a, b)] = v
        for k, v in raw.get("distance_km", {}).items():
            a, b = k.split("|")
            m.distance_km[(a, b)] = v
        return m
    except Exception:  # noqa: BLE001
        return None


def _save_cached(m: TravelMatrix) -> None:
    p = _cache_path(m.snapshot_id)
    p.write_text(json.dumps({"provider": m.provider, "snapshot_id": m.snapshot_id,
                             "minutes": {f"{a}|{b}": v for (a, b), v in m.minutes.items()},
                             "distance_km": {f"{a}|{b}": v for (a, b), v in m.distance_km.items()}}))


def compute_matrix(locations: list[LocationPoint]) -> TravelMatrix:
    """Whole-matrix computation with explicit degradation. Real-provider results are cached on disk by snapshot id
    (location set + provider config), so restarts/resets do not re-query the routing service."""
    provider = build_provider()
    s = get_settings()
    if provider.name == "osrm":
        cfg = {"base": s.osrm_base_url.rstrip("/"), "factor": s.osrm_duration_factor, "base_min": s.osrm_base_minutes, "profile": "driving"}
        cached = _load_cached(matrix_snapshot_id("osrm", sorted(l.id for l in locations), cfg))
        if cached is not None:
            return cached
    try:
        m = provider.travel_matrix(locations)
        if provider.name == "osrm":
            _save_cached(m)
        return m
    except RouteProviderError as exc:
        log.warning("route provider %s failed (%s); degrading whole matrix to estimated", provider.name, exc)
        m = estimated_provider().travel_matrix(locations)
        m.degraded = True
        m.degraded_reason = f"{provider.name} failed: {exc}; using haversine estimate (not a road network)"
        return m


def leg_geometry(a: LocationPoint, b: LocationPoint) -> RouteGeometry:
    """Route geometry for display; degrades to a straight schematic line."""
    provider = build_provider()
    try:
        return provider.geometry(a, b)
    except RouteProviderError as exc:
        g = fixture_provider().geometry(a, b)
        g.provider = f"schematic ({provider.name} failed: {exc})"
        return g
