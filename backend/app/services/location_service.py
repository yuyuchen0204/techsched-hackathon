"""Customer location handling: preset areas, or exact map-picked points when the route provider can price them."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Location
from app.providers.geocode.factory import reverse_geocode
from app.providers.route.base import LocationPoint
from app.providers.route.estimated import haversine_km
from app.providers.route.factory import supports_arbitrary_points
from app.services.order_service import OrderError

# Singapore bounding box (synthetic demo geography); anything outside is refused rather than guessed
log = logging.getLogger(__name__)

LAT_RANGE = (1.15, 1.48)
LON_RANGE = (103.60, 104.10)


def presets(db: Session) -> list[Location]:
    return list(db.scalars(select(Location).where(Location.area != "custom").order_by(Location.name)).all())


def nearest_preset(db: Session, lat: float, lon: float) -> tuple[Location, float]:
    here = LocationPoint("here", "here", lat, lon)
    best: tuple[Location, float] | None = None
    for loc in presets(db):
        d = haversine_km(here, LocationPoint(loc.id, loc.name, loc.lat, loc.lon))
        if best is None or d < best[1]:
            best = (loc, d)
    if best is None:
        raise OrderError("no_locations", "no preset locations loaded", status=500)
    return best


def _name_for_point(lat: float, lon: float, given: str | None) -> tuple[str, str | None, str | None]:
    """A pin must become an address a technician can navigate to, never bare coordinates.

    Returns (name, postal, full address). The caller's `given` label wins only when it is a real name — the customer
    app used to send back "Map pin 1.31731, 103.84552", which is exactly what this has to replace.
    """
    label = (given or "").strip()
    looks_like_coordinates = bool(re.fullmatch(r"(map pin|pinned point)?\s*\(?-?\d+\.\d+\s*,\s*-?\d+\.\d+\)?", label, re.IGNORECASE))
    if label and not looks_like_coordinates:
        return label[:120], None, None
    try:
        outcome = reverse_geocode(lat, lon)
    except Exception as exc:  # noqa: BLE001 — a pin must stay usable even when the provider is down
        log.warning("reverse geocode failed for %.5f,%.5f: %s", lat, lon, exc)
        outcome = None
    hit = outcome.results[0] if outcome and outcome.results else None
    if hit is None:
        return f"Pinned point ({lat:.4f}, {lon:.4f})", None, None
    name = hit.name if hit.name else hit.address
    if hit.postal:
        name = f"{name} S{hit.postal}"
    return name[:120], hit.postal, hit.address


def resolve_point(db: Session, lat: float, lon: float, name: str | None = None) -> dict[str, Any]:
    """Return {location, snapped, note}. Exact point in osrm/estimated mode; nearest preset in fixture mode."""
    if not (LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LON_RANGE[0] <= lon <= LON_RANGE[1]):
        raise OrderError("outside_service_area", "the selected point is outside the Singapore service area",
                         {"lat": lat, "lon": lon})
    if not supports_arbitrary_points():
        loc, km = nearest_preset(db, lat, lon)
        return {"location": _view(loc), "snapped": True,
                "note": f"route mode 'fixture' prices preset areas only — snapped to {loc.name} ({km:.1f} km away)"}
    pid = "pt_" + hashlib.sha1(f"{lat:.5f},{lon:.5f}".encode()).hexdigest()[:10]
    existing = db.get(Location, pid)
    resolved_name, postal, full = _name_for_point(lat, lon, name)
    if existing is None:
        existing = Location(id=pid, name=resolved_name, lat=round(lat, 5), lon=round(lon, 5), area="custom")
        db.add(existing)
        db.flush()
    elif existing.name.lower().startswith(("map pin", "pinned point")) and not resolved_name.lower().startswith(("map pin", "pinned point")):
        existing.name = resolved_name  # an older pin stored as coordinates is upgraded once a provider can name it
        db.flush()
    out = {"location": _view(existing), "snapped": False,
           "note": "exact point; travel times come from the road-network provider"}
    if postal:
        out["postal_code"] = postal
    if full:
        out["formatted_address"] = full
    return out


def _view(loc: Location) -> dict[str, Any]:
    return {"id": loc.id, "name": loc.name, "lat": loc.lat, "lon": loc.lon, "area": loc.area}
