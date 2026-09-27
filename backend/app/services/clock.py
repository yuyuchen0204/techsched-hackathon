"""ClockProvider: single business 'now' shared by scheduling, risk, cancel, expiry and UI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.entities import SimulationState
from app.services.timeutil import add_minutes, local_midnight_utc, local_time_utc, minutes_between


@dataclass(frozen=True)
class Clock:
    now: datetime  # UTC naive
    day_origin: datetime  # local midnight as UTC naive
    timezone: str
    running: bool
    scenario_generation: int
    scenario_name: str
    seed: int

    def to_minutes(self, dt: datetime) -> int:
        return minutes_between(self.day_origin, dt)

    def from_minutes(self, minutes: float) -> datetime:
        return add_minutes(self.day_origin, minutes)

    @property
    def now_minutes(self) -> int:
        return self.to_minutes(self.now)


def _scenario_day() -> date:
    return date.fromisoformat(get_settings().scenario_date)


def ensure_state(db: Session) -> SimulationState:
    state = db.get(SimulationState, 1)
    if state is None:
        day = _scenario_day()
        state = SimulationState(
            id=1,
            now=local_time_utc(day, 8, 30),
            day_origin=local_midnight_utc(day),
            timezone=get_settings().app_timezone,
            running=False,
            scenario_generation=0,
            scenario_name="main",
            seed=42,
        )
        db.add(state)
        db.flush()
    return state


def get_clock(db: Session) -> Clock:
    s = ensure_state(db)
    return Clock(
        now=s.now, day_origin=s.day_origin, timezone=s.timezone, running=s.running,
        scenario_generation=s.scenario_generation, scenario_name=s.scenario_name, seed=s.seed,
    )


def set_now(db: Session, now: datetime) -> Clock:
    s = ensure_state(db)
    s.now = now
    db.flush()
    return get_clock(db)


def set_running(db: Session, running: bool) -> Clock:
    s = ensure_state(db)
    s.running = running
    db.flush()
    return get_clock(db)


def reset_state(db: Session, scenario_name: str, seed: int, start_hour: int = 8, start_minute: int = 30) -> Clock:
    s = ensure_state(db)
    day = _scenario_day()
    s.now = local_time_utc(day, start_hour, start_minute)
    s.day_origin = local_midnight_utc(day)
    s.running = False
    s.scenario_generation = s.scenario_generation + 1
    s.scenario_name = scenario_name
    s.seed = seed
    s.last_scan_at = None
    db.flush()
    return get_clock(db)
