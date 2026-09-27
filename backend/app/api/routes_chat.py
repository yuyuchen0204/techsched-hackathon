from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.agents.understanding import understand
from app.api.deps import DB, locked
from app.db.session import session_scope
from app.schemas.api import ChatMessageRequest, InterpretRequest
from app.services import chat_service
from app.services.clock import get_clock
from app.services.timeutil import iso

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.post("/chat/messages")
def chat_message(body: ChatMessageRequest) -> dict[str, Any]:
    # 1. slow model work (interpretation / classification) on a read-only session, no state lock held
    try:
        with session_scope() as ro:
            pre = chat_service.pre_model_work(ro, body.session_id, body.message, body.action, body.payload)
    except Exception:
        log.exception("pre-lock model work failed; falling back to in-lock interpretation")
        pre = {}
    # 2. the state change itself under the lock (fast)
    return _chat_message_locked(body, pre)


@locked
def _chat_message_locked(body: ChatMessageRequest, pre: dict[str, Any], db: Session) -> dict[str, Any]:
    return chat_service.handle(db, body.session_id, body.message, body.action, body.payload, pre=pre)


@router.get("/chat/sessions/{session_id}")
def chat_session(session_id: str, db: Session = DB) -> dict[str, Any]:
    clock = get_clock(db)
    s = chat_service.get_session(db, session_id, clock)
    # the identified customer has to be carried here too: without it this poll returns only the orders placed in this
    # browser session, and a returning customer watched their earlier orders disappear a few seconds after signing in
    customer_id = (s.draft or {}).get("customer_id")
    return {"session_id": s.id, "state": s.state, "draft": chat_service._draft_view(db, s.draft or {}), "messages": (s.messages or [])[-30:],
            "orders": chat_service.session_orders(db, clock, session_id, customer_id), "slots": chat_service.slot_options(clock), "mode": "simulated",
            "sim_now": iso(clock.now)}


@router.post("/orders/interpret")
def interpret(body: InterpretRequest, db: Session = DB) -> dict[str, Any]:
    clock = get_clock(db)
    u = understand(db, clock, body.text)
    return {"interpreted": u.interpreted.model_dump(), "catalog_item": u.catalog_item, "alternatives": u.alternatives,
            "location": u.location, "window": [u.window[0].isoformat() + "Z", u.window[1].isoformat() + "Z"] if u.window else None,
            "llm": u.llm, "tool_calls": u.tool_calls}
