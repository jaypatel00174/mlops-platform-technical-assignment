"""Authorization entry point for other modules' API layers.

They may not import governance.domain, so the action vocabulary and the checks are exposed here.
"""

from __future__ import annotations

from mlops.governance.domain.policy import policy_for
from mlops.governance.domain.rbac import Action, action_for_deployment, action_for_rollback, require
from mlops.kernel.identity import Principal

__all__ = [
    "Action",
    "authorize",
    "authorize_deploy",
    "authorize_rollback",
    "required_prior_environment",
]


def authorize(principal: Principal, action: Action) -> None:
    """Raise Forbidden unless the caller's roles allow `action`."""
    require(principal.roles, action)


def authorize_deploy(principal: Principal, environment: str) -> None:
    """Unknown environment is a validation error; then the caller's permission is checked."""
    policy_for(environment)
    require(principal.roles, action_for_deployment(environment))


def authorize_rollback(principal: Principal, environment: str) -> None:
    policy_for(environment)
    require(principal.roles, action_for_rollback(environment))


def required_prior_environment(environment: str) -> str | None:
    """The environment a version must have succeeded in first (staging before production)."""
    return policy_for(environment).required_prior_environment
