"""PolicyEngine: the single interpreter of auto / manual / forbidden (section 11.3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Policy
from app.models.enums import PolicyDecision, Priority
from app.scheduling.affected import AffectedResult
from app.scheduling.domain import Snapshot


@dataclass
class AuthorityCheck:
    ok: bool
    max_affected: int | None  # None = no cap
    movable_priorities: list[str]
    affected_count: int
    violations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def check_authority(snap: Snapshot, target_priority: Priority, affected: AffectedResult, policy: Policy,
                    target_ids: set[str]) -> AuthorityCheck:
    rule = policy.rule(target_priority.value)
    chk = AuthorityCheck(True, rule.max_affected, list(rule.movable_priorities), affected.count)
    if affected.removed_ids:
        for oid in affected.removed_ids:
            if oid not in target_ids:
                chk.violations.append(f"order {oid} would lose its assignment without a new destination")
    for oid in affected.affected_ids:
        order = snap.orders[oid]
        if order.departed:
            chk.violations.append(f"order {oid} already departed ({order.lifecycle_status}) and cannot be moved")
        elif order.priority.value not in rule.movable_priorities:
            chk.violations.append(
                f"{target_priority.value} may not move {order.priority.value} order {oid} "
                f"(movable: {rule.movable_priorities or 'none'})"
            )
    if not rule.within_limit(affected.count):
        chk.violations.append(f"affected {affected.count} exceeds limit {rule.max_affected} for {target_priority.value}")
    chk.ok = not chk.violations
    return chk


@dataclass
class PolicyOutcome:
    decision: PolicyDecision
    reasons: list[str]
    decision_score: float | None
    threshold: float
    authority: AuthorityCheck | None
    over_limit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reasons": self.reasons,
            "decision_score": self.decision_score,
            "threshold": self.threshold,
            "threshold_operator": ">",
            "authority": self.authority.as_dict() if self.authority else None,
            "over_limit": self.over_limit,
        }


def decide(
    *,
    target_priority: Priority,
    decision_score: float | None,
    validation_ok: bool,
    authority: AuthorityCheck,
    policy: Policy,
    has_changes: bool,
    search_incomplete: bool = False,
    p2_has_valid_assignment: bool = False,
) -> PolicyOutcome:
    reasons: list[str] = []
    thr = policy.auto_score_threshold
    if not has_changes:
        return PolicyOutcome(PolicyDecision.NO_ACTION, ["plan contains no new or changed assignment"], None, thr, authority)
    if not validation_ok:
        return PolicyOutcome(PolicyDecision.FORBIDDEN, ["hard constraint violation"], decision_score, thr, authority)
    if not authority.ok:
        over = authority.max_affected is not None and authority.affected_count > authority.max_affected
        reasons.extend(authority.violations)
        return PolicyOutcome(PolicyDecision.FORBIDDEN, reasons, decision_score, thr, authority, over_limit=over)
    high = policy.passes_threshold(decision_score)
    score_txt = f"decision_score {decision_score:.2f} {'>' if high else '<='} {thr:g}" if decision_score is not None else "no score"
    if search_incomplete:
        reasons.append("search budget exhausted before completion; incumbent requires review")
        return PolicyOutcome(PolicyDecision.MANUAL, reasons + [score_txt], decision_score, thr, authority)
    p = target_priority
    if p == Priority.P3:
        reasons.append("P3: no other order may change")
        if high:
            return PolicyOutcome(PolicyDecision.AUTO, reasons + [score_txt], decision_score, thr, authority)
        return PolicyOutcome(PolicyDecision.MANUAL, reasons + [score_txt, "low score requires dispatcher review"], decision_score, thr, authority)
    if p == Priority.P2:
        if p2_has_valid_assignment:
            return PolicyOutcome(PolicyDecision.STANDBY, ["P2 with valid assignment: keep and prepare standby"], decision_score, thr, authority)
        reasons.append("P2 without valid assignment: zero-disruption re-dispatch")
        if high:
            return PolicyOutcome(PolicyDecision.AUTO, reasons + [score_txt], decision_score, thr, authority)
        return PolicyOutcome(PolicyDecision.MANUAL, reasons + [score_txt, "low score requires dispatcher review"], decision_score, thr, authority)
    if p == Priority.P1:
        limit = "no limit" if authority.max_affected is None else str(authority.max_affected)
        reasons.append(f"P1: affected {authority.affected_count}/{limit}, all undeparted P3")
        if high:
            return PolicyOutcome(PolicyDecision.AUTO, reasons + [score_txt], decision_score, thr, authority)
        return PolicyOutcome(PolicyDecision.MANUAL, reasons + [score_txt, "low score requires dispatcher review"], decision_score, thr, authority)
    # P0
    if authority.affected_count == 0:
        reasons.append("P0 with zero affected orders")
        if high:
            return PolicyOutcome(PolicyDecision.AUTO, reasons + [score_txt], decision_score, thr, authority)
        return PolicyOutcome(PolicyDecision.MANUAL, reasons + [score_txt, "low score requires dispatcher review"], decision_score, thr, authority)
    reasons.append(f"P0 affecting {authority.affected_count} undeparted P2/P3 orders: dispatcher approval required")
    return PolicyOutcome(PolicyDecision.MANUAL, reasons + [score_txt], decision_score, thr, authority)
