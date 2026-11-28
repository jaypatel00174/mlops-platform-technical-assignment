"""The deployment worker: turns an outbox event into a finished deployment.

Each step commits before the next begins, and nothing is held open across the runtime call:

    txn A  REQUESTED -> VALIDATING, attempt recorded       (intent is durable before any work)
           re-check promotion rules, verify the artifact
    txn B  VALIDATING -> DEPLOYING
           runtime.deploy()                                 (no database transaction open)
    txn C  DEPLOYING -> SUCCEEDED, binding, stage, audit, event done   (all or nothing)
       or  DEPLOYING/VALIDATING -> FAILED, then a retry with backoff or a stop, event settled

Delivery is at least once. A crashed worker leaves its event leased; when the lease expires the
event is claimed again, finds the deployment no longer REQUESTED and does nothing. The reconciler
then repairs deployments that were left half way. Every step is a compare-and-swap on the row
version, so two workers can never both advance the same deployment.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import timedelta

import structlog

from mlops.deployment.application.ports import (
    DeploymentContext,
    ModelRuntime,
    OutboxClaimer,
    TransactionRunner,
)
from mlops.deployment.domain.entities import Deployment, EnvironmentBinding
from mlops.deployment.domain.failure import (
    FailureClass,
    RetryPolicy,
    classify,
    retry_delay_seconds,
    should_retry_automatically,
)
from mlops.deployment.domain.outbox import OutboxEvent
from mlops.deployment.domain.runtime import RuntimeTarget
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus, DeploymentType
from mlops.governance.application.access import required_prior_environment
from mlops.governance.application.policy import ensure_promotable
from mlops.kernel.errors import StaleVersion, ValidationFailure
from mlops.kernel.ports import Clock
from mlops.platform.context import reset_correlation_id, set_correlation_id

WORKER_ACTOR = "system:worker"
_log = structlog.get_logger("mlops.worker")
_BINDING_RETRIES = 3


class DeploymentWorker:
    def __init__(
        self,
        *,
        worker_id: str,
        claimer: OutboxClaimer,
        runner: TransactionRunner,
        runtime: ModelRuntime,
        clock: Clock,
        retry_policy: RetryPolicy | None = None,
        lease: timedelta = timedelta(seconds=120),
        batch_size: int = 10,
        max_deliveries: int = 10,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._worker_id = worker_id
        self._claimer = claimer
        self._runner = runner
        self._runtime = runtime
        self._clock = clock
        self._policy = retry_policy or RetryPolicy()
        self._lease = lease
        self._batch_size = batch_size
        self._max_deliveries = max_deliveries
        self._jitter = jitter

    # -- polling ---------------------------------------------------------------------------

    def run_once(self) -> int:
        """Claim due events and handle each. Returns how many were handled without an error.

        An unexpected error on one event is logged and leaves that event leased: it becomes
        claimable again when the lease expires, and after `max_deliveries` it is parked as DEAD.
        """
        events = self._claimer.claim(
            self._worker_id, self._clock.now(), self._lease, self._batch_size
        )
        handled = 0
        for event in events:
            token = set_correlation_id(str(event.payload.get("correlation_id", event.id)))
            try:
                self.handle(event)
                handled += 1
            except Exception:
                _log.exception("worker.event_failed", event_id=event.id, attempts=event.attempts)
            finally:
                reset_correlation_id(token)
        return handled

    # -- one event -------------------------------------------------------------------------

    def handle(self, event: OutboxEvent) -> None:
        deployment_id = str(event.payload.get("deployment_id", ""))

        if event.attempts > self._max_deliveries:
            self._park(event)
            return

        started = self._begin_attempt(event, deployment_id)
        if started is None:
            return  # nothing to do: unknown, duplicate or taken by another worker
        deployment, target = started

        try:
            self._validate(deployment, target)
            deployment = self._begin_deploying(deployment)
            runtime_ref = self._runtime.deploy(target)
        except StaleVersion:
            self._abandon(event, deployment_id)
            return
        except Exception as error:
            self._settle_failure(event, deployment, error)
            return

        try:
            self._settle_success(event, deployment, runtime_ref)
        except StaleVersion:
            # The reconciler or another worker moved the deployment on. Its outcome wins.
            self._abandon(event, deployment_id)

    # -- transaction A ---------------------------------------------------------------------

    def _begin_attempt(
        self, event: OutboxEvent, deployment_id: str
    ) -> tuple[Deployment, RuntimeTarget] | None:
        with self._runner.transaction(event.tenant_id) as ctx:
            deployment = ctx.deployments.get(deployment_id)
            if deployment is None:
                _log.warning("worker.unknown_deployment", deployment_id=deployment_id)
                self._release(ctx, event.complete())
                return None
            if deployment.status is not DeploymentStatus.REQUESTED:
                # Redelivery, or another worker is on it. Either way this event is spent.
                self._release(ctx, event.complete())
                return None

            now = self._clock.now()
            version = ctx.registry.get_version(deployment.model_id, deployment.version)
            try:
                moved = ctx.deployments.save(deployment.begin_validation(now))
            except StaleVersion:
                self._release(ctx, event.complete())
                return None
            ctx.deployments.record_attempt_started(moved.id, moved.attempt, now)
            target = RuntimeTarget(
                model_id=version.model_id,
                version=version.version,
                environment=deployment.environment,
                artifact_uri=version.artifact_uri,
                artifact_digest=version.artifact_digest,
                runtime_type=version.runtime_type,
            )
            return moved, target

    # -- validation (no transaction) -------------------------------------------------------

    def _validate(self, deployment: Deployment, target: RuntimeTarget) -> None:
        """Repeat the checks the request made: the version may have changed since."""
        with self._runner.transaction(deployment.tenant_id) as ctx:
            version = ctx.registry.get_version(deployment.model_id, deployment.version)
            prior = required_prior_environment(deployment.environment)
            ensure_promotable(
                environment=deployment.environment,
                stage=version.stage,
                approval_status=version.approval_status,
                prior_environment_succeeded=(
                    prior is None
                    or ctx.deployments.has_succeeded(deployment.model_id, deployment.version, prior)
                ),
            )
        if not self._runtime.verify_artifact(target.artifact_uri, target.artifact_digest):
            raise ValidationFailure(
                "the artifact is missing or does not match its recorded digest",
                artifact_uri=target.artifact_uri,
            )

    # -- transaction B ---------------------------------------------------------------------

    def _begin_deploying(self, deployment: Deployment) -> Deployment:
        with self._runner.transaction(deployment.tenant_id) as ctx:
            return ctx.deployments.save(deployment.begin_deploying(self._clock.now()))

    # -- transaction C ---------------------------------------------------------------------

    def _settle_success(self, event: OutboxEvent, deployment: Deployment, runtime_ref: str) -> None:
        with self._runner.transaction(deployment.tenant_id) as ctx:
            now = self._clock.now()
            succeeded = ctx.deployments.save(deployment.succeed(now))
            ctx.deployments.record_attempt_finished(
                succeeded.id,
                succeeded.attempt,
                outcome=AttemptOutcome.SUCCEEDED,
                now=now,
                runtime_ref=runtime_ref,
            )
            self._bind(ctx, succeeded)
            ctx.registry.promote_after_deployment(
                actor=WORKER_ACTOR,
                model_id=succeeded.model_id,
                version=succeeded.version,
                environment=succeeded.environment,
            )
            if succeeded.type is DeploymentType.ROLLBACK and succeeded.rollback_of:
                self._close_rolled_back(ctx, succeeded.rollback_of)
            ctx.audit.record(
                actor=WORKER_ACTOR,
                action="deployment.succeeded",
                entity_type="deployment",
                entity_id=succeeded.id,
                before={"status": deployment.status.value},
                after={"status": succeeded.status.value, "runtime_ref": runtime_ref},
            )
            self._release(ctx, event.complete())

    def _bind(self, ctx: DeploymentContext, deployment: Deployment) -> None:
        """Point the environment at this deployment. Compare-and-swap, so re-read on a lost race."""
        for attempt in range(_BINDING_RETRIES):
            current = ctx.bindings.get(deployment.model_id, deployment.environment)
            binding = (
                current or EnvironmentBinding(deployment.model_id, deployment.environment)
            ).after_success(deployment.id)
            try:
                ctx.bindings.save(binding, self._clock.now())
                return
            except StaleVersion:
                if attempt == _BINDING_RETRIES - 1:
                    raise

    def _close_rolled_back(self, ctx: DeploymentContext, rolled_back_id: str) -> None:
        original = ctx.deployments.get(rolled_back_id)
        if original is None or original.status is not DeploymentStatus.SUCCEEDED:
            return
        closed = ctx.deployments.save(original.mark_rolled_back(self._clock.now()))
        ctx.audit.record(
            actor=WORKER_ACTOR,
            action="deployment.rolled_back",
            entity_type="deployment",
            entity_id=closed.id,
            before={"status": original.status.value},
            after={"status": closed.status.value},
        )

    # -- failure ---------------------------------------------------------------------------

    def _settle_failure(self, event: OutboxEvent, deployment: Deployment, error: Exception) -> None:
        failure_class = classify(error)
        message = f"{type(error).__name__}: {error}"
        _log.warning(
            "worker.deployment_failed",
            deployment_id=deployment.id,
            failure_class=failure_class.value,
            attempt=deployment.attempt,
            error=message,
        )
        try:
            with self._runner.transaction(deployment.tenant_id) as ctx:
                now = self._clock.now()
                failed = ctx.deployments.save(deployment.fail(failure_class, message, now))
                ctx.deployments.record_attempt_finished(
                    failed.id,
                    failed.attempt,
                    outcome=AttemptOutcome.FAILED,
                    now=now,
                    error=failed.error,
                )
                ctx.audit.record(
                    actor=WORKER_ACTOR,
                    action="deployment.failed",
                    entity_type="deployment",
                    entity_id=failed.id,
                    before={"status": deployment.status.value},
                    after={
                        "status": failed.status.value,
                        "failure_class": failure_class.value,
                        "attempt": failed.attempt,
                    },
                )
                if should_retry_automatically(failure_class, failed.attempt, self._policy):
                    delay = retry_delay_seconds(failed.attempt, self._policy, self._jitter())
                    requeued = ctx.deployments.save(failed.retry(now))
                    ctx.audit.record(
                        actor=WORKER_ACTOR,
                        action="deployment.retry_scheduled",
                        entity_type="deployment",
                        entity_id=requeued.id,
                        before={"status": failed.status.value},
                        after={"status": requeued.status.value, "delay_seconds": round(delay, 1)},
                    )
                    self._release(ctx, event.reschedule(now + timedelta(seconds=delay)))
                else:
                    self._release(ctx, event.complete())
        except StaleVersion:
            self._abandon(event, deployment.id)

    # -- helpers ---------------------------------------------------------------------------

    def _release(self, ctx: DeploymentContext, released: OutboxEvent) -> None:
        ctx.outbox.release(released, worker_id=self._worker_id)

    def _abandon(self, event: OutboxEvent, deployment_id: str) -> None:
        """Another actor moved the deployment on; our result no longer applies."""
        _log.warning("worker.result_abandoned", deployment_id=deployment_id, event_id=event.id)
        try:
            with self._runner.transaction(event.tenant_id) as ctx:
                self._release(ctx, event.complete())
        except StaleVersion:
            pass  # the lease is gone too: somebody else owns this event now

    def _park(self, event: OutboxEvent) -> None:
        _log.error("worker.event_dead", event_id=event.id, attempts=event.attempts)
        with self._runner.transaction(event.tenant_id) as ctx:
            self._release(ctx, event.give_up())


__all__ = ["WORKER_ACTOR", "DeploymentWorker", "FailureClass"]
