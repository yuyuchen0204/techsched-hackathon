"""Nominatim (OpenStreetMap) geocoder — free, no key; usage policy: identify yourself, ≤1 request/second, cache.
Good for streets/landmarks in Singapore, weaker for exact HDB block numbers and postal codes (use OneMap for those).
Verified live on 2026-09-15 for "123 Tampines Street 11" and "Hougang Mall"."""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

from app.providers.geocode.base import GeocodeError, GeocodeResult


class NominatimGeocoder:
    name = "nominatim"
    _lock = threading.Lock()
    _last_call = 0.0

    def __init__(self, base_url: str = "https://nominatim.openstreetmap.org", user_agent: str = "techsched-demo/0.2",
                 timeout: float = 8.0, client: httpx.Client | None = None, country: str = "sg"):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.country = country
        self._client = client

    def search(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        with NominatimGeocoder._lock:  # polite: one request per second across the process
            wait = 1.05 - (time.monotonic() - NominatimGeocoder._last_call)
            if wait > 0 and self._client is None:
                time.sleep(wait)
            NominatimGeocoder._last_call = time.monotonic()
        client = self._client or httpx.Client(timeout=self.timeout, headers={"User-Agent": self.user_agent})
        try:
            resp = client.get(f"{self.base_url}/search", params={"q": query, "format": "jsonv2", "countrycodes": self.country,
                                                                 "limit": limit, "addressdetails": 1})
            resp.raise_for_status()
            data: list[dict[str, Any]] = resp.json()
        except Exception as exc:
            raise GeocodeError(f"Nominatim search failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        out: list[GeocodeResult] = []
        for r in data[:limit]:
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            addr = r.get("address") or {}
            display = str(r.get("display_name") or "")
            short = str(r.get("name") or display.split(",")[0])
            out.append(GeocodeResult(name=short, address=", ".join(display.split(", ")[:4]), lat=lat, lon=lon, source=self.name,
                                     postal=addr.get("postcode"), kind=str(r.get("type") or r.get("category") or ""),
                                     score=float(r.get("importance") or 0.0)))
        return out

    def reverse(self, lat: float, lon: float) -> GeocodeResult | None:
        """GET /reverse?lat&lon&format=jsonv2&addressdetails=1 → the nearest addressable feature."""
        with NominatimGeocoder._lock:
            wait = 1.05 - (time.monotonic() - NominatimGeocoder._last_call)
            if wait > 0 and self._client is None:
                time.sleep(wait)
            NominatimGeocoder._last_call = time.monotonic()
        client = self._client or httpx.Client(timeout=self.timeout, headers={"User-Agent": self.user_agent})
        try:
            resp = client.get(f"{self.base_url}/reverse", params={"lat": lat, "lon": lon, "format": "jsonv2",
                                                                  "addressdetails": 1, "zoom": 18})
            resp.raise_for_status()
            r: dict[str, Any] = resp.json()
        except Exception as exc:
            raise GeocodeError(f"Nominatim reverse failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        if not r or r.get("error"):
            return None
        addr = r.get("address") or {}
        display = str(r.get("display_name") or "")
        house, road = addr.get("house_number"), addr.get("road")
        short = str(r.get("name") or "") or addr.get("building") or addr.get("amenity") or \
            (" ".join(x for x in (house, road) if x) or display.split(",")[0])
        return GeocodeResult(name=str(short), address=", ".join(display.split(", ")[:4]), lat=lat, lon=lon,
                             source=self.name, postal=addr.get("postcode"), kind=str(r.get("type") or ""), score=0.0)
