from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Iterator
from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import session_factory
from app.services.execution_service import state_lock


def get_db() -> Iterator[Session]:
    """Read-only endpoints: plain session (no global lock)."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


DB = Depends(get_db)


def locked(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mutating endpoints: acquire the single-process state lock, open a session, commit/rollback and release
    BEFORE the response is produced. The wrapped function must declare a `db: Session` parameter, which is hidden
    from FastAPI's dependency injection."""
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.values() if p.name != "db"]

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with state_lock:
            session = session_factory()()
            try:
                result = fn(*args, db=session, **kwargs)
                session.commit()
                return result
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

    wrapper.__signature__ = sig.replace(parameters=params)  # type: ignore[attr-defined]
    return wrapper
