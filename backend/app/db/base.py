from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    """Real wall-clock UTC (naive, for SQLite storage). Business 'now' comes from ClockProvider."""
    return datetime.now(UTC).replace(tzinfo=None)
