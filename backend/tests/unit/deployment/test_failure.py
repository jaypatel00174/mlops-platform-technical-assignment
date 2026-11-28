from __future__ import annotations

import pytest

from mlops.deployment.domain.failure import (
    FailureClass,
    RetryPolicy,
    classify,
    retry_delay_seconds,
    should_retry_automatically,
)
from mlops.kernel.errors import (
    Forbidden,
    InvalidTransition,
    PolicyViolation,
    RuntimeFailure,
    RuntimeRejected,
    RuntimeTimeout,
    RuntimeUnavailable,
    StaleVersion,
    ValidationFailure,
)

F = FailureClass


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PolicyViolation("not approved"), F.POLICY),
        (Forbidden("no"), F.POLICY),
        (InvalidTransition("bad stage"), F.POLICY),
        (ValidationFailure("artifact missing"), F.VALIDATION),
        (RuntimeTimeout("slow"), F.TRANSIENT),
        (RuntimeUnavailable("503"), F.TRANSIENT),
        (StaleVersion("lost a race"), F.TRANSIENT),
        (TimeoutError("socket"), F.TRANSIENT),
        (ConnectionError("reset"), F.TRANSIENT),
        (RuntimeRejected("unsupported model format"), F.PERMANENT_RUNTIME),
        (RuntimeFailure("something odd"), F.PERMANENT_RUNTIME),
        (RuntimeError("bug"), F.INTERNAL),
        (KeyError("oops"), F.INTERNAL),
    ],
)
def test_errors_are_classified_into_failure_classes(
    error: Exception, expected: FailureClass
) -> None:
    assert classify(error) is expected


def test_an_unknown_runtime_failure_is_not_retried_automatically() -> None:
    """Conservative default: only failures known to be transient are retried without a human."""
    assert classify(RuntimeFailure("unknown")) is F.PERMANENT_RUNTIME


@pytest.mark.parametrize(
    ("failure_class", "attempt", "expected"),
    [
        (F.TRANSIENT, 1, True),
        (F.TRANSIENT, 2, True),
        (F.TRANSIENT, 3, False),  # budget of 3 attempts exhausted
        (F.POLICY, 1, False),
        (F.VALIDATION, 1, False),
        (F.PERMANENT_RUNTIME, 1, False),
        (F.INTERNAL, 1, False),
    ],
)
def test_only_transient_failures_within_budget_retry_automatically(
    failure_class: FailureClass, attempt: int, expected: bool
) -> None:
    policy = RetryPolicy(max_attempts=3)

    assert should_retry_automatically(failure_class, attempt, policy) is expected


@pytest.mark.parametrize(
    ("attempt", "jitter", "expected"),
    [
        (1, 0.0, 1.0),  # base 2s, half of it is the floor with equal jitter
        (1, 1.0, 2.0),
        (2, 0.0, 2.0),
        (2, 1.0, 4.0),
        (3, 1.0, 8.0),
        (20, 1.0, 300.0),  # capped
        (20, 0.0, 150.0),
    ],
)
def test_retry_delay_grows_exponentially_with_equal_jitter_and_a_cap(
    attempt: int, jitter: float, expected: float
) -> None:
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=2.0, max_delay_seconds=300.0)

    assert retry_delay_seconds(attempt, policy, jitter) == pytest.approx(expected)


@pytest.mark.parametrize(("attempt", "jitter"), [(0, 0.5), (-1, 0.5), (1, -0.1), (1, 1.1)])
def test_retry_delay_rejects_out_of_range_inputs(attempt: int, jitter: float) -> None:
    with pytest.raises(ValidationFailure):
        retry_delay_seconds(attempt, RetryPolicy(), jitter)


def test_delays_never_decrease_between_attempts_for_the_same_jitter() -> None:
    policy = RetryPolicy(max_attempts=10)

    delays = [retry_delay_seconds(n, policy, 0.5) for n in range(1, 10)]

    assert delays == sorted(delays)
