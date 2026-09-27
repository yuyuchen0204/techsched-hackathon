from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_engine


def init_db(engine: Engine | None = None) -> Engine:
    """Create tables if missing. Schema is managed by create_all + additive migrations in migrate()."""
    settings = get_settings()
    url = settings.sqlalchemy_url
    if url.startswith("sqlite:///"):
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    engine = engine or get_engine()
    import app.models  # noqa: F401  ensure models are registered
    Base.metadata.create_all(engine)  # new tables (additive)
    from app.db.migrations import migrate
    migrate(engine)  # new columns on existing tables + data backfills, recorded in schema_migrations
    return engine
