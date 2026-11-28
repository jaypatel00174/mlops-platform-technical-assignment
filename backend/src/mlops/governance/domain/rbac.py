"""Role-based access control: which role may perform which action.

The matrix is data, so it is easy to review and to test exhaustively. The API layer turns the
token's roles into `Role` values and calls `require` before touching any use case.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from enum import StrEnum

from mlops.kernel import vocabulary
from mlops.kernel.errors import Forbidden
from mlops.kernel.vocabulary import Role


class Action(StrEnum):
    VIEW = "view"
    REGISTER_MODEL = "register_model"
    REGISTER_VERSION = "register_version"
    VALIDATE_VERSION = "validate_version"
    APPROVE_VERSION = "approve_version"
    DEPLOY_NON_PRODUCTION = "deploy_non_production"
    DEPLOY_PRODUCTION = "deploy_production"
    ROLLBACK_PRODUCTION = "rollback_production"
    ARCHIVE_VERSION = "archive_version"
    READ_AUDIT = "read_audit"
    INGEST_METRICS = "ingest_metrics"


PERMISSIONS: Mapping[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset({Action.VIEW}),
    Role.ML_ENGINEER: frozenset(
        {
            Action.VIEW,
            Action.REGISTER_MODEL,
            Action.REGISTER_VERSION,
            Action.VALIDATE_VERSION,
            Action.DEPLOY_NON_PRODUCTION,
        }
    ),
    Role.APPROVER: frozenset({Action.VIEW, Action.APPROVE_VERSION, Action.READ_AUDIT}),
    Role.RELEASE_MANAGER: frozenset(
        {
            Action.VIEW,
            Action.DEPLOY_NON_PRODUCTION,
            Action.DEPLOY_PRODUCTION,
            Action.ROLLBACK_PRODUCTION,
            Action.ARCHIVE_VERSION,
        }
    ),
    Role.ADMIN: frozenset(Action),
    # Machine identity used by metric agents. It can write metrics and nothing else.
    Role.SERVICE: frozenset({Action.INGEST_METRICS}),
}


def actions_for(roles: Collection[Role]) -> frozenset[Action]:
    granted: set[Action] = set()
    for role in roles:
        granted |= PERMISSIONS[role]
    return frozenset(granted)


def require(roles: Collection[Role], action: Action) -> None:
    """Raise Forbidden unless at least one held role grants `action`."""
    if action not in actions_for(roles):
        raise Forbidden(
            f"none of your roles may {action.value.replace('_', ' ')}",
            action=action.value,
            roles=sorted(role.value for role in roles),
        )


def action_for_deployment(environment: str) -> Action:
    if environment == vocabulary.PRODUCTION:
        return Action.DEPLOY_PRODUCTION
    return Action.DEPLOY_NON_PRODUCTION


def action_for_rollback(environment: str) -> Action:
    if environment == vocabulary.PRODUCTION:
        return Action.ROLLBACK_PRODUCTION
    return Action.DEPLOY_NON_PRODUCTION
