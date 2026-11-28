"""Deployment and environment binding. Immutable values; every change returns a new instance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Self

from mlops.deployment.domain import status as statuses
from mlops.deployment.domain.failure import FailureClass
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus, DeploymentType
from mlops.kernel.errors import ValidationFailure

_MAX_ERROR_LENGTH = 2000


def _required(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValidationFailure(f"{field_name} must not be blank", field=field_name)
    return value.strip()


@dataclass(frozen=True, slots=True)
class Deployment:
    id: str
    model_id: str
    version: str
    environment: str
    type: DeploymentType
    status: DeploymentStatus
    # Number of attempts started. Incremented when validation begins, so a retry of attempt 1
    # becomes attempt 2.
    attempt: int
    requested_by: str
    correlation_id: str
    created_at: datetime
    updated_at: datetime
    failure_class: FailureClass | None = None
    error: str | None = None
    idempotency_key: str | None = None
    rollback_of: str | None = None  # the deployment this ROLLBACK reverses
    tenant_id: str = "default"
    # Managed by the persistence layer as an optimistic-lock counter (compare-and-swap).
    row_version: int = 0

    @classmethod
    def request(
        cls,
        *,
        id: str,
        model_id: str,
        version: str,
        environment: str,
        requested_by: str,
        correlation_id: str,
        now: datetime,
        idempotency_key: str | None = None,
        type: DeploymentType = DeploymentType.DEPLOY,
        rollback_of: str | None = None,
        tenant_id: str = "default",
    ) -> Self:
        if type is DeploymentType.ROLLBACK and not rollback_of:
            raise ValidationFailure(
                "a rollback must name the deployment it reverses", field="rollback_of"
            )
        if type is DeploymentType.DEPLOY and rollback_of:
            raise ValidationFailure(
                "only a rollback can name a deployment to reverse", field="rollback_of"
            )
        return cls(
            id=id,
            model_id=_required("model_id", model_id),
            version=_required("version", version),
            environment=_required("environment", environment),
            type=type,
            status=DeploymentStatus.REQUESTED,
            attempt=0,
            requested_by=_required("requested_by", requested_by),
            correlation_id=correlation_id,
            created_at=now,
            updated_at=now,
            idempotency_key=idempotency_key,
            rollback_of=rollback_of,
            tenant_id=tenant_id,
        )

    @property
    def is_in_flight(self) -> bool:
        return self.status in statuses.IN_FLIGHT

    @property
    def is_settled(self) -> bool:
        return self.status in statuses.SETTLED

    # -- transitions -----------------------------------------------------------------------

    def begin_validation(self, now: datetime) -> Self:
        return self._moved_to(DeploymentStatus.VALIDATING, now, attempt=self.attempt + 1)

    def begin_deploying(self, now: datetime) -> Self:
        return self._moved_to(DeploymentStatus.DEPLOYING, now)

    def succeed(self, now: datetime) -> Self:
        return self._moved_to(DeploymentStatus.SUCCEEDED, now, failure_class=None, error=None)

    def fail(self, failure_class: FailureClass, error: str, now: datetime) -> Self:
        return self._moved_to(
            DeploymentStatus.FAILED,
            now,
            failure_class=failure_class,
            error=error[:_MAX_ERROR_LENGTH],
        )

    def retry(self, now: datetime) -> Self:
        """Back to REQUESTED for a new attempt. Only a FAILED deployment can be retried."""
        return self._moved_to(DeploymentStatus.REQUESTED, now, failure_class=None, error=None)

    def mark_rolled_back(self, now: datetime) -> Self:
        return self._moved_to(DeploymentStatus.ROLLED_BACK, now)

    def _moved_to(self, target: DeploymentStatus, now: datetime, **changes: object) -> Self:
        return replace(
            self,
            status=statuses.transition(self.status, target),
            updated_at=now,
            **changes,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class DeploymentAttempt:
    """One try at a deployment. Committed before the external call, so intent is durable."""

    deployment_id: str
    attempt_no: int
    outcome: AttemptOutcome
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    runtime_ref: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentBinding:
    """What is live for a model in an environment, and what was live before it."""

    model_id: str
    environment: str
    current_deployment_id: str | None = None
    previous_deployment_id: str | None = None
    tenant_id: str = "default"
    # Managed by the persistence layer as an optimistic-lock counter (compare-and-swap).
    row_version: int = 0

    @property
    def is_live(self) -> bool:
        return self.current_deployment_id is not None

    def after_success(self, deployment_id: str) -> Self:
        """The deployment becomes current and the old current becomes previous. Idempotent."""
        if deployment_id == self.current_deployment_id:
            return self
        return replace(
            self,
            previous_deployment_id=self.current_deployment_id,
            current_deployment_id=deployment_id,
        )
