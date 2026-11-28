"""Deployment status as an explicit transition table.

REQUESTED -> VALIDATING -> DEPLOYING -> SUCCEEDED -> ROLLED_BACK
                 |            |
                 +--> FAILED <+        FAILED -> REQUESTED (retry, a new attempt)
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from mlops.kernel.errors import InvalidTransition


class DeploymentStatus(StrEnum):
    REQUESTED = "REQUESTED"
    VALIDATING = "VALIDATING"
    DEPLOYING = "DEPLOYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class DeploymentType(StrEnum):
    DEPLOY = "DEPLOY"
    ROLLBACK = "ROLLBACK"


class AttemptOutcome(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# Statuses covered by the partial unique index: at most one per (tenant, model, environment).
IN_FLIGHT = frozenset(
    {DeploymentStatus.REQUESTED, DeploymentStatus.VALIDATING, DeploymentStatus.DEPLOYING}
)

# Statuses where a client can stop polling. FAILED can still be retried by an operator.
SETTLED = frozenset(
    {DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK}
)

_ALLOWED: Mapping[DeploymentStatus, frozenset[DeploymentStatus]] = {
    DeploymentStatus.REQUESTED: frozenset({DeploymentStatus.VALIDATING}),
    DeploymentStatus.VALIDATING: frozenset({DeploymentStatus.DEPLOYING, DeploymentStatus.FAILED}),
    DeploymentStatus.DEPLOYING: frozenset({DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED}),
    DeploymentStatus.SUCCEEDED: frozenset({DeploymentStatus.ROLLED_BACK}),
    DeploymentStatus.FAILED: frozenset({DeploymentStatus.REQUESTED}),
    DeploymentStatus.ROLLED_BACK: frozenset(),
}


def allowed_targets(current: DeploymentStatus) -> frozenset[DeploymentStatus]:
    return _ALLOWED[current]


def can_transition(current: DeploymentStatus, target: DeploymentStatus) -> bool:
    return target in _ALLOWED[current]


def transition(current: DeploymentStatus, target: DeploymentStatus) -> DeploymentStatus:
    if not can_transition(current, target):
        raise InvalidTransition(
            f"a deployment cannot move from {current.value} to {target.value}",
            current=current.value,
            target=target.value,
            allowed=sorted(status.value for status in _ALLOWED[current]),
        )
    return target
