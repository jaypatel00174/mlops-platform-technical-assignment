"""Rollback safety (docs/architecture.md section 7.3). Acceptance scenario 7 and risk R4."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mlops.deployment.domain.rollback import RollbackFacts, check_rollback_allowed
from mlops.deployment.domain.status import DeploymentStatus
from mlops.kernel.errors import DeploymentConflict, UnsafeRollback
from mlops.kernel.vocabulary import ApprovalStatus, Stage

SAFE = RollbackFacts(
    environment="production",
    previous_deployment_id="dep-1",
    previous_status=DeploymentStatus.SUCCEEDED,
    target_stage=Stage.PRODUCTION,
    target_approval=ApprovalStatus.APPROVED,
    artifact_digest_verified=True,
    runtime_compatible=True,
    in_flight_deployment_id=None,
)


def failed_checks(facts: RollbackFacts) -> list[str]:
    with pytest.raises(UnsafeRollback) as error:
        check_rollback_allowed(facts)
    assert error.value.code == "UNSAFE_ROLLBACK"
    checks: list[str] = error.value.details["failed_checks"]
    return checks


def test_a_rollback_with_every_precondition_met_is_allowed() -> None:
    check_rollback_allowed(SAFE)


def test_no_previous_deployment_means_there_is_nothing_to_roll_back_to() -> None:
    facts = replace(
        SAFE,
        previous_deployment_id=None,
        previous_status=None,
        target_stage=None,
        target_approval=None,
    )

    assert failed_checks(facts) == ["NO_PREVIOUS_DEPLOYMENT"]


@pytest.mark.parametrize(
    "status", [DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK, DeploymentStatus.DEPLOYING]
)
def test_the_previous_deployment_must_have_succeeded(status: DeploymentStatus) -> None:
    assert failed_checks(replace(SAFE, previous_status=status)) == [
        "PREVIOUS_DEPLOYMENT_NOT_SUCCEEDED"
    ]


def test_an_archived_target_version_is_refused() -> None:
    assert failed_checks(replace(SAFE, target_stage=Stage.ARCHIVED)) == ["TARGET_ARCHIVED"]


def test_a_rejected_target_version_is_refused() -> None:
    facts = replace(SAFE, target_approval=ApprovalStatus.REJECTED)

    assert failed_checks(facts) == ["TARGET_REJECTED"]


def test_a_target_version_that_was_never_approved_is_refused() -> None:
    facts = replace(SAFE, target_approval=ApprovalStatus.PENDING)

    assert failed_checks(facts) == ["TARGET_NOT_APPROVED"]


def test_an_artifact_whose_digest_no_longer_matches_is_refused() -> None:
    assert failed_checks(replace(SAFE, artifact_digest_verified=False)) == [
        "ARTIFACT_DIGEST_MISMATCH"
    ]


def test_a_runtime_that_cannot_host_the_target_version_is_refused() -> None:
    assert failed_checks(replace(SAFE, runtime_compatible=False)) == ["RUNTIME_INCOMPATIBLE"]


def test_all_failed_checks_are_reported_together() -> None:
    facts = replace(
        SAFE,
        previous_status=DeploymentStatus.ROLLED_BACK,
        target_stage=Stage.ARCHIVED,
        artifact_digest_verified=False,
        runtime_compatible=False,
    )

    assert failed_checks(facts) == [
        "PREVIOUS_DEPLOYMENT_NOT_SUCCEEDED",
        "TARGET_ARCHIVED",
        "ARTIFACT_DIGEST_MISMATCH",
        "RUNTIME_INCOMPATIBLE",
    ]


def test_another_deployment_in_flight_is_a_conflict_not_an_unsafe_rollback() -> None:
    with pytest.raises(DeploymentConflict) as error:
        check_rollback_allowed(replace(SAFE, in_flight_deployment_id="dep-7"))

    assert error.value.details["existing_deployment_id"] == "dep-7"


def test_unsafe_takes_precedence_over_conflict() -> None:
    facts = replace(SAFE, target_stage=Stage.ARCHIVED, in_flight_deployment_id="dep-7")

    with pytest.raises(UnsafeRollback):
        check_rollback_allowed(facts)
