"""Geocoding contract: free-text address → candidate coordinates. Coordinates only ever come from a provider."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


def normalize_address(query: str) -> str:
    """Customer shorthand → what geocoders accept (verified against OneMap 2026-09-16):
    'Blk 210A', 'Block 210A', 'B210A', 'blk210a' → '210A'; unit numbers '#05-123' dropped; 'Singapore' dropped."""
    q = re.sub(r"#\s*\d{1,3}\s*-\s*\d{1,5}[a-z]?", " ", query, flags=re.IGNORECASE)  # unit number
    q = re.sub(r"\b(blk|block|blok)\.?\s*(?=\d)", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"\bb(?=\d{1,4}[a-z]?\b)", " ", q, flags=re.IGNORECASE)  # 'B210A' shorthand
    q = re.sub(r"\b(singapore|s'pore|sg)\b", " ", q, flags=re.IGNORECASE)
    q = re.sub(r"[,;]+", " ", q)
    return re.sub(r"\s+", " ", q).strip()


@dataclass
class GeocodeResult:
    name: str          # short label (building / search value)
    address: str       # full display address
    lat: float
    lon: float
    source: str        # onemap | nominatim
    postal: str | None = None
    kind: str | None = None
    score: float = 0.0  # provider relevance, higher = better (not comparable across providers)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "address": self.address, "lat": self.lat, "lon": self.lon, "source": self.source,
                "postal": self.postal, "kind": self.kind, "score": self.score}


@dataclass
class GeocodeOutcome:
    query: str
    results: list[GeocodeResult] = field(default_factory=list)
    provider: str | None = None
    degraded: bool = False
    reason: str | None = None
    cached: bool = False


class GeocodeError(RuntimeError):
    pass


class GeocodeProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 5) -> list[GeocodeResult]: ...

    def reverse(self, lat: float, lon: float) -> GeocodeResult | None:
        """Coordinates → the nearest real address. A map pin must still become a street name, block and postal code:
        a technician cannot navigate to "1.31731, 103.84552"."""
        ...
