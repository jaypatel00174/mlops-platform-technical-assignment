"""The version lifecycle is a table, not a flag. Every pair of stages is checked."""

from __future__ import annotations

import itertools

import pytest

from mlops.kernel.errors import InvalidTransition, PolicyViolation
from mlops.kernel.vocabulary import Stage
from mlops.registry.domain import lifecycle

D, V, A, S, P, X = (
    Stage.DRAFT,
    Stage.VALIDATED,
    Stage.APPROVED,
    Stage.STAGING,
    Stage.PRODUCTION,
    Stage.ARCHIVED,
)

# docs/architecture.md, "Lifecycle state machine"
ALLOWED = {(D, V), (D, X), (V, A), (V, X), (A, S), (A, X), (S, P), (S, X), (P, X)}
ALL_PAIRS = list(itertools.product(Stage, Stage))
FORBIDDEN = [pair for pair in ALL_PAIRS if pair not in ALLOWED]


@pytest.mark.parametrize(("current", "target"), ALL_PAIRS)
def test_transition_table_is_exactly_the_documented_one(current: Stage, target: Stage) -> None:
    assert lifecycle.can_transition(current, target) is ((current, target) in ALLOWED)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transition_returns_the_target(current: Stage, target: Stage) -> None:
    assert lifecycle.transition(current, target) is target


@pytest.mark.parametrize(("current", "target"), FORBIDDEN)
def test_forbidden_transition_raises_with_context(current: Stage, target: Stage) -> None:
    with pytest.raises(InvalidTransition) as error:
        lifecycle.transition(current, target)

    assert error.value.code == "INVALID_TRANSITION"
    assert error.value.details["current"] == current.value
    assert error.value.details["target"] == target.value
    allowed = sorted(stage.value for stage in lifecycle.allowed_targets(current))
    assert error.value.details["allowed"] == allowed


def test_archived_is_terminal() -> None:
    assert lifecycle.allowed_targets(Stage.ARCHIVED) == frozenset()


@pytest.mark.parametrize("stage", [Stage.DRAFT, Stage.VALIDATED, Stage.APPROVED, Stage.STAGING])
def test_a_version_that_is_not_live_can_be_archived(stage: Stage) -> None:
    assert lifecycle.archive(stage, live_environments=[]) is Stage.ARCHIVED


def test_a_production_version_can_be_archived_once_it_is_no_longer_live() -> None:
    assert lifecycle.archive(Stage.PRODUCTION, live_environments=[]) is Stage.ARCHIVED


def test_a_live_version_cannot_be_archived() -> None:
    with pytest.raises(PolicyViolation) as error:
        lifecycle.archive(Stage.PRODUCTION, live_environments=["production", "staging"])

    assert error.value.details["live_in"] == ["production", "staging"]


def test_archiving_twice_is_an_invalid_transition() -> None:
    with pytest.raises(InvalidTransition):
        lifecycle.archive(Stage.ARCHIVED, live_environments=[])


@pytest.mark.parametrize(
    ("current", "environment", "expected"),
    [
        (A, "staging", S),
        (S, "staging", S),  # redeploying is idempotent
        (P, "staging", P),  # an older production version redeployed to staging is not demoted
        (S, "production", P),
        (P, "production", P),  # rollback target is already PRODUCTION
        (D, "dev", D),  # non-governed environments never change the stage
        (V, "dev", V),
        (A, "dev", A),
    ],
)
def test_stage_after_a_successful_deployment(
    current: Stage, environment: str, expected: Stage
) -> None:
    assert lifecycle.stage_after_successful_deployment(current, environment) is expected


@pytest.mark.parametrize(
    ("current", "environment"),
    [
        (A, "production"),  # staging cannot be skipped
        (V, "production"),
        (V, "staging"),
        (D, "staging"),
        (X, "production"),
        (X, "staging"),
        (X, "dev"),
    ],
)
def test_a_deployment_cannot_move_a_version_through_an_illegal_stage(
    current: Stage, environment: str
) -> None:
    with pytest.raises(InvalidTransition):
        lifecycle.stage_after_successful_deployment(current, environment)
