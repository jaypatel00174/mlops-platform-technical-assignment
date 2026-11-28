"""Environment promotion controls: who may deploy what, where, and under which conditions.

The application layer gathers the facts (version stage, approval, whether the version already
succeeded in the prior environment) from the registry and deployment modules and passes them
in. The rules themselves are pure and unit-tested.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from mlops.governance.domain.rbac import action_for_deployment, require
from mlops.kernel import vocabulary
from mlops.kernel.errors import PolicyViolation, ValidationFailure
from mlops.kernel.vocabulary import ApprovalStatus, Role, Stage


@dataclass(frozen=True, slots=True)
class EnvironmentPolicy:
    environment: str
    requires_approval: bool
    # The environment the same version must already have succeeded in, for example staging
    # before production. None means no prior environment is required.
    required_prior_environment: str | None = None


DEFAULT_POLICIES: Mapping[str, EnvironmentPolicy] = {
    vocabulary.DEV: EnvironmentPolicy(vocabulary.DEV, requires_approval=False),
    vocabulary.STAGING: EnvironmentPolicy(vocabulary.STAGING, requires_approval=True),
    vocabulary.PRODUCTION: EnvironmentPolicy(
        vocabulary.PRODUCTION,
        requires_approval=True,
        required_prior_environment=vocabulary.STAGING,
    ),
}


@dataclass(frozen=True, slots=True)
class DeploymentFacts:
    """What the policy needs to know about the version being deployed."""

    stage: Stage
    approval_status: ApprovalStatus
    prior_environment_succeeded: bool


def policy_for(
    environment: str, policies: Mapping[str, EnvironmentPolicy] = DEFAULT_POLICIES
) -> EnvironmentPolicy:
    try:
        return policies[environment]
    except KeyError:
        raise ValidationFailure(
            f"unknown environment {environment!r}",
            field="environment",
            value=environment,
            known=sorted(policies),
        ) from None


def check_deployment_allowed(
    *,
    environment: str,
    roles: Collection[Role],
    facts: DeploymentFacts,
    policies: Mapping[str, EnvironmentPolicy] = DEFAULT_POLICIES,
) -> EnvironmentPolicy:
    """Raise unless `roles` may deploy a version with these `facts` to `environment`.

    Order matters: an unknown environment is a validation error, then permission is checked, so a
    caller without permission learns nothing about the state of a version; only then do the
    promotion rules run.
    """
    policy_for(environment, policies)
    require(roles, action_for_deployment(environment))
    return check_promotion_rules(environment=environment, facts=facts, policies=policies)


def check_promotion_rules(
    *,
    environment: str,
    facts: DeploymentFacts,
    policies: Mapping[str, EnvironmentPolicy] = DEFAULT_POLICIES,
) -> EnvironmentPolicy:
    """The promotion rules alone, with no caller. The worker repeats these before it acts,
    because the version may have been rejected or archived since the request was accepted."""
    policy = policy_for(environment, policies)

    if facts.stage is Stage.ARCHIVED:
        raise PolicyViolation(
            "an archived version cannot be deployed", reason="ARCHIVED", environment=environment
        )
    if facts.approval_status is ApprovalStatus.REJECTED:
        raise PolicyViolation(
            "a rejected version cannot be deployed", reason="REJECTED", environment=environment
        )
    if policy.requires_approval and facts.approval_status is not ApprovalStatus.APPROVED:
        raise PolicyViolation(
            f"the version is not approved and {environment} requires approval",
            reason="NOT_APPROVED",
            environment=environment,
        )
    prior = policy.required_prior_environment
    if prior is not None and not facts.prior_environment_succeeded:
        raise PolicyViolation(
            f"the version must succeed in {prior} before it can go to {environment}",
            reason="PRIOR_ENVIRONMENT_NOT_SUCCEEDED",
            environment=environment,
            required_environment=prior,
        )
    return policy
