"""OneMap (Singapore Land Authority) search API — authoritative for Singapore postal codes and HDB blocks.

Contract (OneMap API docs, checked 2026-09-15; the endpoint now requires an API token):
  GET https://www.onemap.gov.sg/api/common/elastic/search?searchVal=<q>&returnGeom=Y&getAddrDetails=Y&pageNum=1
      header Authorization: Bearer <token>
  -> {"found":N,"results":[{"SEARCHVAL","BLK_NO","ROAD_NAME","BUILDING","ADDRESS","POSTAL","LATITUDE","LONGITUDE",...}]}
Token: either ONEMAP_TOKEN (from the OneMap account page) or fetched with ONEMAP_EMAIL/ONEMAP_PASSWORD via
  POST https://www.onemap.gov.sg/api/auth/post/getToken {"email","password"} -> {"access_token","expiry_timestamp"} (3 days).
Not verified live here (no OneMap account on the dev machine); response parsing is unit-tested with recorded shapes.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.providers.geocode.base import GeocodeError, GeocodeResult, normalize_address


class OneMapGeocoder:
    name = "onemap"

    def __init__(self, base_url: str = "https://www.onemap.gov.sg", token: str = "", email: str = "", password: str = "",
                 timeout: float = 8.0, client: httpx.Client | None = None):
        if not token and not (email and password):
            raise GeocodeError("OneMap needs ONEMAP_TOKEN or ONEMAP_EMAIL + ONEMAP_PASSWORD")
        self.base_url = base_url.rstrip("/")
        self._static_token = token
        self._email, self._password = email, password
        self.timeout = timeout
        self._client = client
        self._token: str | None = token or None
        self._token_expiry: float = float("inf") if token else 0.0

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=self.timeout, headers={"User-Agent": "techsched-demo/0.2"})

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 600:
            return self._token
        client = self._http()
        try:
            resp = client.post(f"{self.base_url}/api/auth/post/getToken", json={"email": self._email, "password": self._password})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise GeocodeError(f"OneMap token request failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        token = data.get("access_token")
        if not token:
            raise GeocodeError(f"OneMap token response without access_token: {data}")
        self._token = str(token)
        self._token_expiry = float(data.get("expiry_timestamp", time.time() + 3 * 24 * 3600))
        return self._token

    @staticmethod
    def normalize(query: str) -> str:
        """OneMap's search rejects 'Blk/Block/B' prefixes and unit numbers ('#05-123') — verified live 2026-09-16."""
        return normalize_address(query)

    def search(self, query: str, limit: int = 5) -> list[GeocodeResult]:
        token = self._ensure_token()
        client = self._http()
        query = self.normalize(query) or query
        try:
            resp = client.get(f"{self.base_url}/api/common/elastic/search",
                              params={"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
                              headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 401 and not self._static_token:
                self._token = None  # expired → refresh once
                token = self._ensure_token()
                resp = client.get(f"{self.base_url}/api/common/elastic/search",
                                  params={"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
                                  headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except GeocodeError:
            raise
        except Exception as exc:
            raise GeocodeError(f"OneMap search failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        if data.get("error"):
            raise GeocodeError(f"OneMap error: {data['error']}")
        out: list[GeocodeResult] = []
        for r in (data.get("results") or [])[:limit]:
            try:
                lat, lon = float(r["LATITUDE"]), float(r["LONGITUDE"])
            except (KeyError, TypeError, ValueError):
                continue
            building = (r.get("BUILDING") or "").strip()
            name = building if building and building != "NIL" else (r.get("SEARCHVAL") or r.get("ADDRESS") or query)
            postal = (r.get("POSTAL") or "").strip() or None
            out.append(GeocodeResult(name=str(name).title(), address=str(r.get("ADDRESS") or name).title(), lat=lat, lon=lon,
                                     source=self.name, postal=None if postal in (None, "NIL") else postal, kind="address",
                                     score=1.0 - 0.05 * len(out)))
        return out

    def reverse(self, lat: float, lon: float) -> GeocodeResult | None:
        """GET /api/public/revgeocode?location=<lat>,<lon>&buffer=200&addressType=All (OneMap docs, same Bearer token).
        -> {"GeocodeInfo":[{"BUILDINGNAME","BLOCK","ROAD","POSTALCODE","LATITUDE","LONGITUDE"},...]}
        Authoritative for Singapore blocks and postal codes, which is exactly what a map pin is missing."""
        token = self._ensure_token()
        client = self._http()
        params: dict[str, str] = {"location": f"{lat},{lon}", "buffer": "200", "addressType": "All", "otherFeatures": "N"}
        try:
            resp = client.get(f"{self.base_url}/api/public/revgeocode", params=params,
                              headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 401 and not self._static_token:
                self._token = None
                token = self._ensure_token()
                resp = client.get(f"{self.base_url}/api/public/revgeocode", params=params,
                                  headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except GeocodeError:
            raise
        except Exception as exc:
            raise GeocodeError(f"OneMap reverse geocode failed: {exc}") from exc
        finally:
            if self._client is None:
                client.close()
        rows = data.get("GeocodeInfo") or []
        if not rows:
            return None
        r = rows[0]
        building = str(r.get("BUILDINGNAME") or "").strip()
        block, road = str(r.get("BLOCK") or "").strip(), str(r.get("ROAD") or "").strip()
        street = " ".join(x for x in (block, road) if x)
        name = building if building and building.upper() != "NIL" else (street or "Pinned point")
        raw_postal = str(r.get("POSTALCODE") or "").strip()
        postal = None if raw_postal.upper() in ("", "NIL") else raw_postal
        address = ", ".join(x for x in (street, building if building.upper() != "NIL" else "", f"Singapore {postal}" if postal else "") if x)
        return GeocodeResult(name=str(name).title(), address=(address or str(name)).title(), lat=lat, lon=lon,
                             source=self.name, postal=postal, kind="address", score=1.0)
