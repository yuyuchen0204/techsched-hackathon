from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import WorkOrder


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def next_order_id(db: Session) -> str:
    """Sequential, readable work-order ids (wo_001, wo_002, …).

    Random hex ids (wo_bc36b1ab71) are unreadable on a board, in a demo and over the phone, and give no clue about the
    order in which work arrived. The number is taken from the highest existing wo_<n> so it never collides with the
    seeded orders, and it keeps counting across a demo reset rather than restarting on top of ids people just saw.
    """
    highest = 0
    for (oid,) in db.execute(select(WorkOrder.id).where(WorkOrder.id.like("wo\\_%", escape="\\"))):
        tail = oid.split("_", 1)[1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"wo_{highest + 1:03d}"
