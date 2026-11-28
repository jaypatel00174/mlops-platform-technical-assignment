"""The reconciler: repairs what a crash left half done.

The worker commits after every step, so a crash leaves a deployment in a state that is true but
stuck. Two kinds are repaired, each in its own transaction so one bad record cannot block the rest:

* VALIDATING or DEPLOYING with no progress for `stale_after`: the worker is gone. The attempt is
  closed as a transient failure and, while the attempt budget lasts, the deployment is queued
  again. Redeploying is safe because ModelRuntime.deploy is idempotent.
* REQUESTED with no pending event: the work note was lost. A new event is written.

`stale_after` must be longer than the worker's lease and any runtime call, otherwise a slow but
healthy worker is mistaken for a dead one. If that happens anyway, the worker's compare-and-swap
fails and it abandons its result, so state stays consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import structlog

from mlops.deployment.application.ports import DEPLOYMENT_REQUESTED, TransactionRunner
from mlops.deployment.domain.failure import FailureClass, RetryPolicy, should_retry_automatically
from mlops.deployment.domain.outbox import OutboxEvent
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus
from mlops.kernel.errors import StaleVersion
from mlops.kernel.ports import Clock, IdGenerator

RECONCILER_ACTOR = "system:reconciler"
_log = structlog.get_logger("mlops.reconciler")

_RUNNING = frozenset({DeploymentStatus.VALIDATING, DeploymentStatus.DEPLOYING})
_WAITING = frozenset({DeploymentStatus.REQUESTED})


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    recovered: int  # stuck mid-flight and failed over
    requeued: int  # waiting with no event, given a new one

    @property
    def changed(self) -> int:
        return self.recovered + self.requeued


class Reconciler:
    def __init__(
        self,
        *,
        runner: TransactionRunner,
        clock: Clock,
        ids: IdGenerator,
        stale_after: timedelta = timedelta(minutes=5),
        retry_policy: RetryPolicy | None = None,
        batch_size: int = 50,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._ids = ids
        self._stale_after = stale_after
        self._policy = retry_policy or RetryPolicy()
        self._batch_size = batch_size

    def reconcile(self, tenant_id: str) -> ReconcileReport:
        cutoff = self._clock.now() - self._stale_after
        with self._runner.transaction(tenant_id) as ctx:
            running = [
                d.id
                for d in ctx.deployments.find_stale(_RUNNING, before=cutoff, limit=self._batch_size)
            ]
            waiting = [
                d.id
                for d in ctx.deployments.find_stale(_WAITING, before=cutoff, limit=self._batch_size)
            ]

        recovered = sum(self._recover(tenant_id, deployment_id) for deployment_id in running)
        requeued = sum(self._requeue_if_lost(tenant_id, deployment_id) for deployment_id in waiting)
        if recovered or requeued:
            _log.info("reconciler.repaired", recovered=recovered, requeued=requeued)
        return ReconcileReport(recovered=recovered, requeued=requeued)

    def _recover(self, tenant_id: str, deployment_id: str) -> int:
        try:
            with self._runner.transaction(tenant_id) as ctx:
                deployment = ctx.deployments.get(deployment_id)
                if deployment is None or deployment.status not in _RUNNING:
                    return 0  # it finished while we were looking
                now = self._clock.now()
                message = "the worker stopped responding; the attempt was abandoned"
                failed = ctx.deployments.save(deployment.fail(FailureClass.TRANSIENT, message, now))
                ctx.deployments.record_attempt_finished(
                    failed.id,
                    failed.attempt,
                    outcome=AttemptOutcome.FAILED,
                    now=now,
                    error=message,
                )
                retried = should_retry_automatically(
                    FailureClass.TRANSIENT, failed.attempt, self._policy
                )
                final = ctx.deployments.save(failed.retry(now)) if retried else failed
                if retried:
                    ctx.outbox.add(
                        OutboxEvent.create(
                            id=self._ids.new_id("out"),
                            type=DEPLOYMENT_REQUESTED,
                            payload={
                                "deployment_id": final.id,
                                "correlation_id": final.correlation_id,
                            },
                            now=now,
                            tenant_id=tenant_id,
                        )
                    )
                ctx.audit.record(
                    actor=RECONCILER_ACTOR,
                    action="deployment.reconciled",
                    entity_type="deployment",
                    entity_id=final.id,
                    before={"status": deployment.status.value},
                    after={"status": final.status.value, "requeued": retried},
                )
                return 1
        except StaleVersion:
            return 0  # the worker made progress after all; leave it alone

    def _requeue_if_lost(self, tenant_id: str, deployment_id: str) -> int:
        with self._runner.transaction(tenant_id) as ctx:
            deployment = ctx.deployments.get(deployment_id)
            if deployment is None or deployment.status is not DeploymentStatus.REQUESTED:
                return 0
            if deployment_id in ctx.outbox.open_deployment_ids():
                return 0  # the work note exists; the worker will get to it
            now = self._clock.now()
            ctx.outbox.add(
                OutboxEvent.create(
                    id=self._ids.new_id("out"),
                    type=DEPLOYMENT_REQUESTED,
                    payload={
                        "deployment_id": deployment.id,
                        "correlation_id": deployment.correlation_id,
                    },
                    now=now,
                    tenant_id=tenant_id,
                )
            )
            ctx.audit.record(
                actor=RECONCILER_ACTOR,
                action="deployment.reconciled",
                entity_type="deployment",
                entity_id=deployment.id,
                before={"status": deployment.status.value},
                after={"status": deployment.status.value, "requeued": True},
            )
            return 1
