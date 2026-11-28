from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from mlops.deployment.domain.entities import Deployment, EnvironmentBinding
from mlops.deployment.domain.failure import FailureClass
from mlops.deployment.domain.status import DeploymentStatus, DeploymentType
from mlops.kernel.errors import InvalidTransition, ValidationFailure

S = DeploymentStatus


def requested(now: datetime, **overrides: object) -> Deployment:
    values: dict[str, object] = {
        "id": "dep-1",
        "model_id": "pump-failure-predictor",
        "version": "2.0.0",
        "environment": "production",
        "requested_by": "carol",
        "correlation_id": "c-1",
        "now": now,
        "idempotency_key": "key-12345678",
    }
    values.update(overrides)
    return Deployment.request(**values)  # type: ignore[arg-type]


def test_a_new_request_is_requested_and_has_not_been_attempted(now: datetime) -> None:
    deployment = requested(now)

    assert deployment.status is S.REQUESTED
    assert deployment.type is DeploymentType.DEPLOY
    assert deployment.attempt == 0
    assert deployment.failure_class is None
    assert deployment.is_in_flight
    assert not deployment.is_settled
    assert deployment.created_at == deployment.updated_at == now


@pytest.mark.parametrize("field", ["model_id", "version", "environment", "requested_by"])
def test_required_request_fields_cannot_be_blank(now: datetime, field: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        requested(now, **{field: ""})

    assert error.value.details["field"] == field


def test_a_rollback_must_say_which_deployment_it_reverses(now: datetime) -> None:
    with pytest.raises(ValidationFailure) as error:
        requested(now, type=DeploymentType.ROLLBACK)

    assert error.value.details["field"] == "rollback_of"


def test_a_deploy_must_not_name_a_rollback_target(now: datetime) -> None:
    with pytest.raises(ValidationFailure):
        requested(now, rollback_of="dep-0")


def test_a_rollback_request_records_what_it_reverses(now: datetime) -> None:
    rollback = requested(now, type=DeploymentType.ROLLBACK, rollback_of="dep-0")

    assert rollback.type is DeploymentType.ROLLBACK
    assert rollback.rollback_of == "dep-0"


def test_the_happy_path_counts_one_attempt(now: datetime) -> None:
    t1, t2, t3 = (now + timedelta(seconds=n) for n in (1, 2, 3))

    done = requested(now).begin_validation(t1).begin_deploying(t2).succeed(t3)

    assert done.status is S.SUCCEEDED
    assert done.attempt == 1
    assert done.updated_at == t3
    assert done.is_settled
    assert not done.is_in_flight


def test_deployments_are_immutable(now: datetime) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        requested(now).status = S.SUCCEEDED  # type: ignore[misc]


def test_transitions_return_new_values_and_leave_the_original_untouched(now: datetime) -> None:
    original = requested(now)

    moved = original.begin_validation(now)

    assert original.status is S.REQUESTED
    assert moved.status is S.VALIDATING


@pytest.mark.parametrize("stage", ["validating", "deploying"])
def test_a_failure_records_class_and_error_from_either_in_flight_stage(
    now: datetime, stage: str
) -> None:
    deployment = requested(now).begin_validation(now)
    if stage == "deploying":
        deployment = deployment.begin_deploying(now)

    failed = deployment.fail(FailureClass.TRANSIENT, "runtime timed out after 30s", now)

    assert failed.status is S.FAILED
    assert failed.failure_class is FailureClass.TRANSIENT
    assert failed.error == "runtime timed out after 30s"
    assert failed.is_settled


def test_error_text_is_truncated_so_it_always_fits_the_column(now: datetime) -> None:
    failed = requested(now).begin_validation(now).fail(FailureClass.INTERNAL, "x" * 5000, now)

    assert failed.error is not None
    assert len(failed.error) == 2000


def test_a_requested_deployment_cannot_fail_directly(now: datetime) -> None:
    with pytest.raises(InvalidTransition):
        requested(now).fail(FailureClass.POLICY, "no", now)


def test_a_retry_creates_a_new_attempt_and_clears_the_failure(now: datetime) -> None:
    failed = requested(now).begin_validation(now).fail(FailureClass.TRANSIENT, "boom", now)

    retried = failed.retry(now)
    again = retried.begin_validation(now)

    assert retried.status is S.REQUESTED
    assert retried.failure_class is None
    assert retried.error is None
    assert retried.attempt == 1
    assert again.attempt == 2


def test_only_a_failed_deployment_can_be_retried(now: datetime) -> None:
    with pytest.raises(InvalidTransition):
        requested(now).retry(now)


def test_a_succeeded_deployment_cannot_be_retried(now: datetime) -> None:
    done = requested(now).begin_validation(now).begin_deploying(now).succeed(now)

    with pytest.raises(InvalidTransition):
        done.retry(now)


def test_a_succeeded_deployment_can_be_marked_rolled_back(now: datetime) -> None:
    done = requested(now).begin_validation(now).begin_deploying(now).succeed(now)

    assert done.mark_rolled_back(now).status is S.ROLLED_BACK


def test_a_failed_deployment_cannot_be_marked_rolled_back(now: datetime) -> None:
    failed = requested(now).begin_validation(now).fail(FailureClass.POLICY, "no", now)

    with pytest.raises(InvalidTransition):
        failed.mark_rolled_back(now)


# ------------------------------------------------------------------------------- binding


def test_a_binding_with_nothing_live_has_no_current_deployment() -> None:
    binding = EnvironmentBinding(model_id="m", environment="production")

    assert binding.current_deployment_id is None
    assert binding.previous_deployment_id is None
    assert not binding.is_live


def test_the_first_success_makes_a_deployment_current_with_no_previous() -> None:
    binding = EnvironmentBinding(model_id="m", environment="production").after_success("dep-1")

    assert binding.current_deployment_id == "dep-1"
    assert binding.previous_deployment_id is None
    assert binding.is_live


def test_each_success_pushes_the_current_deployment_to_previous() -> None:
    binding = (
        EnvironmentBinding(model_id="m", environment="production")
        .after_success("dep-1")
        .after_success("dep-2")
    )

    assert binding.current_deployment_id == "dep-2"
    assert binding.previous_deployment_id == "dep-1"


def test_recording_the_same_success_twice_is_idempotent() -> None:
    once = EnvironmentBinding(model_id="m", environment="production").after_success("dep-1")

    assert once.after_success("dep-1") == once
