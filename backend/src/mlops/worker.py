"""The worker process:  python -m mlops.worker

Polls the outbox, runs deployments and, on a timer, repairs deployments a crash left half done.
Any number of these can run at once: events are claimed with row locks and every state change is
a compare-and-swap, so they never step on each other. SIGTERM and SIGINT finish the current event
and then exit, which is what a rolling restart wants.
"""

from __future__ import annotations

import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Sequence
from datetime import timedelta

import structlog
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.application.ports import ModelRuntime
from mlops.deployment.application.reconciler import Reconciler
from mlops.deployment.application.worker import DeploymentWorker
from mlops.deployment.domain.failure import RetryPolicy
from mlops.kernel.ports import Clock, IdGenerator
from mlops.platform.config import Settings, get_settings
from mlops.platform.db import make_engine, make_session_factory
from mlops.platform.logging import configure_logging
from mlops.platform.runtime import SystemClock, UuidGenerator
from mlops.wiring import SqlOutboxClaimer, SqlTransactionRunner, build_runtime

_log = structlog.get_logger("mlops.worker")


def build_worker(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    runtime: ModelRuntime | None = None,
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
    worker_id: str | None = None,
) -> tuple[DeploymentWorker, Reconciler]:
    clock = clock or SystemClock()
    ids = ids or UuidGenerator()
    policy = RetryPolicy(
        max_attempts=settings.retry_max_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
        max_delay_seconds=settings.retry_max_delay_seconds,
    )
    runner = SqlTransactionRunner(session_factory=session_factory, clock=clock, ids=ids)
    worker = DeploymentWorker(
        worker_id=worker_id or f"{socket.gethostname()}-{os.getpid()}",
        claimer=SqlOutboxClaimer(session_factory=session_factory),
        runner=runner,
        runtime=runtime or build_runtime(settings),
        clock=clock,
        retry_policy=policy,
        lease=timedelta(seconds=settings.worker_lease_seconds),
        batch_size=settings.worker_batch_size,
    )
    reconciler = Reconciler(
        runner=runner,
        clock=clock,
        ids=ids,
        stale_after=timedelta(seconds=settings.reconcile_stale_seconds),
        retry_policy=policy,
    )
    return worker, reconciler


def run_forever(
    worker: DeploymentWorker,
    reconciler: Reconciler,
    *,
    tenants: Sequence[str],
    poll_seconds: float,
    reconcile_interval_seconds: float,
    stop: threading.Event,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Poll until `stop` is set. An error in one pass is logged and the loop carries on."""
    next_reconcile = monotonic()
    while not stop.is_set():
        handled = 0
        try:
            handled = worker.run_once()
            if monotonic() >= next_reconcile:
                next_reconcile = monotonic() + reconcile_interval_seconds
                for tenant in tenants:
                    reconciler.reconcile(tenant)
        except Exception:
            _log.exception("worker.loop_error")
        if handled == 0:
            stop.wait(poll_seconds)


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    session_factory = make_session_factory(make_engine(settings.database_url))
    worker, reconciler = build_worker(settings, session_factory)

    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signum, _frame: stop.set())

    _log.info("worker.started", poll_seconds=settings.worker_poll_seconds)
    run_forever(
        worker,
        reconciler,
        tenants=[settings.default_tenant],
        poll_seconds=settings.worker_poll_seconds,
        reconcile_interval_seconds=settings.reconcile_interval_seconds,
        stop=stop,
    )
    _log.info("worker.stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
