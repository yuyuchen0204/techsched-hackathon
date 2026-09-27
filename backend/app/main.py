from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.errors import install_error_handlers
from app.api.routes_core import router as core_router
from app.api.routes_demo import router as demo_router
from app.config import get_policy, get_settings
from app.db.init_db import init_db
from app.db.session import session_scope
from app.models.entities import Technician
from app.services import catalog_service, demo_service, execution_service
from app.services.clock import ensure_state, get_clock

log = logging.getLogger("techsched")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _background_tick(settings, last_scan: float) -> float:
    """Runs in a worker thread so waiting for the state lock never blocks the event loop."""
    now = time.monotonic()
    with execution_service.state_lock, session_scope() as db:
        clock = get_clock(db)
        if clock.running:
            execution_service.advance_clock(db, max(1, settings.clock_run_sim_minutes_per_second))
            return now
        if now - last_scan >= settings.risk_scan_interval_seconds:
            execution_service.scan(db, trigger="periodic_scan")
            return now
    return last_scan


async def background_loop(app: FastAPI) -> None:
    """Single-process loop: advances the sim clock when running, scans risks every RISK_SCAN_INTERVAL_SECONDS real seconds."""
    settings = get_settings()
    last_scan = time.monotonic()
    while True:
        try:
            await asyncio.sleep(1.0)
            last_scan = await asyncio.to_thread(_background_tick, settings, last_scan)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background loop iteration failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    with session_scope() as db:
        ensure_state(db)
        demo_service.ensure_locations(db)
        result = catalog_service.reload_catalog(db)
        if not result.ok:
            log.warning("catalog not configured: %s", result.fatal_error)
        else:
            log.info("catalog loaded: %d items (version %s)", result.valid_count, result.catalog_version)
        has_techs = db.scalars(select(Technician)).first() is not None
        if not has_techs and result.ok:
            summary = demo_service.seed_scenario(db, "main")
            log.info("seeded scenario: %s", summary)
    task = None
    worker = None
    if settings.background_loop_enabled:
        task = asyncio.create_task(background_loop(app))
        from app.agents import runtime as agent_runtime
        worker = agent_runtime.start_worker_thread()   # model-driven agent tasks: own thread, never on the clock tick
    try:
        yield
    finally:
        if worker is not None:
            from app.agents import runtime as agent_runtime
            agent_runtime.stop_worker_thread()
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    app = FastAPI(title="Technician Scheduling", version="0.2.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    install_error_handlers(app)
    app.include_router(core_router)
    app.include_router(demo_router)
    from app.api.routes_chat import router as chat_router
    app.include_router(chat_router)
    from app.api.routes_eval import router as eval_router
    app.include_router(eval_router)
    from app.api.routes_v3 import router as v3_router
    app.include_router(v3_router)

    @app.get("/health")
    def health() -> dict[str, Any]:
        s = get_settings()
        with session_scope() as db:
            cat = catalog_service.catalog_status(db)
            clock = get_clock(db)
        return {"status": "ok", **s.public_dict(), "policy_version": get_policy().policy_version,
                "catalog_configured": cat["configured"], "catalog_items": cat["active_count"], "catalog_version": cat["catalog_version"],
                "sim_now": clock.now.isoformat() + "Z", "scenario_generation": clock.scenario_generation}

    return app


app = create_app()
