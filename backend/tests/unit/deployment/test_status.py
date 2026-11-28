from __future__ import annotations

import itertools

import pytest

from mlops.deployment.domain.status import (
    IN_FLIGHT,
    SETTLED,
    DeploymentStatus,
    allowed_targets,
    can_transition,
    transition,
)
from mlops.kernel.errors import InvalidTransition

R, V, D, S, F, B = (
    DeploymentStatus.REQUESTED,
    DeploymentStatus.VALIDATING,
    DeploymentStatus.DEPLOYING,
    DeploymentStatus.SUCCEEDED,
    DeploymentStatus.FAILED,
    DeploymentStatus.ROLLED_BACK,
)

# docs/architecture.md, "Deployment state machine"
ALLOWED = {(R, V), (V, D), (V, F), (D, S), (D, F), (F, R), (S, B)}
ALL_PAIRS = list(itertools.product(DeploymentStatus, DeploymentStatus))


@pytest.mark.parametrize(("current", "target"), ALL_PAIRS)
def test_transition_table_is_exactly_the_documented_one(
    current: DeploymentStatus, target: DeploymentStatus
) -> None:
    assert can_transition(current, target) is ((current, target) in ALLOWED)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transitions_return_the_target(
    current: DeploymentStatus, target: DeploymentStatus
) -> None:
    assert transition(current, target) is target


@pytest.mark.parametrize(("current", "target"), [pair for pair in ALL_PAIRS if pair not in ALLOWED])
def test_forbidden_transitions_raise_with_context(
    current: DeploymentStatus, target: DeploymentStatus
) -> None:
    with pytest.raises(InvalidTransition) as error:
        transition(current, target)

    assert error.value.details["current"] == current.value
    assert error.value.details["target"] == target.value
    assert error.value.details["allowed"] == sorted(s.value for s in allowed_targets(current))


def test_in_flight_statuses_are_the_ones_covered_by_the_unique_index() -> None:
    assert {R, V, D} == IN_FLIGHT


def test_settled_statuses_are_where_the_ui_stops_polling() -> None:
    assert {S, F, B} == SETTLED


def test_a_retry_is_the_only_way_out_of_failed() -> None:
    assert allowed_targets(F) == {R}


def test_a_rolled_back_deployment_is_final() -> None:
    assert allowed_targets(B) == frozenset()
