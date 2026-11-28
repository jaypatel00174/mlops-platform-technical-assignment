"""Approving or rejecting a version, with separation of duties."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from mlops.governance.domain.rbac import Action, require
from mlops.kernel.errors import InvalidTransition, SeparationOfDuties, ValidationFailure
from mlops.kernel.vocabulary import ApprovalStatus, Role, Stage


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    version_id: str
    decision: ApprovalStatus
    decided_by: str
    comment: str
    decided_at: datetime


def decide(
    *,
    version_id: str,
    version_stage: Stage,
    registered_by: str,
    decided_by: str,
    roles: Collection[Role],
    approve: bool,
    comment: str | None,
    now: datetime,
) -> ApprovalDecision:
    """Validate and build an approval decision.

    Checks run in this order: permission, separation of duties (nobody decides on their own
    version, admins included), the version must be VALIDATED, and a rejection needs a reason.
    """
    require(roles, Action.APPROVE_VERSION)
    if decided_by == registered_by:
        raise SeparationOfDuties(
            "the person who registered a version cannot approve or reject it",
            actor=decided_by,
        )
    if version_stage is not Stage.VALIDATED:
        raise InvalidTransition(
            "only a VALIDATED version can be approved or rejected",
            current=version_stage.value,
            required_stage=Stage.VALIDATED.value,
        )
    text = (comment or "").strip()
    if not approve and not text:
        raise ValidationFailure("a rejection needs a reason", field="comment")
    return ApprovalDecision(
        version_id=version_id,
        decision=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
        decided_by=decided_by,
        comment=text,
        decided_at=now,
    )
