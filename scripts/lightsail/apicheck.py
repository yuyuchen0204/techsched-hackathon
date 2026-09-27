"""Daily live check of every external API the app depends on (LLM, OneMap, OSRM, Nominatim, OSM tiles).

Run from the repo root or anywhere: backend/.venv/bin/python scripts/lightsail/apicheck.py
Prints one line per API (no secrets) and exits 1 if any check fails. Installed as a daily cron job on the
Lightsail server by server_setup.sh; output is appended to ~/apicheck/apicheck.log.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

import httpx
from app.config import get_settings
from app.providers.geocode.onemap import OneMapGeocoder

UA = {"User-Agent": "techsched-demo/0.2"}
failures = 0


def row(name: str, ok: bool, info: str) -> None:
    global failures
    failures += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {name:<22} {info}")


def main() -> int:
    s = get_settings()
    print(f"== {datetime.now().astimezone().isoformat(timespec='seconds')} ==")
    base = s.llm_base_url.rstrip("/")
    url = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    try:
        t = time.time()
        r = httpx.post(url, headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=40,
                       json={"model": s.llm_model, "messages": [{"role": "user", "content": "Reply with OK"}], "max_tokens": 5})
        row("LLM API", r.status_code == 200, f"HTTP {r.status_code}, {time.time() - t:.1f}s" + ("" if r.status_code == 200 else f", {r.text[:150]}"))
    except Exception as exc:  # noqa: BLE001 - a health check reports any failure
        row("LLM API", False, repr(exc)[:150])
    try:
        g = OneMapGeocoder(s.onemap_base_url, s.onemap_token, s.onemap_email, s.onemap_password)
        res = g.search("520123")
        row("OneMap", bool(res), f"token valid {(g._token_expiry - time.time()) / 86400:.1f} days")
    except Exception as exc:  # noqa: BLE001 - a health check reports any failure
        row("OneMap", False, repr(exc)[:150])
    checks = [
        ("OSRM routing", f"{s.osrm_base_url.rstrip('/')}/table/v1/driving/103.8990,1.3298;103.9403,1.3525", None),
        ("Nominatim (fallback)", "https://nominatim.openstreetmap.org/search", {"q": "Hougang Mall", "format": "json", "limit": 1}),
        ("OSM map tiles", "https://tile.openstreetmap.org/12/3226/2025.png", None),
    ]
    for name, u, params in checks:
        try:
            r = httpx.get(u, params=params, headers=UA, timeout=15)
            row(name, r.status_code == 200, f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001 - a health check reports any failure
            row(name, False, repr(exc)[:150])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
