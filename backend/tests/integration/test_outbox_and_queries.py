"""Outbox claiming and the queries behind the worker, the reconciler and the promotion gate."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.domain.entities import EnvironmentBinding
from mlops.deployment.domain.outbox import OutboxEvent, OutboxStatus
from mlops.deployment.domain.status import DeploymentStatus
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
    SqlOutboxRepository,
)
from mlops.kernel.errors import StaleVersion
from support.builders import NOW, at, make_deployment, persist_model_and_version

pytestmark = pytest.mark.integration

LEASE = timedelta(seconds=60)


def event(
    event_id: str, *, available_at_minutes: int = 0, deployment: str = "dep-1"
) -> OutboxEvent:
    return OutboxEvent.create(
        id=event_id,
        type="deployment.requested",
        payload={"deployment_id": deployment},
        now=NOW,
        available_at=at(available_at_minutes),
    )


# ------------------------------------------------------------------------------------ outbox


def test_claiming_takes_due_events_in_order_and_leaves_future_ones(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-b", available_at_minutes=0))
    repo.add(event("evt-a", available_at_minutes=-5))
    repo.add(event("evt-later", available_at_minutes=30))

    claimed = repo.claim_batch("worker-1", NOW, LEASE, limit=10)

    assert [e.id for e in claimed] == ["evt-a", "evt-b"]
    assert all(e.status is OutboxStatus.CLAIMED and e.locked_by == "worker-1" for e in claimed)
    assert repo.get("evt-later") is not None
    assert repo.get("evt-later").status is OutboxStatus.PENDING  # type: ignore[union-attr]


def test_a_batch_respects_the_limit(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    for number in range(5):
        repo.add(event(f"evt-{number}"))

    assert len(repo.claim_batch("worker-1", NOW, LEASE, limit=2)) == 2
    assert len(repo.claim_batch("worker-2", NOW, LEASE, limit=10)) == 3


def test_a_claimed_event_is_not_handed_to_a_second_worker(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-1"))
    repo.claim_batch("worker-1", NOW, LEASE)

    assert repo.claim_batch("worker-2", at(0), LEASE) == []


def test_an_event_whose_lease_expired_is_taken_over(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-1"))
    repo.claim_batch("worker-1", NOW, LEASE)

    taken = repo.claim_batch("worker-2", NOW + LEASE, LEASE)

    assert [(e.id, e.locked_by, e.attempts) for e in taken] == [("evt-1", "worker-2", 2)]


def test_the_lease_holder_can_complete_an_event(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-1"))
    (claimed,) = repo.claim_batch("worker-1", NOW, LEASE)

    repo.release(claimed.complete(), worker_id="worker-1")

    assert repo.count_by_status() == {"DONE": 1}
    assert repo.claim_batch("worker-2", at(60), LEASE) == []


def test_a_worker_that_lost_its_lease_cannot_release_the_event(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-1"))
    (first,) = repo.claim_batch("worker-1", NOW, LEASE)
    repo.claim_batch("worker-2", NOW + LEASE, LEASE)

    with pytest.raises(StaleVersion):
        repo.release(first.complete(), worker_id="worker-1")

    assert repo.get("evt-1").status is OutboxStatus.CLAIMED  # type: ignore[union-attr]


def test_a_rescheduled_event_waits_for_its_new_time(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-1"))
    (claimed,) = repo.claim_batch("worker-1", NOW, LEASE)

    repo.release(claimed.reschedule(at(10)), worker_id="worker-1")

    assert repo.claim_batch("worker-2", at(9), LEASE) == []
    assert [e.id for e in repo.claim_batch("worker-2", at(10), LEASE)] == ["evt-1"]


def test_open_deployment_ids_lists_pending_and_claimed_work_only(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(event("evt-1", deployment="dep-1"))
    repo.add(event("evt-2", deployment="dep-2"))
    repo.add(event("evt-3", deployment="dep-3", available_at_minutes=30))
    claimed = {e.id: e for e in repo.claim_batch("worker-1", NOW, LEASE, limit=2)}
    repo.release(claimed["evt-2"].complete(), worker_id="worker-1")

    assert repo.open_deployment_ids() == {"dep-1", "dep-3"}


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs PostgreSQL for row locking"
)
def test_two_workers_polling_at_once_never_take_the_same_event(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as setup:
        repo = SqlOutboxRepository(setup)
        for number in range(6):
            repo.add(event(f"evt-{number}"))
        setup.commit()

    first, second = session_factory(), session_factory()
    try:
        # Both transactions are open at the same time: the second must skip rows the first
        # has locked instead of waiting for them or claiming them again.
        taken_first = SqlOutboxRepository(first).claim_batch("worker-1", NOW, LEASE, limit=3)
        taken_second = SqlOutboxRepository(second).claim_batch("worker-2", NOW, LEASE, limit=3)
        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()

    ids = [e.id for e in taken_first] + [e.id for e in taken_second]
    assert len(ids) == 6
    assert len(set(ids)) == 6


# ---------------------------------------------------------------------------- other queries


def test_has_succeeded_only_counts_a_succeeded_deploy_in_that_environment(
    session: Session,
) -> None:
    persist_model_and_version(session)
    repo = SqlDeploymentRepository(session)
    repo.add(make_deployment("dep-1", environment="staging"))
    staging = repo.get("dep-1")
    assert staging is not None
    assert not repo.has_succeeded("pump-failure-predictor", "2.0.0", "staging")

    saved = repo.save(staging.begin_validation(at(1)))
    saved = repo.save(saved.begin_deploying(at(2)))
    repo.save(saved.succeed(at(3)))

    assert repo.has_succeeded("pump-failure-predictor", "2.0.0", "staging")
    assert not repo.has_succeeded("pump-failure-predictor", "2.0.0", "production")
    assert not repo.has_succeeded("pump-failure-predictor", "1.0.0", "staging")


def test_find_stale_returns_only_old_deployments_in_the_given_statuses(session: Session) -> None:
    persist_model_and_version(session, versions=("1.0.0", "2.0.0"))
    repo = SqlDeploymentRepository(session)
    repo.add(make_deployment("dep-old", version="1.0.0", environment="dev"))
    repo.add(make_deployment("dep-fresh", environment="staging"))
    old, fresh = repo.get("dep-old"), repo.get("dep-fresh")
    assert old is not None
    assert fresh is not None
    repo.save(old.begin_validation(at(1)))
    repo.save(fresh.begin_validation(at(50)))

    stale = repo.find_stale(
        frozenset({DeploymentStatus.VALIDATING, DeploymentStatus.DEPLOYING}), before=at(30)
    )

    assert [d.id for d in stale] == ["dep-old"]


def test_bindings_can_be_listed_per_environment(session: Session) -> None:
    persist_model_and_version(session)
    deployments = SqlDeploymentRepository(session)
    deployments.add(make_deployment("dep-1", environment="staging"))
    bindings = SqlEnvironmentBindingRepository(session)
    bindings.save(
        EnvironmentBinding(
            model_id="pump-failure-predictor",
            environment="staging",
            current_deployment_id="dep-1",
        ),
        NOW,
    )

    assert [b.model_id for b in bindings.list_for_environment("staging")] == [
        "pump-failure-predictor"
    ]
    assert bindings.list_for_environment("production") == []
