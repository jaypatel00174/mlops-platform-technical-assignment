"""Failure classification and retry policy.

The class decides what happens next: whether the platform retries by itself, who is paged, and
what an operator is told. Only failures known to be transient are retried without a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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


class FailureClass(StrEnum):
    POLICY = "POLICY"  # a promotion rule refused it
    VALIDATION = "VALIDATION"  # the request or artifact is invalid
    TRANSIENT = "TRANSIENT"  # timeouts, 5xx, lost races: worth retrying
    PERMANENT_RUNTIME = "PERMANENT_RUNTIME"  # the runtime refuses the model
    INTERNAL = "INTERNAL"  # a platform bug or infrastructure fault


def classify(error: BaseException) -> FailureClass:
    """Map an exception raised while deploying to a failure class.

    The order matters: specific runtime errors are checked before the generic RuntimeFailure,
    and anything unrecognised is INTERNAL so it is investigated rather than silently retried.
    """
    if isinstance(error, PolicyViolation | Forbidden | InvalidTransition):
        return FailureClass.POLICY
    if isinstance(error, ValidationFailure):
        return FailureClass.VALIDATION
    if isinstance(error, RuntimeTimeout | RuntimeUnavailable | StaleVersion):
        return FailureClass.TRANSIENT
    if isinstance(error, TimeoutError | ConnectionError):
        return FailureClass.TRANSIENT
    if isinstance(error, RuntimeRejected | RuntimeFailure):
        return FailureClass.PERMANENT_RUNTIME
    return FailureClass.INTERNAL


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 300.0


def should_retry_automatically(
    failure_class: FailureClass, attempt: int, policy: RetryPolicy
) -> bool:
    """True only for a transient failure while the attempt budget lasts."""
    return failure_class is FailureClass.TRANSIENT and attempt < policy.max_attempts


def retry_delay_seconds(attempt: int, policy: RetryPolicy, jitter: float) -> float:
    """Exponential backoff with "equal jitter": half fixed, half random.

    `attempt` is the attempt that just failed (1 for the first). `jitter` is a random number in
    [0, 1] supplied by the caller, which keeps this function deterministic and testable.
    """
    if attempt < 1:
        raise ValidationFailure("attempt must be at least 1", field="attempt", value=attempt)
    if not 0.0 <= jitter <= 1.0:
        raise ValidationFailure("jitter must be between 0 and 1", field="jitter", value=jitter)
    ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * 2.0 ** (attempt - 1))
    return ceiling / 2 + (ceiling / 2) * jitter
