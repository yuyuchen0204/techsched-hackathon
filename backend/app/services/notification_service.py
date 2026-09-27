from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Notification
from app.services.clock import Clock
from app.services.ids import new_id


def notify(db: Session, clock: Clock, *, recipient_ref: str, recipient_type: str, type: str, message: str,
           order_id: str | None = None, dedupe_key: str | None = None) -> Notification | None:
    """In-app simulated notification. Never calls a real SMS/email channel."""
    if dedupe_key:
        existing = db.scalars(select(Notification).where(Notification.dedupe_key == dedupe_key)).first()
        if existing:
            return None
    n = Notification(id=new_id("ntf"), scenario_generation=clock.scenario_generation, recipient_ref=recipient_ref,
                     recipient_type=recipient_type, type=type, message=message, order_id=order_id,
                     delivery_mode="simulated", status="delivered", dedupe_key=dedupe_key, sim_time=clock.now)
    db.add(n)
    db.flush()
    return n
