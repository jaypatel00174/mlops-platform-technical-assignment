"""What the deployment application layer needs from the outside world.

Storage and the model runtime are Protocols so the use cases can be tested with the real SQL
repositories over SQLite and a scripted runtime, and so a real runtime (Kubernetes, SageMaker,
a serving gateway) is a new adapter rather than a change to the use cases (ADR-001).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from mlops.deployment.domain.entities import Deployment, DeploymentAttempt, EnvironmentBinding
from mlops.deployment.domain.idempotency import IdempotencyRecord
from mlops.deployment.domain.outbox import OutboxEvent
from mlops.deployment.domain.runtime import RuntimeTarget
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus
from mlops.kernel.ports import AuditRecorder
from mlops.registry.application.services import RegistryService


class ModelRuntime(Protocol):
    """The platform-agnostic seam to wherever models actually run.

    Adapters raise the RuntimeFailure family (RuntimeTimeout, RuntimeUnavailable,
    RuntimeRejected); the domain classifies those into retryable and permanent failures.
    """

    def supports(self, runtime_type: str) -> bool:
        """True if this runtime can host models of that type (rollback compatibility check)."""
        ...

    def verify_artifact(self, artifact_uri: str, artifact_digest: str) -> bool:
        """True if the artifact is reachable and matches its recorded digest."""
        ...

    def deploy(self, target: RuntimeTarget) -> str:
        """Make the version serve traffic in the environment. Returns a runtime reference.

        Must be idempotent: deploying the same target twice leaves one running copy, because the
        platform delivers work at least once.
        """
        ...


class DeploymentRepository(Protocol):
    def add(self, deployment: Deployment) -> None: ...

    def get(self, deployment_id: str) -> Deployment | None: ...

    def search(
        self,
        *,
        model_id: str | None = None,
        environment: str | None = None,
        status: DeploymentStatus | None = None,
        limit: int = 50,
    ) -> list[Deployment]: ...

    def find_in_flight(self, model_id: str, environment: str) -> Deployment | None: ...

    def save(self, deployment: Deployment) -> Deployment: ...

    def has_succeeded(self, model_id: str, version: str, environment: str) -> bool: ...

    def find_stale(
        self, statuses: frozenset[DeploymentStatus], *, before: datetime, limit: int = 50
    ) -> list[Deployment]: ...

    def record_attempt_started(
        self, deployment_id: str, attempt_no: int, now: datetime
    ) -> None: ...

    def record_attempt_finished(
        self,
        deployment_id: str,
        attempt_no: int,
        *,
        outcome: AttemptOutcome,
        now: datetime,
        error: str | None = None,
        runtime_ref: str | None = None,
    ) -> None: ...

    def attempts(self, deployment_id: str) -> list[DeploymentAttempt]: ...


class BindingRepository(Protocol):
    def get(self, model_id: str, environment: str) -> EnvironmentBinding | None: ...

    def save(self, binding: EnvironmentBinding, now: datetime) -> EnvironmentBinding: ...

    def list_for_environment(self, environment: str) -> list[EnvironmentBinding]: ...


class OutboxRepository(Protocol):
    def add(self, event: OutboxEvent) -> None: ...

    def release(self, event: OutboxEvent, *, worker_id: str) -> None: ...

    def open_deployment_ids(self) -> set[str]: ...


class OutboxClaimer(Protocol):
    """Claims due events across tenants, in its own short transaction."""

    def claim(
        self, worker_id: str, now: datetime, lease: timedelta, limit: int
    ) -> list[OutboxEvent]: ...


class IdempotencyRepository(Protocol):
    def get(self, actor: str, key: str) -> IdempotencyRecord | None: ...

    def add(self, record: IdempotencyRecord) -> None: ...

    def purge_expired(self, now: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class DeploymentContext:
    """Everything one transaction can touch. Committed together or not at all."""

    tenant_id: str
    deployments: DeploymentRepository
    bindings: BindingRepository
    outbox: OutboxRepository
    idempotency: IdempotencyRepository
    registry: RegistryService
    audit: AuditRecorder


class TransactionRunner(Protocol):
    """Opens a transaction for one tenant. Used by the worker and the reconciler."""

    def transaction(self, tenant_id: str) -> AbstractContextManager[DeploymentContext]: ...


# Event types carried by the outbox.
DEPLOYMENT_REQUESTED = "deployment.requested"

JsonObject = Mapping[str, object]
