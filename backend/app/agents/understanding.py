"""UnderstandingAgent: customer text → catalog-backed structured request. Tools: lookup_catalog, resolve_location,
validate_order. The LLM (mock or real) only proposes; every id is checked against the real catalog/locations."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Location
from app.providers.llm.base import InterpretedRequest
from app.providers.llm.factory import get_llm
from app.services import catalog_service
from app.services.clock import Clock
from app.services.timeutil import local_time_utc, to_local


@dataclass
class Understanding:
    interpreted: InterpretedRequest
    catalog_item: dict[str, Any] | None
    alternatives: list[dict[str, Any]]
    location: dict[str, Any] | None
    window: tuple[Any, Any] | None
    time_note: str | None = None  # 'past' | 'unparsed' when a time was mentioned but no window resulted
    location_resolution: LocationResolution | None = None
    llm: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def lookup_catalog(db: Session, query: str, limit: int = 5) -> list[dict[str, Any]]:
    return [catalog_service.snapshot_of(i) for i in catalog_service.search(db, query, limit)]


# Chinese names of the preset Singapore areas (aliases only; coordinates still come from the preset table)
AREA_ALIASES_ZH: dict[str, str] = {
    "后港": "Hougang", "淡滨尼": "Tampines", "勿洛": "Bedok", "巴耶利峇": "Paya Lebar", "大巴窑": "Toa Payoh", "碧山": "Bishan",
    "宏茂桥": "Ang Mo Kio", "实龙岗": "Serangoon", "榜鹅": "Punggol", "武吉士": "Bugis", "乌节": "Orchard", "女皇镇": "Queenstown",
    "金文泰": "Clementi", "裕廊东": "Jurong East", "裕廊": "Jurong East", "兀兰": "Woodlands", "义顺": "Yishun",
    "马林百列": "Marine Parade", "乌美": "Ubi",
}
ADDRESS_WORDS = re.compile(r"\b(blk|block|street|st|road|rd|avenue|ave|drive|dr|lane|ln|crescent|cres|close|walk|way|terrace|"
                           r"link|place|pl|central|hub|mall|tower|plaza|park|#\d)\b|\d", re.IGNORECASE)


def looks_like_address(hint: str) -> bool:
    """Digits / street words / postal codes → try the geocoder before matching a preset area name."""
    return bool(ADDRESS_WORDS.search(hint or ""))


@dataclass
class LocationResolution:
    location: dict[str, Any] | None = None           # resolved (preset or exact point)
    candidates: list[dict[str, Any]] = field(default_factory=list)  # ambiguous geocoder hits for the customer to pick
    provider: str | None = None                       # preset | alias | onemap | nominatim | area
    note: str | None = None                           # e.g. snapped-to-preset explanation / degradation reason


def _preset_match(db: Session, h: str) -> dict[str, Any] | None:
    for loc in db.scalars(select(Location).where(Location.area != "custom")).all():
        area = (loc.area or "").lower()
        if loc.name.lower() == h or h in loc.name.lower() or (area and (area in h or h in area)):
            return {"id": loc.id, "name": loc.name, "lat": loc.lat, "lon": loc.lon}
    return None


def resolve_location_ex(db: Session, hint: str | None) -> LocationResolution:
    """Preset areas (EN/ZH aliases), else geocoder (OneMap → Nominatim) for address-like text, else area substring.
    Never invents coordinates: every point comes from the preset table or a geocoding provider."""
    from app.providers.geocode.factory import enabled as geocode_enabled
    from app.providers.geocode.factory import geocode
    from app.services.location_service import resolve_point

    if not hint:
        return LocationResolution()
    h = hint.lower().strip()
    for zh, en in AREA_ALIASES_ZH.items():
        if zh in hint:
            loc = _preset_match(db, en.lower())
            if loc:
                return LocationResolution(loc, provider="alias")
    if not looks_like_address(hint):
        loc = _preset_match(db, h)
        if loc:
            return LocationResolution(loc, provider="preset")
    if geocode_enabled():
        postal = re.search(r"\b(\d{6})\b", hint)
        query = postal.group(1) if postal else hint  # a Singapore postal code is unambiguous: query just that
        out = geocode(query, limit=5)
        note = out.reason if out.degraded else None
        if out.results:
            from app.providers.geocode.base import normalize_address
            digits = set(re.findall(r"\d+", normalize_address(query)))  # unit numbers (#05-123) are not address numbers
            nums = lambda r: set(re.findall(r"\d+", f"{r.name} {r.address} {r.postal or ''}"))
            matching = [r for r in out.results if digits <= nums(r)] if digits else list(out.results)
            chosen = None
            if len(out.results) == 1:
                chosen = out.results[0]  # a single hit is the best available answer; the customer sees it in the reply
            elif len(matching) == 1:
                chosen = matching[0]  # exactly one hit carries every number the customer typed (block / postal code)
            elif not digits and matching and (len(matching) == 1 or (matching[0].score >= 0.2 and matching[0].score >= 2 * matching[1].score)):
                chosen = matching[0]  # a landmark that clearly dominates the other hits
            if chosen is not None:
                res = resolve_point(db, chosen.lat, chosen.lon, chosen.address)
                return LocationResolution(res["location"], provider=out.provider,
                                          note=(res["note"] if res["snapped"] else None) or note)
            seen: set[str] = set()
            cands: list[dict[str, Any]] = []
            for r in (matching or out.results):
                label = r.address + (f" (S{r.postal})" if r.postal and r.postal not in r.address else "")
                if label in seen:
                    continue  # identical labels give the customer nothing to choose between
                seen.add(label)
                cands.append({**r.as_dict(), "address": label})
                if len(cands) == 4:
                    break
            return LocationResolution(candidates=cands, provider=out.provider, note=note)
        if out.degraded and not out.results:
            note = f"address lookup unavailable ({out.reason})"
    else:
        note = "address lookup disabled (GEOCODE_MODE=none)"
    loc = _preset_match(db, h)
    if loc:
        area_note = f"I could not find the exact address “{hint}”, so I used the nearest service area ({loc['name']}); drop a pin on the map for the exact spot."
        return LocationResolution(loc, provider="area", note=(note + "; " if note else "") + area_note if looks_like_address(hint) else note)
    return LocationResolution(note=note)


def resolve_location(db: Session, hint: str | None) -> dict[str, Any] | None:
    return resolve_location_ex(db, hint).location


def _parse_clock(token: str) -> tuple[int, int] | None:
    t = token.strip().lower()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t) or re.match(r"^(\d{1,2})点(?:(\d{2})分?)?$", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    ampm = m.group(3) if m.re.groups >= 3 else None
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if h > 23 or mi > 59:
        return None
    return h, mi


def parse_time_hint(clock: Clock, hint: str | None) -> tuple[Any, Any] | None:
    window, _ = parse_time_hint_ex(clock, hint)
    return window


def parse_time_hint_ex(clock: Clock, hint: str | None) -> tuple[tuple[Any, Any] | None, str | None]:
    """Turn a verbatim time mention into a window on the scenario day: ranges ('9:30-9:50', 'between 2 and 3pm',
    '下午2点到3点'), single times (90-minute window) or day parts. Returns (window, reason) where reason is
    None | 'past' (already elapsed on the scenario day) | 'unparsed'."""
    if not hint:
        return None, None
    h = hint.lower().strip()
    day = to_local(clock.day_origin).date()  # type: ignore[union-attr]
    rng = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}点)\s*(?:-|–|to|and|到|至)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}点)", h)
    start: tuple[int, int] | None = None
    end: tuple[int, int] | None = None
    if rng:
        a, b = _parse_clock(rng.group(1)), _parse_clock(rng.group(2))
        if a and b:
            # "2 and 3pm": inherit pm from the end token; afternoon words also push into pm
            if ("pm" in rng.group(2) or "下午" in h) and a[0] < 12 and b[0] >= 12 and not re.search(r"am|pm", rng.group(1)):
                a = (a[0] + 12, a[1])
            start, end = a, b
    if start is None:
        single = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\d{1,2}点)", h)
        if single:
            start = _parse_clock(single.group(1))  # an explicit clock time beats the day-part defaults below
            if start and "下午" in h and start[0] < 12:
                start = (start[0] + 12, start[1])
        elif "morning" in h or "上午" in h:
            start = (9, 0)
        elif "afternoon" in h or "下午" in h:
            start = (13, 0)
        elif "evening" in h or "晚上" in h:
            start = (17, 0)
    if start is None:
        return None, "unparsed"
    pm_words = "下午" in h or "晚上" in h or "pm" in h
    explicit_am = "am" in h or "上午" in h or "morning" in h

    def _adjust(t: tuple[int, int]) -> tuple[int, int]:
        # afternoon words, or a bare 1–6 o'clock (service hours are 08–19), mean pm
        if t[0] < 12 and not explicit_am and (pm_words or 1 <= t[0] <= 6):
            return (t[0] + 12, t[1])
        return t

    start = _adjust(start)
    if end is not None:
        end = _adjust(end)
        if end <= start and end[0] < 12:
            end = (end[0] + 12, end[1])
    ws = local_time_utc(day, start[0], start[1])
    if end is None or local_time_utc(day, end[0], end[1]) <= ws:
        we = ws + timedelta(minutes=90)
    else:
        we = local_time_utc(day, end[0], end[1])
    if we <= clock.now:
        return None, "past"
    return (ws, we), None


def understand(db: Session, clock: Clock, text: str, known: dict[str, Any] | None = None) -> Understanding:
    """`known` = slots already collected in the session (problem, location, window, contact) so the model does not
    ask for them again; the current sim time/day is passed so times are interpreted on the scenario day."""
    items = catalog_service.list_active(db)
    catalog = [{"id": i.id, "trade_type": i.trade_type, "problem_name": i.problem_name, "source_row": i.source_row} for i in items]
    location_names = [l.name for l in db.scalars(select(Location)).all()]
    llm = get_llm()
    local_now = to_local(clock.now)
    interpreted = llm.interpret(text, catalog, {
        "location_names": location_names, "already_collected": known or {},
        "now_local": local_now.strftime("%Y-%m-%d %H:%M") if local_now else None,
        "service_day": to_local(clock.day_origin).strftime("%Y-%m-%d") if to_local(clock.day_origin) else None,  # type: ignore[union-attr]
    })
    tool_calls = [{"tool": "lookup_catalog", "facts": {"candidates": len(catalog), "selected": interpreted.catalog_item_id}}]
    valid = {c["id"] for c in catalog}
    item = catalog_service.get_item(db, interpreted.catalog_item_id) if interpreted.catalog_item_id and interpreted.catalog_item_id in valid else None
    alts = [catalog_service.snapshot_of(catalog_service.get_item(db, a)) for a in interpreted.alternatives if a in valid and a != interpreted.catalog_item_id]  # type: ignore[arg-type]
    lr = resolve_location_ex(db, interpreted.location_hint)
    location = lr.location
    tool_calls.append({"tool": "resolve_location", "facts": {"hint": interpreted.location_hint, "resolved": location["id"] if location else None,
                                                             "provider": lr.provider, "candidates": len(lr.candidates), "note": lr.note}})
    window, time_note = parse_time_hint_ex(clock, interpreted.time_hint)
    tool_calls.append({"tool": "parse_time_hint", "facts": {"hint": interpreted.time_hint, "window": bool(window), "note": time_note}})
    return Understanding(interpreted, catalog_service.snapshot_of(item) if item else None, alts[:4], location, window,
                         time_note=time_note, location_resolution=lr, llm=dict(llm.last), tool_calls=tool_calls)
