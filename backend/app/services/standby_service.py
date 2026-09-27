"""P2 standby: up to N alternative technicians (not reservations), re-validated before use."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_policy
from app.models.entities import StandbyCandidate, WorkOrder
from app.scheduling.domain import Snapshot
from app.scheduling.simulator import simulate_route
from app.scheduling.solver import pinned_starts, qualified_techs
from app.services.clock import Clock
from app.services.ids import new_id


def compute_standby(snap: Snapshot, order_id: str) -> list[dict[str, Any]]:
    """Zero-disturbance alternatives: earliest feasible start per other qualified technician."""
    order = snap.orders[order_id]
    current = snap.assignments.get(order_id)
    pins = pinned_starts(snap)
    pins.pop(order_id, None)
    base = {oid: a for oid, a in snap.assignments.items() if oid != order_id}
    out: list[dict[str, Any]] = []
    for tech in qualified_techs(snap, order):
        if current and tech.id == current.tech_id:
            continue
        movable = snap.movable_route(tech.id, base)
        seq = [a.order_id for a in movable]
        best: int | None = None
        for pos in range(len(seq) + 1):
            rr = simulate_route(snap, tech, seq[:pos] + [order_id] + seq[pos:], pins)
            if not rr.feasible:
                continue
            # zero disturbance: every other order keeps its pinned start
            shifted = any(a.service_start != pins[a.order_id] for a in rr.assignments if a.order_id != order_id)
            target = next(a for a in rr.assignments if a.order_id == order_id)
            if shifted or target.service_end > tech.shift_end:
                continue
            within = order.recovery_target or target.service_start <= order.window_end
            if within and (best is None or target.service_start < best):
                best = target.service_start
        if best is not None:
            out.append({"technician_id": tech.id, "technician_name": tech.name, "earliest_start": best,
                        "skill_match": {"level": tech.skills.get(order.trade_type, 0), "required": order.required_level}})
    out.sort(key=lambda c: (c["earliest_start"], c["technician_id"]))
    return out[: get_policy().standby_max]


def refresh_standby(db: Session, clock: Clock, snap: Snapshot, order: WorkOrder) -> list[StandbyCandidate]:
    for row in db.scalars(select(StandbyCandidate).where(StandbyCandidate.order_id == order.id)).all():
        db.delete(row)
    rows: list[StandbyCandidate] = []
    for c in compute_standby(snap, order.id):
        row = StandbyCandidate(
            id=new_id("sby"), order_id=order.id, technician_id=c["technician_id"], technician_name=c["technician_name"],
            earliest_start=clock.from_minutes(c["earliest_start"]), skill_match=c["skill_match"],
            snapshot_version=snap.schedule_version, computed_at=clock.now,
            expires_at=clock.from_minutes(clock.now_minutes + get_policy().candidate_expiry_sim_minutes), valid=True,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def invalidate_standby(db: Session, order_id: str) -> None:
    for row in db.scalars(select(StandbyCandidate).where(StandbyCandidate.order_id == order_id)).all():
        row.valid = False
    db.flush()
