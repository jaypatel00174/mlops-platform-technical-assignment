from __future__ import annotations

import pytest

from mlops.governance.domain.rbac import (
    PERMISSIONS,
    Action,
    action_for_deployment,
    action_for_rollback,
    actions_for,
    require,
)
from mlops.kernel.errors import Forbidden
from mlops.kernel.vocabulary import Role

A = Action

# The documented permission matrix (docs/architecture.md, Security). Written out in full so any
# change to the real table has to be a deliberate change here too.
EXPECTED = {
    Role.VIEWER: {A.VIEW},
    Role.ML_ENGINEER: {
        A.VIEW,
        A.REGISTER_MODEL,
        A.REGISTER_VERSION,
        A.VALIDATE_VERSION,
        A.DEPLOY_NON_PRODUCTION,
    },
    Role.APPROVER: {A.VIEW, A.APPROVE_VERSION, A.READ_AUDIT},
    Role.RELEASE_MANAGER: {
        A.VIEW,
        A.DEPLOY_NON_PRODUCTION,
        A.DEPLOY_PRODUCTION,
        A.ROLLBACK_PRODUCTION,
        A.ARCHIVE_VERSION,
    },
    Role.SERVICE: {A.INGEST_METRICS},
}


@pytest.mark.parametrize("role", sorted(EXPECTED, key=str))
def test_each_role_has_exactly_its_documented_permissions(role: Role) -> None:
    assert PERMISSIONS[role] == frozenset(EXPECTED[role])


def test_admin_can_do_everything() -> None:
    assert PERMISSIONS[Role.ADMIN] == frozenset(Action)


def test_every_role_is_covered_by_the_matrix() -> None:
    assert set(PERMISSIONS) == set(Role)


def test_permissions_are_the_union_over_all_roles_held() -> None:
    held = actions_for([Role.VIEWER, Role.APPROVER])

    assert held == {A.VIEW, A.APPROVE_VERSION, A.READ_AUDIT}


def test_no_roles_means_no_permissions() -> None:
    assert actions_for([]) == frozenset()


def test_require_passes_when_any_role_grants_the_action() -> None:
    require([Role.VIEWER, Role.RELEASE_MANAGER], A.DEPLOY_PRODUCTION)


@pytest.mark.parametrize(
    ("roles", "action"),
    [
        ([Role.VIEWER], A.REGISTER_MODEL),
        ([Role.ML_ENGINEER], A.DEPLOY_PRODUCTION),
        ([Role.ML_ENGINEER], A.APPROVE_VERSION),
        ([Role.APPROVER], A.DEPLOY_NON_PRODUCTION),
        ([Role.RELEASE_MANAGER], A.APPROVE_VERSION),
        ([Role.SERVICE], A.VIEW),
        ([], A.VIEW),
    ],
)
def test_require_raises_forbidden_with_context(roles: list[Role], action: Action) -> None:
    with pytest.raises(Forbidden) as error:
        require(roles, action)

    assert error.value.code == "FORBIDDEN"
    assert error.value.details["action"] == action.value
    assert error.value.details["roles"] == sorted(role.value for role in roles)


@pytest.mark.parametrize(
    ("environment", "deploy", "rollback"),
    [
        ("production", A.DEPLOY_PRODUCTION, A.ROLLBACK_PRODUCTION),
        ("staging", A.DEPLOY_NON_PRODUCTION, A.DEPLOY_NON_PRODUCTION),
        ("dev", A.DEPLOY_NON_PRODUCTION, A.DEPLOY_NON_PRODUCTION),
    ],
)
def test_the_required_action_depends_on_the_environment(
    environment: str, deploy: Action, rollback: Action
) -> None:
    assert action_for_deployment(environment) is deploy
    assert action_for_rollback(environment) is rollback
