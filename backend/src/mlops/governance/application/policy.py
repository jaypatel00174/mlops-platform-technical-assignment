"""Promotion policy entry point for modules that may not import governance.domain."""

from __future__ import annotations

from mlops.governance.domain.policy import (
    DeploymentFacts,
    check_deployment_allowed,
    check_promotion_rules,
)
from mlops.kernel.identity import Principal
from mlops.kernel.vocabulary import ApprovalStatus, Stage

__all__ = ["ensure_deploy_allowed", "ensure_promotable"]


def ensure_deploy_allowed(
    principal: Principal,
    *,
    environment: str,
    stage: Stage,
    approval_status: ApprovalStatus,
    prior_environment_succeeded: bool,
) -> None:
    """Raise unless the caller may deploy this version here (permission first, then the rules)."""
    check_deployment_allowed(
        environment=environment,
        roles=principal.roles,
        facts=DeploymentFacts(stage, approval_status, prior_environment_succeeded),
    )


def ensure_promotable(
    *,
    environment: str,
    stage: Stage,
    approval_status: ApprovalStatus,
    prior_environment_succeeded: bool,
) -> None:
    """The same promotion rules without a caller. The worker repeats them before it acts."""
    check_promotion_rules(
        environment=environment,
        facts=DeploymentFacts(stage, approval_status, prior_environment_succeeded),
    )
