"""Request and response bodies of the deployment API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from mlops.deployment.domain.entities import (
    Deployment,
    DeploymentAttempt,
    EnvironmentBinding,
)


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(max_length=63, examples=["pump-failure-predictor"])
    version: str = Field(max_length=64, examples=["2.0.0"])
    environment: str = Field(max_length=32, examples=["staging"])


class AcceptedOut(BaseModel):
    """What the platform promises after a 202: the work is recorded, not yet done."""

    deployment_id: str
    status: str
    model_id: str
    version: str
    environment: str
    type: str
    attempt: int


class DeploymentOut(BaseModel):
    deployment_id: str
    model_id: str
    version: str
    environment: str
    type: str
    status: str
    attempt: int
    failure_class: str | None
    error: str | None
    requested_by: str
    rollback_of: str | None
    correlation_id: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, deployment: Deployment) -> Self:
        return cls(
            deployment_id=deployment.id,
            model_id=deployment.model_id,
            version=deployment.version,
            environment=deployment.environment,
            type=deployment.type.value,
            status=deployment.status.value,
            attempt=deployment.attempt,
            failure_class=deployment.failure_class.value if deployment.failure_class else None,
            error=deployment.error,
            requested_by=deployment.requested_by,
            rollback_of=deployment.rollback_of,
            correlation_id=deployment.correlation_id,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
        )


class DeploymentListOut(BaseModel):
    items: list[DeploymentOut]


class AttemptOut(BaseModel):
    attempt_no: int
    outcome: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    runtime_ref: str | None

    @classmethod
    def from_domain(cls, attempt: DeploymentAttempt) -> Self:
        return cls(
            attempt_no=attempt.attempt_no,
            outcome=attempt.outcome.value,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
            error=attempt.error,
            runtime_ref=attempt.runtime_ref,
        )


class TimelineEntryOut(BaseModel):
    occurred_at: datetime
    actor: str
    action: str
    details: dict[str, Any] | None


class TimelineOut(BaseModel):
    deployment: DeploymentOut
    attempts: list[AttemptOut]
    events: list[TimelineEntryOut]


class BindingOut(BaseModel):
    model_id: str
    environment: str
    current_deployment_id: str | None
    previous_deployment_id: str | None

    @classmethod
    def from_domain(cls, binding: EnvironmentBinding) -> Self:
        return cls(
            model_id=binding.model_id,
            environment=binding.environment,
            current_deployment_id=binding.current_deployment_id,
            previous_deployment_id=binding.previous_deployment_id,
        )


class BindingListOut(BaseModel):
    environment: str
    items: list[BindingOut]
