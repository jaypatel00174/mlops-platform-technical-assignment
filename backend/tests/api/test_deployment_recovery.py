"""What happens when things go wrong around the worker: crashes, duplicates, lost work, races.

The platform promises at-least-once delivery with idempotent handling, so none of these may
produce a deployment that is applied twice, lost, or left stuck.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.application.reconciler import Reconciler
from mlops.deployment.application.worker import DeploymentWorker
from mlops.deployment.domain.outbox import OutboxEvent
from mlops.deployment.domain.runtime import RuntimeTarget
from mlops.deployment.infrastructure.orm import OutboxEventRow
from mlops.deployment.infrastructure.repositories import SqlOutboxRepository
from mlops.deployment.infrastructure.simulated_runtime import SimulatedRuntime
from mlops.platform.config import Settings
from mlops.platform.runtime import UuidGenerator
from mlops.worker import build_worker
from support.api import PUMP, AdjustableClock, SequentialIds, version_payload
from support.deployments import (
    Headers,
    deployment,
    release_version,
    request_deployment,
    run_until_settled,
)

pytestmark = pytest.mark.integration

LEASE_SECONDS = 120
STALE_SECONDS = 300


class WorkerCrash(BaseException):
    """Stands in for a killed process: it unwinds without any clean-up running."""


class ScriptedRuntime(SimulatedRuntime):
    """A simulated runtime that counts calls and can run a hook in the middle of deploy()."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.before_deploy: Callable[[], None] | None = None

    def deploy(self, target: RuntimeTarget) -> str:
        self.calls += 1
        if self.before_deploy is not None:
            hook, self.before_deploy = self.before_deploy, None
            hook()
        return super().deploy(target)


@pytest.fixture
def scripted() -> ScriptedRuntime:
    return ScriptedRuntime()


@pytest.fixture
def pair(
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
    ids: SequentialIds,
    scripted: ScriptedRuntime,
) -> tuple[DeploymentWorker, Reconciler]:
    return build_worker(
        settings, session_factory, runtime=scripted, clock=clock, ids=ids, worker_id="worker-a"
    )


@pytest.fixture
def client(app_factory: Callable[..., Any], scripted: ScriptedRuntime) -> Any:
    with TestClient(app_factory(runtime=scripted), raise_server_exceptions=False) as test_client:
        yield test_client


def requested(client: TestClient, alice: Headers, bob: Headers) -> str:
    release_version(client, alice, bob, "2.0.0")
    return str(request_deployment(client, alice, "2.0.0").json()["deployment_id"])


def open_events(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        return SqlOutboxRepository(session).count_by_status()


# ------------------------------------------------------------------------- worker crashes


def test_a_worker_that_dies_mid_deployment_is_recovered_and_the_work_is_finished(
    client: TestClient,
    pair: tuple[DeploymentWorker, Reconciler],
    scripted: ScriptedRuntime,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    worker, reconciler = pair
    deployment_id = requested(client, alice, bob)

    def die() -> None:
        raise WorkerCrash

    scripted.before_deploy = die
    with pytest.raises(WorkerCrash):
        worker.run_once()

    stuck = deployment(client, alice, deployment_id)
    assert stuck["status"] == "DEPLOYING"  # the intent was committed before the crash

    # While the lease is alive nobody touches the event. Once it expires another worker takes it,
    # sees a deployment that is not REQUESTED and leaves it alone.
    clock.advance(seconds=LEASE_SECONDS + 1)
    assert worker.run_once() == 1
    assert deployment(client, alice, deployment_id)["status"] == "DEPLOYING"
    assert reconciler.reconcile("default").changed == 0  # not stale for long enough yet

    clock.advance(seconds=STALE_SECONDS)
    report = reconciler.reconcile("default")
    assert report.recovered == 1
    assert deployment(client, alice, deployment_id)["status"] == "REQUESTED"

    settled = run_until_settled(client, worker, clock, alice, deployment_id)
    assert settled["status"] == "SUCCEEDED"
    assert settled["attempt"] == 2
    body = client.get(f"/api/v1/deployments/{deployment_id}/events", headers=alice).json()
    assert [a["outcome"] for a in body["attempts"]] == ["FAILED", "SUCCEEDED"]
    assert "deployment.reconciled" in [e["action"] for e in body["events"]]


def test_a_worker_presumed_dead_cannot_overwrite_the_recovery(
    client: TestClient,
    pair: tuple[DeploymentWorker, Reconciler],
    scripted: ScriptedRuntime,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    worker, reconciler = pair
    deployment_id = requested(client, alice, bob)

    def reconciler_decides_the_worker_is_dead() -> None:
        clock.advance(seconds=STALE_SECONDS + 1)
        assert reconciler.reconcile("default").recovered == 1

    scripted.before_deploy = reconciler_decides_the_worker_is_dead
    worker.run_once()  # the slow worker finishes deploying after it was written off

    after_slow_worker = deployment(client, alice, deployment_id)
    assert after_slow_worker["status"] == "REQUESTED"  # the recovery stands; no overwrite
    assert after_slow_worker["attempt"] == 1

    settled = run_until_settled(client, worker, clock, alice, deployment_id)
    assert settled["status"] == "SUCCEEDED"
    assert settled["attempt"] == 2
    # Deploying is idempotent, so the runtime having been called twice is harmless.
    assert scripted.calls == 2
    assert scripted.deployed[("pump-failure-predictor", "staging")] == "2.0.0"


# ------------------------------------------------------------------- duplicates and lost work


def test_a_duplicate_event_does_not_deploy_twice(
    client: TestClient,
    pair: tuple[DeploymentWorker, Reconciler],
    scripted: ScriptedRuntime,
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    worker, _ = pair
    deployment_id = requested(client, alice, bob)
    with session_factory() as session:
        SqlOutboxRepository(session).add(
            OutboxEvent.create(
                id="out-duplicate",
                type="deployment.requested",
                payload={"deployment_id": deployment_id},
                now=clock.now(),
            )
        )
        session.commit()

    handled = worker.run_once()

    assert handled == 2
    assert scripted.calls == 1
    assert deployment(client, alice, deployment_id)["attempt"] == 1
    assert open_events(session_factory) == {"DONE": 2}


def test_an_event_for_an_unknown_deployment_is_closed_not_retried_forever(
    pair: tuple[DeploymentWorker, Reconciler],
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
) -> None:
    worker, _ = pair
    with session_factory() as session:
        SqlOutboxRepository(session).add(
            OutboxEvent.create(
                id="out-orphan",
                type="deployment.requested",
                payload={"deployment_id": "dep-gone"},
                now=clock.now(),
            )
        )
        session.commit()

    assert worker.run_once() == 1
    assert open_events(session_factory) == {"DONE": 1}


def test_a_lost_work_note_is_written_again_by_the_reconciler(
    client: TestClient,
    pair: tuple[DeploymentWorker, Reconciler],
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    worker, reconciler = pair
    deployment_id = requested(client, alice, bob)
    with session_factory() as session:
        session.execute(delete(OutboxEventRow))
        session.commit()
    assert worker.run_once() == 0  # nothing to do: the request is stuck

    clock.advance(seconds=STALE_SECONDS + 1)
    report = reconciler.reconcile("default")

    assert report.requeued == 1
    assert run_until_settled(client, worker, clock, alice, deployment_id)["status"] == "SUCCEEDED"


def test_the_reconciler_leaves_a_waiting_deployment_that_still_has_its_event(
    client: TestClient,
    pair: tuple[DeploymentWorker, Reconciler],
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    _, reconciler = pair
    requested(client, alice, bob)

    clock.advance(seconds=STALE_SECONDS + 1)

    assert reconciler.reconcile("default").changed == 0


def test_an_event_that_keeps_failing_is_parked_for_an_operator(
    pair: tuple[DeploymentWorker, Reconciler],
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
) -> None:
    worker, _ = pair
    with session_factory() as session:
        SqlOutboxRepository(session).add(
            OutboxEvent.create(
                id="out-poison",
                type="deployment.requested",
                payload={"deployment_id": "dep-x"},
                now=clock.now(),
            )
        )
        session.execute(update(OutboxEventRow).values(attempts=10))  # delivered 10 times already
        session.commit()

    worker.run_once()

    assert open_events(session_factory) == {"DEAD": 1}
    clock.advance(days=1)
    assert worker.run_once() == 0  # a dead event is never claimed again


def test_an_unexpected_error_is_internal_and_is_not_retried_automatically(
    client: TestClient,
    pair: tuple[DeploymentWorker, Reconciler],
    scripted: ScriptedRuntime,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    worker, _ = pair
    deployment_id = requested(client, alice, bob)

    def bug() -> None:
        raise RuntimeError("a defect nobody anticipated")

    scripted.before_deploy = bug

    settled = run_until_settled(client, worker, clock, alice, deployment_id)

    # Unknown errors are investigated by a person, not silently retried (docs/architecture.md).
    assert settled["status"] == "FAILED"
    assert settled["failure_class"] == "INTERNAL"
    assert settled["attempt"] == 1


# ----------------------------------------------------------------------------- concurrency


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs PostgreSQL for concurrent workers"
)
def test_three_workers_running_at_once_apply_each_deployment_exactly_once(
    settings: Settings,
    session_factory: sessionmaker[Session],
    client: TestClient,
    scripted: ScriptedRuntime,
    clock: AdjustableClock,
    alice: Headers,
) -> None:
    deployments = []
    for number in range(9):
        model_id = f"model-{number}"
        client.post("/api/v1/models", json={**PUMP, "id": model_id}, headers=alice)
        client.post(
            f"/api/v1/models/{model_id}/versions", json=version_payload("1.0.0"), headers=alice
        )
        accepted = client.post(
            "/api/v1/deployments",
            json={"model_id": model_id, "version": "1.0.0", "environment": "dev"},
            headers={**alice, "Idempotency-Key": f"key-model-{number:04d}"},
        )
        assert accepted.status_code == 202, accepted.text
        deployments.append(accepted.json()["deployment_id"])

    errors: list[BaseException] = []

    def work(name: str) -> None:
        worker, _ = build_worker(
            settings,
            session_factory,
            runtime=scripted,
            clock=clock,
            ids=UuidGenerator(),
            worker_id=name,
        )
        try:
            while worker.run_once():
                pass
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=work, args=(f"worker-{n}",)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    states = [deployment(client, alice, deployment_id) for deployment_id in deployments]
    assert [d["status"] for d in states] == ["SUCCEEDED"] * 9
    assert [d["attempt"] for d in states] == [1] * 9
    assert scripted.calls == 9  # no deployment was applied twice
    with session_factory() as session:
        statuses = session.scalars(select(OutboxEventRow.status)).all()
    assert set(statuses) == {"DONE"}
