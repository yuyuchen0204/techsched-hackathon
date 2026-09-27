"""Simulated technician positions (V3 §6.3): derived from the sim clock, route geometry and planned/actual trips.
No random drift; paused clock = frozen positions; arrived = stays at the destination; source is labelled."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Location, Technician, WorkOrder
from app.providers.route.base import LocationPoint
from app.providers.route.factory import leg_geometry
from app.services.clock import Clock
from app.services.snapshot import build_snapshot
from app.services.timeutil import iso

_geom_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}


def _points(db: Session, a_id: str, b_id: str, locs: dict[str, Location]) -> list[tuple[float, float]]:
    key = (a_id, b_id)
    if key not in _geom_cache:
        la, lb = locs.get(a_id), locs.get(b_id)
        if la is None or lb is None:
            return []
        g = leg_geometry(LocationPoint(la.id, la.name, la.lat, la.lon), LocationPoint(lb.id, lb.name, lb.lat, lb.lon))
        _geom_cache[key] = g.points
    return _geom_cache[key]


def _interpolate(points: list[tuple[float, float]], frac: float) -> tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    if len(points) == 1 or frac <= 0:
        return points[0]
    if frac >= 1:
        return points[-1]
    import math
    seg = [math.hypot(points[i + 1][0] - points[i][0], (points[i + 1][1] - points[i][1]) * math.cos(math.radians(points[i][0]))) for i in range(len(points) - 1)]
    total = sum(seg) or 1.0
    target = frac * total
    acc = 0.0
    for i, s in enumerate(seg):
        if acc + s >= target:
            t = (target - acc) / s if s else 0.0
            return (points[i][0] + (points[i + 1][0] - points[i][0]) * t, points[i][1] + (points[i + 1][1] - points[i][1]) * t)
        acc += s
    return points[-1]


def positions(db: Session, clock: Clock) -> list[dict[str, Any]]:
    snap = build_snapshot(db, clock)
    locs = {l.id: l for l in db.scalars(select(Location)).all()}
    orders = {o.id: o for o in db.scalars(select(WorkOrder).where(WorkOrder.scenario_generation == clock.scenario_generation, WorkOrder.window_end >= clock.day_origin)).all()}
    out: list[dict[str, Any]] = []
    for t in db.scalars(select(Technician).where(Technician.scenario_generation == clock.scenario_generation)).all():
        route = snap.route_of(t.id)
        locked = next((a for a in route if a.locked), None)
        nxt = next((a for a in route if not a.locked), None)
        state = t.status
        current_service = None
        coords: tuple[float, float]
        moving = False
        progress = None
        if locked is not None:
            o = orders.get(locked.order_id)
            dest = locs.get(snap.orders[locked.order_id].location_id)
            if o is not None and o.lifecycle_status == "EN_ROUTE" and o.departed_at is not None and dest is not None:
                total = max(1, (locked.arrival - locked.departure))
                elapsed = clock.now_minutes - clock.to_minutes(o.departed_at)
                progress = max(0.0, min(1.0, elapsed / total))
                pts = _points(db, locked.origin_location_id, dest.id, locs) or [(dest.lat, dest.lon)]
                origin = locs.get(locked.origin_location_id)
                if origin and pts and pts[0] != (origin.lat, origin.lon):
                    pts = [(origin.lat, origin.lon)] + pts
                coords = _interpolate(pts, progress)
                moving = progress < 1.0
                state = "EN_ROUTE"
            elif dest is not None:
                coords = (dest.lat, dest.lon)
                current_service = {"order_id": locked.order_id, "location_id": dest.id, "name": dest.name, "status": o.lifecycle_status if o else None}
                state = "IN_PROGRESS" if (o and o.lifecycle_status == "IN_PROGRESS") else "ARRIVED"
            else:
                coords = (t.lat, t.lon)
        else:
            here = locs.get(t.current_location_id)
            coords = (here.lat, here.lon) if here else (t.lat, t.lon)
        # Where the technician is driving RIGHT NOW takes precedence over what is queued after it. The queued job used
        # to win this branch, so a technician en route to A with B waiting was reported as "en route to B" — wrong in
        # the tooltip, and wrong for anything (like trimming the drawn route) that trusted it.
        current_leg = None
        next_dest = None
        if locked is not None and moving:
            d = locs.get(snap.orders[locked.order_id].location_id)
            if d:
                current_leg = {"order_id": locked.order_id, "from_location_id": locked.origin_location_id,
                               "to_location_id": d.id, "progress": round(progress, 3) if progress is not None else None}
                next_dest = {"order_id": locked.order_id, "location_id": d.id, "name": d.name, "lat": d.lat, "lon": d.lon,
                             "planned_departure": iso(clock.from_minutes(locked.departure)), "eta": iso(clock.from_minutes(locked.arrival))}
        elif nxt is not None and (locked is None or locked.order_id != nxt.order_id):
            d = locs.get(snap.orders[nxt.order_id].location_id)
            if d:
                next_dest = {"order_id": nxt.order_id, "location_id": d.id, "name": d.name, "lat": d.lat, "lon": d.lon,
                             "planned_departure": iso(clock.from_minutes(nxt.departure)), "eta": iso(clock.from_minutes(nxt.arrival))}
        out.append({"technician_id": t.id, "name": t.name, "status": state, "sim_mode": t.sim_mode, "current_coords": [round(coords[0], 6), round(coords[1], 6)],
                    "moving": moving, "leg_progress": round(progress, 3) if progress is not None else None,
                    "current_service_location": current_service, "next_destination": next_dest, "current_leg": current_leg,
                    "source": "simulated: route geometry + sim clock (no GPS, no live traffic)", "sim_time": iso(clock.now)})
    return out


def tracking_for_order(db: Session, clock: Clock, order: WorkOrder) -> dict[str, Any]:
    """Customer view: only while the assigned technician is EN_ROUTE / ARRIVED / IN_PROGRESS for THIS order."""
    if order.lifecycle_status not in ("EN_ROUTE", "ARRIVED", "IN_PROGRESS") or not order.technician_id:
        return {"active": False, "reason": f"tracking is shown after the technician departs (status {order.lifecycle_status})"}
    snap = positions(db, clock)
    p = next((x for x in snap if x["technician_id"] == order.technician_id), None)
    if p is None:
        return {"active": False, "reason": "technician position unavailable"}
    locs = {l.id: l for l in db.scalars(select(Location)).all()}
    a = next((a for a in build_snapshot(db, clock).route_of(order.technician_id) if a.order_id == order.id), None)
    pts = _points(db, a.origin_location_id, order.location_id, locs) if a else []
    return {"active": True, "technician": {"id": p["technician_id"], "name": p["name"], "status": p["status"]}, "current_coords": p["current_coords"],
            "moving": p["moving"], "leg_progress": p["leg_progress"], "eta": iso(clock.from_minutes(a.arrival)) if a else None,
            "planned_service_start": iso(clock.from_minutes(a.service_start)) if a else None, "route_points": pts,
            "destination": {"lat": order.lat, "lon": order.lon}, "source": p["source"], "sim_time": p["sim_time"]}
