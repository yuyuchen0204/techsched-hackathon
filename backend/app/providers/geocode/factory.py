from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.providers.geocode.base import (
    GeocodeError,
    GeocodeOutcome,
    GeocodeProvider,
    GeocodeResult,
    normalize_address,
)
from app.providers.geocode.nominatim import NominatimGeocoder
from app.providers.geocode.onemap import OneMapGeocoder

log = logging.getLogger(__name__)
_memory: dict[str, list[GeocodeResult]] = {}


def normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", normalize_address(q or "").lower()).strip()


@lru_cache(maxsize=1)
def providers() -> list[GeocodeProvider]:
    """Ordered chain. auto = OneMap when credentials exist, then Nominatim; explicit modes use one provider only."""
    s = get_settings()
    chain: list[GeocodeProvider] = []
    if s.geocode_mode == "none":
        return chain
    if s.geocode_mode in ("onemap", "auto"):
        try:
            chain.append(OneMapGeocoder(s.onemap_base_url, s.onemap_token, s.onemap_email, s.onemap_password, s.geocode_timeout_seconds))
        except GeocodeError as exc:
            if s.geocode_mode == "onemap":
                log.warning("OneMap requested but not configured: %s", exc)
    if s.geocode_mode in ("nominatim", "auto"):
        chain.append(NominatimGeocoder(s.nominatim_base_url, s.nominatim_user_agent, s.geocode_timeout_seconds))
    return chain


def enabled() -> bool:
    return bool(providers())


def _cache_file() -> Path:
    d = get_settings().resolve_path(get_settings().route_cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / "geocode.json"


def _load_disk() -> dict[str, list[dict]]:
    p = _cache_file()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


def geocode(query: str, limit: int = 5) -> GeocodeOutcome:
    """Free text → candidates via the provider chain, cached in memory and on disk (never re-queries a known string)."""
    key = normalize_query(query)
    out = GeocodeOutcome(query=query)
    if not key:
        return out
    if key in _memory:
        out.results, out.cached, out.provider = _memory[key], True, _memory[key][0].source if _memory[key] else None
        return out
    disk = _load_disk()
    if key in disk:
        results = [GeocodeResult(**r) for r in disk[key]]
        _memory[key] = results
        out.results, out.cached, out.provider = results, True, results[0].source if results else None
        return out
    errors: list[str] = []
    empty: list[str] = []
    cleaned = normalize_address(query) or query
    for p in providers():
        try:
            results = p.search(cleaned, limit)
        except GeocodeError as exc:
            errors.append(f"{p.name}: {exc}")
            continue
        if not results:
            empty.append(p.name)  # answered but found nothing → ask the next provider before giving up
            if out.provider is None:
                out.provider = p.name
            continue
        out.provider = p.name
        out.results = results
        break
    if errors:
        out.degraded, out.reason = True, "; ".join(errors)
    if out.provider is None:
        out.degraded, out.reason = True, "; ".join(errors) or "no geocoding provider configured"
        return out
    if not out.results and empty:
        out.reason = (out.reason + "; " if out.reason else "") + f"no match from {', '.join(empty)}"
    _memory[key] = out.results
    disk[key] = [r.as_dict() for r in out.results]
    try:
        _cache_file().write_text(json.dumps(disk, ensure_ascii=False))
    except OSError as exc:
        log.warning("geocode cache not written: %s", exc)
    return out


def reverse_geocode(lat: float, lon: float) -> GeocodeOutcome:
    """Coordinates → the nearest real address, through the same provider chain and the same disk cache.

    A dropped map pin used to travel through the system as its own coordinates ("Map pin 1.31731, 103.84552"), which
    the technician then saw instead of a street. Rounded to ~11 m for the cache key so re-pinning the same spot is free.
    """
    key = f"rev:{lat:.4f},{lon:.4f}"
    out = GeocodeOutcome(query=key)
    if key in _memory:
        out.results, out.cached, out.provider = _memory[key], True, _memory[key][0].source if _memory[key] else None
        return out
    disk = _load_disk()
    if key in disk:
        results = [GeocodeResult(**r) for r in disk[key]]
        _memory[key] = results
        out.results, out.cached, out.provider = results, True, results[0].source if results else None
        return out
    errors: list[str] = []
    for p in providers():
        try:
            hit = p.reverse(lat, lon)
        except GeocodeError as exc:
            errors.append(f"{p.name}: {exc}")
            continue
        out.provider = p.name
        if hit is not None:
            out.results = [hit]
            break
    if errors:
        out.degraded, out.reason = True, "; ".join(errors)
    if out.provider is None:
        out.degraded, out.reason = True, "; ".join(errors) or "no geocoding provider configured"
        return out
    _memory[key] = out.results
    disk[key] = [r.as_dict() for r in out.results]
    try:
        _cache_file().write_text(json.dumps(disk, ensure_ascii=False))
    except OSError as exc:
        log.warning("geocode cache not written: %s", exc)
    return out


def status() -> dict:
    s = get_settings()
    return {"mode": s.geocode_mode, "providers": [p.name for p in providers()],
            "onemap_configured": bool(s.onemap_token or (s.onemap_email and s.onemap_password))}
