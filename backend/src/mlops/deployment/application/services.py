"""Deployment use cases that run inside an API request.

A request never touches the runtime. It records the intent (the deployment, an outbox event, the
audit entry and the idempotency record) in ONE transaction and returns 202; the worker does the
slow, failure-prone part later. Authorization is checked before any state is read, so a caller
without permission learns nothing about the version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mlops.deployment.application.ports import (
    DEPLOYMENT_REQUESTED,
    DeploymentContext,
    ModelRuntime,
)
from mlops.deployment.domain.entities import Deployment, DeploymentAttempt, EnvironmentBinding
from mlops.deployment.domain.idempotency import (
    IdempotencyRecord,
    Replay,
    decide,
    fingerprint,
    validate_key,
)
from mlops.deployment.domain.outbox import OutboxEvent
from mlops.deployment.domain.rollback import RollbackFacts, check_rollback_allowed
from mlops.deployment.domain.status import DeploymentStatus, DeploymentType
from mlops.governance.application.access import (
    authorize_deploy,
    authorize_rollback,
    required_prior_environment,
)
from mlops.governance.application.policy import ensure_deploy_allowed
from mlops.kernel.errors import DeploymentConflict, NotFound, UnsafeRollback
from mlops.kernel.identity import Principal
from mlops.kernel.ports import Clock, IdGenerator
from mlops.platform.context import get_correlation_id

__all__ = ["Accepted", "DeploymentService", "accepted_body"]

ACCEPTED = 202


@dataclass(frozen=True, slots=True)
class Accepted:
    """The outcome of asking for work: what to answer with, and whether it is a replay."""

    deployment_id: str
    body: dict[str, Any]
    replayed: bool


def accepted_body(deployment: Deployment) -> dict[str, Any]:
    """The 202 response body. Also what an idempotent replay returns, so it must stay JSON-safe."""
    return {
        "deployment_id": deployment.id,
        "status": deployment.status.value,
        "model_id": deployment.model_id,
        "version": deployment.version,
        "environment": deployment.environment,
        "type": deployment.type.value,
        "attempt": deployment.attempt,
    }


def _state(deployment: Deployment) -> dict[str, Any]:
    return {
        "status": deployment.status.value,
        "attempt": deployment.attempt,
        "failure_class": deployment.failure_class.value if deployment.failure_class else None,
    }


class DeploymentService:
    def __init__(
        self,
        *,
        context: DeploymentContext,
        runtime: ModelRuntime,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._ctx = context
        self._runtime = runtime
        self._clock = clock
        self._ids = ids

    # -- requesting work -------------------------------------------------------------------

    def request_deployment(
        self,
        principal: Principal,
        *,
        model_id: str,
        version: str,
        environment: str,
        idempotency_key: str,
    ) -> Accepted:
        key = validate_key(idempotency_key)
        now = self._clock.now()
        request_hash = fingerprint(
            {"model_id": model_id, "version": version, "environment": environment}
        )

        self._ctx.idempotency.purge_expired(now)
        stored = self._ctx.idempotency.get(principal.subject, key)
        if stored is not None and (
            decide(stored_hash=stored.request_hash, new_hash=request_hash) is Replay.REPLAY
        ):
            return Accepted(
                deployment_id=str(stored.response_body["deployment_id"]),
                body=dict(stored.response_body),
                replayed=True,
            )

        authorize_deploy(principal, environment)
        target = self._ctx.registry.get_version(model_id, version)
        prior = required_prior_environment(environment)
        ensure_deploy_allowed(
            principal,
            environment=environment,
            stage=target.stage,
            approval_status=target.approval_status,
            prior_environment_succeeded=(
                prior is None or self._ctx.deployments.has_succeeded(model_id, version, prior)
            ),
        )
        self._refuse_if_in_flight(model_id, environment)

        deployment = self._create(
            Deployment.request(
                id=self._ids.new_id("dep"),
                model_id=model_id,
                version=version,
                environment=environment,
                requested_by=principal.subject,
                correlation_id=get_correlation_id(),
                now=now,
                idempotency_key=key,
                tenant_id=self._ctx.tenant_id,
            ),
            audit_action="deployment.requested",
        )
        body = accepted_body(deployment)
        self._ctx.idempotency.add(
            IdempotencyRecord.create(
                key=key,
                actor=principal.subject,
                request_hash=request_hash,
                response_status=ACCEPTED,
                response_body=body,
                now=now,
                tenant_id=self._ctx.tenant_id,
            )
        )
        return Accepted(deployment_id=deployment.id, body=body, replayed=False)

    def retry(self, principal: Principal, deployment_id: str) -> Deployment:
        """Another attempt for a FAILED deployment. Any other status is an invalid transition."""
        deployment = self.get(deployment_id)
        authorize_deploy(principal, deployment.environment)
        now = self._clock.now()
        retried = self._ctx.deployments.save(deployment.retry(now))
        self._enqueue(retried)
        self._ctx.audit.record(
            actor=principal.subject,
            action="deployment.retried",
            entity_type="deployment",
            entity_id=retried.id,
            before=_state(deployment),
            after=_state(retried),
        )
        return retried

    def rollback(self, principal: Principal, deployment_id: str) -> Deployment:
        """Redeploy what was live before `deployment_id`, if every safety check passes.

        A rollback is a new deployment (type ROLLBACK) that goes through the same queue, so it is
        asynchronous, audited and retried like any other.
        """
        target = self.get(deployment_id)
        authorize_rollback(principal, target.environment)
        now = self._clock.now()
        target.mark_rolled_back(now)  # raises InvalidTransition unless it SUCCEEDED

        binding = self._ctx.bindings.get(target.model_id, target.environment)
        if binding is None or binding.current_deployment_id != target.id:
            raise UnsafeRollback(
                "only the deployment that is currently live can be rolled back",
                failed_checks=["NOT_CURRENT_DEPLOYMENT"],
                environment=target.environment,
            )
        previous = self._previous_of(binding)
        facts = self._rollback_facts(target, previous)
        check_rollback_allowed(facts)
        assert previous is not None  # noqa: S101  (check_rollback_allowed rejects None)

        rollback = self._create(
            Deployment.request(
                id=self._ids.new_id("dep"),
                model_id=previous.model_id,
                version=previous.version,
                environment=target.environment,
                requested_by=principal.subject,
                correlation_id=get_correlation_id(),
                now=now,
                type=DeploymentType.ROLLBACK,
                rollback_of=target.id,
                tenant_id=self._ctx.tenant_id,
            ),
            audit_action="deployment.rollback_requested",
        )
        return rollback

    # -- reading ---------------------------------------------------------------------------

    def get(self, deployment_id: str) -> Deployment:
        found = self._ctx.deployments.get(deployment_id)
        if found is None:
            raise NotFound(f"deployment {deployment_id!r} does not exist", id=deployment_id)
        return found

    def search(
        self,
        *,
        model_id: str | None = None,
        environment: str | None = None,
        status: DeploymentStatus | None = None,
        limit: int = 50,
    ) -> list[Deployment]:
        return self._ctx.deployments.search(
            model_id=model_id, environment=environment, status=status, limit=limit
        )

    def attempts(self, deployment_id: str) -> list[DeploymentAttempt]:
        self.get(deployment_id)
        return self._ctx.deployments.attempts(deployment_id)

    def bindings(self, environment: str) -> list[EnvironmentBinding]:
        return self._ctx.bindings.list_for_environment(environment)

    # -- internals -------------------------------------------------------------------------

    def _refuse_if_in_flight(self, model_id: str, environment: str) -> None:
        existing = self._ctx.deployments.find_in_flight(model_id, environment)
        if existing is not None:
            raise DeploymentConflict(
                "another deployment is already in progress for this model and environment",
                existing_deployment_id=existing.id,
                environment=environment,
            )

    def _create(self, deployment: Deployment, *, audit_action: str) -> Deployment:
        """Store the deployment, its outbox event and the audit entry (same transaction)."""
        self._ctx.deployments.add(deployment)
        stored = self._ctx.deployments.get(deployment.id)  # carries the stored row_version
        assert stored is not None  # noqa: S101
        self._enqueue(stored)
        self._ctx.audit.record(
            actor=stored.requested_by,
            action=audit_action,
            entity_type="deployment",
            entity_id=stored.id,
            after={
                **_state(stored),
                "model_id": stored.model_id,
                "version": stored.version,
                "environment": stored.environment,
                "type": stored.type.value,
            },
        )
        return stored

    def _enqueue(self, deployment: Deployment) -> None:
        self._ctx.outbox.add(
            OutboxEvent.create(
                id=self._ids.new_id("out"),
                type=DEPLOYMENT_REQUESTED,
                payload={
                    "deployment_id": deployment.id,
                    "correlation_id": deployment.correlation_id,
                },
                now=self._clock.now(),
                tenant_id=self._ctx.tenant_id,
            )
        )

    def _previous_of(self, binding: EnvironmentBinding) -> Deployment | None:
        if binding.previous_deployment_id is None:
            return None
        return self._ctx.deployments.get(binding.previous_deployment_id)

    def _rollback_facts(self, target: Deployment, previous: Deployment | None) -> RollbackFacts:
        in_flight = self._ctx.deployments.find_in_flight(target.model_id, target.environment)
        if previous is None:
            return RollbackFacts(
                environment=target.environment,
                previous_deployment_id=None,
                previous_status=None,
                target_stage=None,
                target_approval=None,
                artifact_digest_verified=False,
                runtime_compatible=False,
                in_flight_deployment_id=in_flight.id if in_flight else None,
            )
        version = self._ctx.registry.get_version(previous.model_id, previous.version)
        return RollbackFacts(
            environment=target.environment,
            previous_deployment_id=previous.id,
            previous_status=previous.status,
            target_stage=version.stage,
            target_approval=version.approval_status,
            artifact_digest_verified=self._runtime.verify_artifact(
                version.artifact_uri, version.artifact_digest
            ),
            runtime_compatible=self._runtime.supports(version.runtime_type),
            in_flight_deployment_id=in_flight.id if in_flight else None,
        )
