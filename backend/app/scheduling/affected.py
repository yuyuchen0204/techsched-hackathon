"""Affected-set computation (section 10.1 of the brief).

affected_order_ids = existing valid orders (excluding the target) whose technician or planned service start changed.
Travel-only changes are reported as diffs but do not count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.scheduling.domain import Assign, Snapshot


@dataclass
class DiffEntry:
    order_id: str
    change: str  # new | technician | time | technician+time | travel_only | removed | unchanged
    old_tech: str | None
    new_tech: str | None
    old_start: int | None
    new_start: int | None
    shift_minutes: int
    priority: str
    counts: bool

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class AffectedResult:
    affected_ids: list[str] = field(default_factory=list)
    diff: list[DiffEntry] = field(default_factory=list)
    total_shift_minutes: int = 0
    technician_changes: int = 0
    removed_ids: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.affected_ids)


def compute_affected(snap: Snapshot, plan: dict[str, Assign], target_ids: set[str]) -> AffectedResult:
    res = AffectedResult()
    for oid, base in snap.assignments.items():
        order = snap.orders.get(oid)
        if order is None or order.closed:
            continue
        new = plan.get(oid)
        pr = order.priority.value
        if new is None:
            res.removed_ids.append(oid)
            res.diff.append(DiffEntry(oid, "removed", base.tech_id, None, base.service_start, None, 0, pr, oid not in target_ids))
            if oid not in target_ids:
                res.affected_ids.append(oid)
            continue
        tech_changed = new.tech_id != base.tech_id
        shift = new.service_start - base.service_start
        time_changed = abs(shift) >= 1
        if tech_changed and time_changed:
            change = "technician+time"
        elif tech_changed:
            change = "technician"
        elif time_changed:
            change = "time"
        elif new.travel != base.travel or new.departure != base.departure or new.origin_location_id != base.origin_location_id:
            change = "travel_only"
        else:
            change = "unchanged"
        counts = change in ("technician", "time", "technician+time") and oid not in target_ids
        res.diff.append(DiffEntry(oid, change, base.tech_id, new.tech_id, base.service_start, new.service_start,
                                  shift, pr, counts))
        if counts:
            res.affected_ids.append(oid)
            res.total_shift_minutes += abs(shift)
            if tech_changed:
                res.technician_changes += 1
    for oid, new in plan.items():
        if oid not in snap.assignments:
            order = snap.orders[oid]
            res.diff.append(DiffEntry(oid, "new", None, new.tech_id, None, new.service_start, 0, order.priority.value, False))
    res.affected_ids = sorted(set(res.affected_ids))
    return res


def changed_or_new_ids(snap: Snapshot, plan: dict[str, Assign]) -> list[str]:
    """Orders whose assignment is new or changed (technician or start) — the decision_score population."""
    out: list[str] = []
    for oid, a in plan.items():
        base = snap.assignments.get(oid)
        if base is None or base.tech_id != a.tech_id or base.service_start != a.service_start:
            out.append(oid)
    return out
