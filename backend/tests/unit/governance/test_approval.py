from __future__ import annotations

from datetime import datetime

import pytest

from mlops.governance.domain.approval import ApprovalDecision, decide
from mlops.kernel.errors import Forbidden, InvalidTransition, SeparationOfDuties, ValidationFailure
from mlops.kernel.vocabulary import ApprovalStatus, Role, Stage


def decision(now: datetime, **overrides: object) -> ApprovalDecision:
    values: dict[str, object] = {
        "version_id": "ver-1",
        "version_stage": Stage.VALIDATED,
        "registered_by": "alice",
        "decided_by": "bob",
        "roles": [Role.APPROVER],
        "approve": True,
        "comment": "metrics look good",
        "now": now,
    }
    values.update(overrides)
    return decide(**values)  # type: ignore[arg-type]


def test_an_approver_can_approve_a_validated_version(now: datetime) -> None:
    result = decision(now)

    assert result.decision is ApprovalStatus.APPROVED
    assert result.decided_by == "bob"
    assert result.decided_at == now
    assert result.version_id == "ver-1"


def test_an_approval_comment_is_optional(now: datetime) -> None:
    assert decision(now, comment=None).comment == ""


def test_a_rejection_needs_a_reason(now: datetime) -> None:
    with pytest.raises(ValidationFailure) as error:
        decision(now, approve=False, comment="  ")

    assert error.value.details["field"] == "comment"


def test_a_rejection_with_a_reason_is_recorded(now: datetime) -> None:
    result = decision(now, approve=False, comment="drift too high")

    assert result.decision is ApprovalStatus.REJECTED
    assert result.comment == "drift too high"


def test_the_registrant_cannot_approve_their_own_version(now: datetime) -> None:
    with pytest.raises(SeparationOfDuties) as error:
        decision(now, decided_by="alice")

    assert error.value.code == "SEPARATION_OF_DUTIES"


def test_even_an_admin_must_respect_separation_of_duties(now: datetime) -> None:
    with pytest.raises(SeparationOfDuties):
        decision(now, decided_by="alice", roles=[Role.ADMIN])


def test_the_registrant_cannot_reject_their_own_version_either(now: datetime) -> None:
    with pytest.raises(SeparationOfDuties):
        decision(now, decided_by="alice", approve=False, comment="changed my mind")


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ML_ENGINEER, Role.RELEASE_MANAGER])
def test_only_approvers_and_admins_can_decide(now: datetime, role: Role) -> None:
    with pytest.raises(Forbidden):
        decision(now, roles=[role])


def test_an_admin_who_is_not_the_registrant_can_approve(now: datetime) -> None:
    assert decision(now, roles=[Role.ADMIN]).decision is ApprovalStatus.APPROVED


@pytest.mark.parametrize(
    "stage", [Stage.DRAFT, Stage.APPROVED, Stage.STAGING, Stage.PRODUCTION, Stage.ARCHIVED]
)
def test_only_a_validated_version_can_be_decided(now: datetime, stage: Stage) -> None:
    with pytest.raises(InvalidTransition) as error:
        decision(now, version_stage=stage)

    assert error.value.details["required_stage"] == "VALIDATED"


def test_permission_is_checked_before_separation_of_duties(now: datetime) -> None:
    with pytest.raises(Forbidden) as error:
        decision(now, decided_by="alice", roles=[Role.VIEWER])

    assert not isinstance(error.value, SeparationOfDuties)
