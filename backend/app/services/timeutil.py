"""Time semantics: API = tz-aware ISO 8601; DB = naive UTC; solver = integer minutes from day origin."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().app_timezone)


def to_utc_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(UTC).replace(tzinfo=None)


def as_aware_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return as_aware_utc(dt).astimezone(tz())


def iso(dt: datetime | None) -> str | None:
    """ISO 8601 with offset in app timezone (for API output)."""
    local = to_local(dt)
    return local.isoformat(timespec="minutes") if local else None


def parse_iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return to_utc_naive(value if value.tzinfo else value.replace(tzinfo=tz()))
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return to_utc_naive(dt)


def local_midnight_utc(day: date) -> datetime:
    return to_utc_naive(datetime(day.year, day.month, day.day, tzinfo=tz()))


def local_time_utc(day: date, hour: int, minute: int = 0) -> datetime:
    return to_utc_naive(datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz()))


def minutes_between(a: datetime, b: datetime) -> int:
    return round((b - a).total_seconds() / 60)


def add_minutes(dt: datetime, minutes: float) -> datetime:
    return dt + timedelta(minutes=minutes)


def hhmm(dt: datetime | None) -> str:
    local = to_local(dt)
    return local.strftime("%H:%M") if local else "--:--"
