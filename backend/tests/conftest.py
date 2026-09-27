from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("BACKGROUND_LOOP_ENABLED", "false")
os.environ["LLM_MODE"] = "mock"  # tests never call a real model, whatever .env says
os.environ["ROUTE_MODE"] = "fixture"  # tests never call a routing service
os.environ["GEOCODE_MODE"] = "none"  # tests never call a geocoder (unit tests inject fakes)

import pytest

from app.config import Policy, get_policy
from app.models.enums import Priority
from app.providers.route.base import TravelMatrix
from app.scheduling.domain import Assign, OrderSpec, Snapshot, TechSpec


@pytest.fixture
def policy() -> Policy:
    return get_policy()


def make_matrix(ids: list[str], default: int = 20, overrides: dict[tuple[str, str], int | None] | None = None) -> TravelMatrix:
    m = TravelMatrix(provider="test", snapshot_id="test:1")
    for a in ids:
        for b in ids:
            m.minutes[(a, b)] = 0 if a == b else default
    for k, v in (overrides or {}).items():
        m.minutes[k] = v
    return m


def make_order(oid: str, *, trade="Air Conditioning", level=2, duration=35, ws=600, we=720, loc="L1",
               priority=Priority.P3, status="OPEN", paid=False, recovery=False, version=1) -> OrderSpec:
    return OrderSpec(id=oid, trade_type=trade, required_level=level, duration=duration, window_start=ws, window_end=we,
                     location_id=loc, priority=priority, lifecycle_status=status, version=version, paid_expedite=paid,
                     recovery_target=recovery, customer_ref="c")


def make_tech(tid: str, *, skills=None, shift=(480, 1080), breaks=(), unavailable=(), loc="L0", status="AVAILABLE") -> TechSpec:
    return TechSpec(id=tid, name=tid, skills=skills or {"Air Conditioning": 3}, shift_start=shift[0], shift_end=shift[1],
                    breaks=tuple(breaks), unavailable=tuple(unavailable), home_location_id=loc, current_location_id=loc,
                    status=status, version=1)


def make_snapshot(now: int, orders: list[OrderSpec], techs: list[TechSpec], assignments: list[Assign] | None = None,
                  matrix: TravelMatrix | None = None) -> Snapshot:
    locs = sorted({o.location_id for o in orders} | {t.current_location_id for t in techs} | {"L0", "L1", "L2", "L3", "L4"})
    return Snapshot(now=now, orders={o.id: o for o in orders}, techs={t.id: t for t in techs},
                    assignments={a.order_id: a for a in (assignments or [])}, matrix=matrix or make_matrix(locs),
                    schedule_version=1, scenario_generation=0)


def assign_from_route(snap: Snapshot, tech_id: str, seq: list[str], locked: set[str] | None = None) -> list[Assign]:
    from app.scheduling.simulator import simulate_route
    rr = simulate_route(snap, snap.techs[tech_id], seq)
    assert rr.feasible, rr.violations
    out = []
    for a in rr.assignments:
        out.append(Assign(**{**a.__dict__, "locked": a.order_id in (locked or set())}))
    return out


@pytest.fixture
def db_session():
    """Fresh in-memory SQLite with catalog + locations loaded and the main scenario seeded."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.db.session import configure_engine, session_factory
    from app.services import catalog_service, demo_service
    from app.services.snapshot import _matrix_cache

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    import app.models  # noqa: F401
    Base.metadata.create_all(engine)
    configure_engine(engine)
    _matrix_cache.clear()
    session = session_factory()()
    catalog_service.reload_catalog(session)
    demo_service.ensure_locations(session)
    demo_service.seed_scenario(session, "main")
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient

    from app.main import create_app
    app = create_app()
    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    with TestClient(app) as c:
        yield c


from contextlib import asynccontextmanager


@asynccontextmanager
async def _noop_lifespan(app):
    yield
