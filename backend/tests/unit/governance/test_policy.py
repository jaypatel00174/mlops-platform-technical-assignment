"""Promotion policy: who may deploy what, where. Acceptance scenario 3 lives here."""

from __future__ import annotations

import pytest

from mlops.governance.domain.policy import (
    DEFAULT_POLICIES,
    DeploymentFacts,
    check_deployment_allowed,
    policy_for,
)
from mlops.kernel.errors import Forbidden, PolicyViolation, ValidationFailure
from mlops.kernel.vocabulary import ApprovalStatus, Role, Stage

MANAGER = [Role.RELEASE_MANAGER]
ENGINEER = [Role.ML_ENGINEER]


def facts(
    stage: Stage = Stage.STAGING,
    approval: ApprovalStatus = ApprovalStatus.APPROVED,
    prior_succeeded: bool = True,
) -> DeploymentFacts:
    return DeploymentFacts(
        stage=stage, approval_status=approval, prior_environment_succeeded=prior_succeeded
    )


def test_default_policies_match_the_documented_promotion_controls() -> None:
    assert DEFAULT_POLICIES["dev"].requires_approval is False
    assert DEFAULT_POLICIES["dev"].required_prior_environment is None
    assert DEFAULT_POLICIES["staging"].requires_approval is True
    assert DEFAULT_POLICIES["staging"].required_prior_environment is None
    assert DEFAULT_POLICIES["production"].requires_approval is True
    assert DEFAULT_POLICIES["production"].required_prior_environment == "staging"


def test_an_unknown_environment_is_a_validation_error() -> None:
    with pytest.raises(ValidationFailure) as error:
        policy_for("moon-base")

    assert error.value.details["known"] == ["dev", "production", "staging"]


def test_an_approved_and_staged_version_can_go_to_production() -> None:
    policy = check_deployment_allowed(environment="production", roles=MANAGER, facts=facts())

    assert policy.environment == "production"


def test_scenario_3_an_unapproved_version_is_blocked_from_production() -> None:
    unapproved = facts(stage=Stage.VALIDATED, approval=ApprovalStatus.PENDING)

    with pytest.raises(PolicyViolation) as error:
        check_deployment_allowed(environment="production", roles=MANAGER, facts=unapproved)

    assert error.value.code == "POLICY_VIOLATION"
    assert error.value.details["reason"] == "NOT_APPROVED"
    assert error.value.details["environment"] == "production"


def test_production_requires_a_prior_successful_staging_deployment() -> None:
    with pytest.raises(PolicyViolation) as error:
        check_deployment_allowed(
            environment="production",
            roles=MANAGER,
            facts=facts(stage=Stage.APPROVED, prior_succeeded=False),
        )

    assert error.value.details["reason"] == "PRIOR_ENVIRONMENT_NOT_SUCCEEDED"
    assert error.value.details["required_environment"] == "staging"


def test_an_approved_version_can_be_deployed_to_staging_without_a_prior_environment() -> None:
    check_deployment_allowed(
        environment="staging",
        roles=ENGINEER,
        facts=facts(stage=Stage.APPROVED, prior_succeeded=False),
    )


def test_staging_still_requires_approval() -> None:
    with pytest.raises(PolicyViolation) as error:
        check_deployment_allowed(
            environment="staging",
            roles=ENGINEER,
            facts=facts(stage=Stage.VALIDATED, approval=ApprovalStatus.PENDING),
        )

    assert error.value.details["reason"] == "NOT_APPROVED"


def test_dev_is_not_governed_so_a_draft_can_be_deployed() -> None:
    check_deployment_allowed(
        environment="dev",
        roles=ENGINEER,
        facts=facts(stage=Stage.DRAFT, approval=ApprovalStatus.PENDING, prior_succeeded=False),
    )


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_an_archived_version_is_never_deployable(environment: str) -> None:
    with pytest.raises(PolicyViolation) as error:
        check_deployment_allowed(
            environment=environment, roles=[Role.ADMIN], facts=facts(stage=Stage.ARCHIVED)
        )

    assert error.value.details["reason"] == "ARCHIVED"


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_a_rejected_version_is_never_deployable(environment: str) -> None:
    rejected = facts(stage=Stage.VALIDATED, approval=ApprovalStatus.REJECTED)

    with pytest.raises(PolicyViolation) as error:
        check_deployment_allowed(environment=environment, roles=[Role.ADMIN], facts=rejected)

    assert error.value.details["reason"] == "REJECTED"


def test_an_ml_engineer_cannot_deploy_to_production() -> None:
    with pytest.raises(Forbidden):
        check_deployment_allowed(environment="production", roles=ENGINEER, facts=facts())


def test_a_viewer_cannot_deploy_anywhere() -> None:
    with pytest.raises(Forbidden):
        check_deployment_allowed(environment="dev", roles=[Role.VIEWER], facts=facts())


def test_permission_is_checked_before_policy_so_forbidden_callers_learn_nothing_else() -> None:
    unapproved = facts(stage=Stage.DRAFT, approval=ApprovalStatus.PENDING, prior_succeeded=False)

    with pytest.raises(Forbidden):
        check_deployment_allowed(environment="production", roles=ENGINEER, facts=unapproved)
