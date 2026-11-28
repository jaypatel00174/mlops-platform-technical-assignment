"""The concurrency guards of ADR-003, proven against the real schema."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.domain.entities import Deployment, EnvironmentBinding
from mlops.deployment.domain.failure import FailureClass
from mlops.deployment.domain.idempotency import IdempotencyRecord
from mlops.deployment.domain.outbox import OutboxEvent, OutboxStatus
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from mlops.kernel.errors import AlreadyExists, DeploymentConflict, NotFound, StaleVersion
from support.builders import NOW, at, make_deployment, persist_model_and_version

pytestmark = pytest.mark.integration


def committed_with(session: Session, *deployments: Deployment) -> SqlDeploymentRepository:
    """A model with one version, plus the given deployments, all committed."""
    persist_model_and_version(session, versions=("1.0.0", "2.0.0"))
    repo = SqlDeploymentRepository(session)
    for deployment in deployments:
        repo.add(deployment)
    session.commit()
    return repo


def run_to_success(repo: SqlDeploymentRepository, deployment: Deployment) -> Deployment:
    saved = repo.save(deployment.begin_validation(at(1)))
    saved = repo.save(saved.begin_deploying(at(2)))
    return repo.save(saved.succeed(at(3)))


# ----------------------------------------------------------------------------- deployments


def test_a_deployment_survives_a_round_trip(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        committed_with(session, make_deployment("dep-1"))

    with session_factory() as session:
        loaded = SqlDeploymentRepository(session).get("dep-1")

    assert loaded is not None
    assert loaded.status is DeploymentStatus.REQUESTED
    assert loaded.created_at == NOW
    assert loaded.idempotency_key == "key-dep-1-12345"
    assert loaded.row_version == 1


def test_a_deployment_of_an_unknown_version_raises_not_found(session: Session) -> None:
    persist_model_and_version(session)
    session.commit()

    with pytest.raises(NotFound):
        SqlDeploymentRepository(session).add(make_deployment("dep-1", version="9.9.9"))


def test_scenario_8_a_second_in_flight_deployment_for_the_same_model_and_environment_conflicts(
    session: Session,
) -> None:
    repo = committed_with(session, make_deployment("dep-1"))

    with pytest.raises(DeploymentConflict) as error:
        repo.add(make_deployment("dep-2", version="1.0.0"))

    assert error.value.code == "DEPLOYMENT_CONFLICT"
    assert error.value.details["existing_deployment_id"] == "dep-1"


def test_the_conflict_leaves_the_session_usable_and_the_first_deployment_intact(
    session: Session,
) -> None:
    repo = committed_with(session, make_deployment("dep-1"))

    with pytest.raises(DeploymentConflict):
        repo.add(make_deployment("dep-2"))

    assert [d.id for d in repo.search()] == ["dep-1"]


def test_another_environment_does_not_conflict(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1", environment="production"))

    repo.add(make_deployment("dep-2", environment="staging"))

    assert {d.id for d in repo.search()} == {"dep-1", "dep-2"}


def test_another_model_does_not_conflict(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))
    persist_model_and_version(session, model_id="valve-health-model")
    session.commit()

    repo.add(make_deployment("dep-2", model_id="valve-health-model"))

    assert {d.id for d in repo.search()} == {"dep-1", "dep-2"}


def test_a_settled_deployment_does_not_block_a_new_one(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))
    run_to_success(repo, repo.get("dep-1"))  # type: ignore[arg-type]
    session.commit()

    repo.add(make_deployment("dep-2", version="1.0.0"))

    assert repo.find_in_flight("pump-failure-predictor", "production") is not None


def test_a_failed_deployment_does_not_block_a_new_one(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))
    first = repo.get("dep-1")
    assert first is not None
    repo.save(first.begin_validation(at(1)).fail(FailureClass.TRANSIENT, "timeout", at(2)))
    session.commit()

    repo.add(make_deployment("dep-2"))

    assert repo.find_in_flight("pump-failure-predictor", "production") is not None


def test_retrying_conflicts_when_a_newer_deployment_is_already_in_flight(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))
    first = repo.get("dep-1")
    assert first is not None
    failed = repo.save(first.begin_validation(at(1)).fail(FailureClass.TRANSIENT, "timeout", at(2)))
    repo.add(make_deployment("dep-2"))
    session.commit()

    with pytest.raises(DeploymentConflict) as error:
        repo.save(failed.retry(at(5)))

    assert error.value.details["existing_deployment_id"] == "dep-2"


def test_duplicate_deployment_ids_are_refused(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1", environment="staging"))

    with pytest.raises(AlreadyExists):
        repo.add(make_deployment("dep-1", environment="production"))


def test_state_changes_are_compare_and_swap(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        committed_with(session, make_deployment("dep-1"))

    with session_factory() as first, session_factory() as second:
        seen_by_first = SqlDeploymentRepository(first).get("dep-1")
        seen_by_second = SqlDeploymentRepository(second).get("dep-1")
        assert seen_by_first is not None
        assert seen_by_second is not None

        SqlDeploymentRepository(second).save(seen_by_second.begin_validation(at(1)))
        second.commit()

        with pytest.raises(StaleVersion):
            SqlDeploymentRepository(first).save(seen_by_first.begin_validation(at(2)))


def test_failure_details_are_stored(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))
    first = repo.get("dep-1")
    assert first is not None

    repo.save(first.begin_validation(at(1)).fail(FailureClass.POLICY, "not approved", at(2)))
    session.commit()
    loaded = repo.get("dep-1")

    assert loaded is not None
    assert loaded.status is DeploymentStatus.FAILED
    assert loaded.failure_class is FailureClass.POLICY
    assert loaded.error == "not approved"


def test_listing_filters_and_returns_newest_first(session: Session) -> None:
    repo = committed_with(
        session,
        make_deployment("dep-1", environment="dev", now=at(1)),
        make_deployment("dep-2", environment="staging", now=at(2)),
        make_deployment("dep-3", environment="production", now=at(3)),
    )

    assert [d.id for d in repo.search()] == ["dep-3", "dep-2", "dep-1"]
    assert [d.id for d in repo.search(environment="staging")] == ["dep-2"]
    assert [d.id for d in repo.search(status=DeploymentStatus.FAILED)] == []
    assert [d.id for d in repo.search(model_id="pump-failure-predictor", limit=2)] == [
        "dep-3",
        "dep-2",
    ]


def test_one_tenant_cannot_see_anothers_deployments(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))

    assert repo.get("dep-1") is not None
    assert SqlDeploymentRepository(session, "tenant-b").get("dep-1") is None
    assert SqlDeploymentRepository(session, "tenant-b").search() == []


# --------------------------------------------------------------------------------- attempts


def test_attempts_are_recorded_before_and_finished_after_the_external_call(
    session: Session,
) -> None:
    repo = committed_with(session, make_deployment("dep-1"))

    repo.record_attempt_started("dep-1", 1, at(1))
    started = repo.attempts("dep-1")
    repo.record_attempt_finished(
        "dep-1", 1, outcome=AttemptOutcome.FAILED, now=at(2), error="runtime timeout"
    )
    finished = repo.attempts("dep-1")

    assert [a.outcome for a in started] == [AttemptOutcome.STARTED]
    assert started[0].finished_at is None
    assert [a.outcome for a in finished] == [AttemptOutcome.FAILED]
    assert finished[0].finished_at == at(2)
    assert finished[0].error == "runtime timeout"


def test_attempts_are_ordered_and_unique_per_deployment(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))
    repo.record_attempt_started("dep-1", 1, at(1))
    repo.record_attempt_finished("dep-1", 1, outcome=AttemptOutcome.FAILED, now=at(2))
    repo.record_attempt_started("dep-1", 2, at(3))
    session.commit()

    assert [a.attempt_no for a in repo.attempts("dep-1")] == [1, 2]
    with pytest.raises(AlreadyExists):
        repo.record_attempt_started("dep-1", 2, at(4))


def test_an_attempt_for_an_unknown_deployment_is_refused(session: Session) -> None:
    with pytest.raises(NotFound):
        SqlDeploymentRepository(session).record_attempt_started("ghost", 1, at(1))


def test_finishing_an_attempt_that_never_started_is_refused(session: Session) -> None:
    repo = committed_with(session, make_deployment("dep-1"))

    with pytest.raises(NotFound):
        repo.record_attempt_finished("dep-1", 7, outcome=AttemptOutcome.SUCCEEDED, now=at(1))


# ---------------------------------------------------------------------------------- bindings


def test_a_binding_is_created_on_first_success_and_tracks_the_previous_deployment(
    session: Session,
) -> None:
    deployments = committed_with(
        session,
        make_deployment("dep-1", environment="staging"),
        make_deployment("dep-2", environment="production"),
    )
    bindings = SqlEnvironmentBindingRepository(session)
    assert bindings.get("pump-failure-predictor", "production") is None

    first = bindings.save(
        EnvironmentBinding(
            model_id="pump-failure-predictor", environment="production"
        ).after_success("dep-1"),
        at(1),
    )
    second = bindings.save(first.after_success("dep-2"), at(2))
    session.commit()
    reloaded = bindings.get("pump-failure-predictor", "production")

    assert deployments is not None
    assert first.row_version == 1
    assert second.row_version == 2
    assert reloaded is not None
    assert reloaded.current_deployment_id == "dep-2"
    assert reloaded.previous_deployment_id == "dep-1"


def test_two_promotions_cannot_both_win_the_binding(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        committed_with(
            session,
            make_deployment("dep-1", environment="staging"),
            make_deployment("dep-2", environment="dev"),
            make_deployment("dep-3", environment="production"),
        )
        SqlEnvironmentBindingRepository(session).save(
            EnvironmentBinding(
                model_id="pump-failure-predictor", environment="production"
            ).after_success("dep-1"),
            at(1),
        )
        session.commit()

    with session_factory() as first, session_factory() as second:
        seen_by_first = SqlEnvironmentBindingRepository(first).get(
            "pump-failure-predictor", "production"
        )
        seen_by_second = SqlEnvironmentBindingRepository(second).get(
            "pump-failure-predictor", "production"
        )
        assert seen_by_first is not None
        assert seen_by_second is not None

        SqlEnvironmentBindingRepository(second).save(seen_by_second.after_success("dep-2"), at(2))
        second.commit()

        with pytest.raises(StaleVersion):
            SqlEnvironmentBindingRepository(first).save(seen_by_first.after_success("dep-3"), at(3))


def test_a_binding_for_an_unknown_deployment_is_refused(session: Session) -> None:
    persist_model_and_version(session)
    session.commit()
    binding = EnvironmentBinding(model_id="pump-failure-predictor", environment="production")

    with pytest.raises(NotFound):
        SqlEnvironmentBindingRepository(session).save(binding.after_success("ghost"), at(1))


def test_live_environments_lists_where_a_version_is_currently_deployed(session: Session) -> None:
    committed_with(
        session,
        make_deployment("dep-1", version="2.0.0", environment="production"),
        make_deployment("dep-2", version="2.0.0", environment="staging"),
        make_deployment("dep-3", version="1.0.0", environment="dev"),
    )
    bindings = SqlEnvironmentBindingRepository(session)
    for deployment_id, environment in (
        ("dep-1", "production"),
        ("dep-2", "staging"),
        ("dep-3", "dev"),
    ):
        bindings.save(
            EnvironmentBinding(
                model_id="pump-failure-predictor", environment=environment
            ).after_success(deployment_id),
            at(1),
        )
    session.commit()

    assert bindings.live_environments("pump-failure-predictor", "2.0.0") == [
        "production",
        "staging",
    ]
    assert bindings.live_environments("pump-failure-predictor", "1.0.0") == ["dev"]
    assert bindings.live_environments("pump-failure-predictor", "3.0.0") == []


# ------------------------------------------------------------------------------ idempotency


def record(
    key: str = "key-12345678", actor: str = "carol", **overrides: object
) -> IdempotencyRecord:
    values: dict[str, object] = {
        "key": key,
        "actor": actor,
        "request_hash": "h" * 64,
        "response_status": 202,
        "response_body": {"deployment_id": "dep-1"},
        "now": NOW,
    }
    values.update(overrides)
    return IdempotencyRecord.create(**values)  # type: ignore[arg-type]


def test_an_idempotency_record_survives_a_round_trip(session: Session) -> None:
    repo = SqlIdempotencyRepository(session)
    repo.add(record())
    session.commit()

    loaded = repo.get("carol", "key-12345678")

    assert loaded == record()
    assert repo.get("carol", "other-key-1234") is None


def test_the_same_key_is_unique_per_actor_and_tenant(session: Session) -> None:
    SqlIdempotencyRepository(session).add(record())
    session.commit()

    SqlIdempotencyRepository(session).add(record(actor="dave"))
    SqlIdempotencyRepository(session, "tenant-b").add(record(tenant_id="tenant-b"))
    session.commit()

    with pytest.raises(AlreadyExists):
        SqlIdempotencyRepository(session).add(record())


def test_expired_records_are_purged_and_live_ones_are_kept(session: Session) -> None:
    repo = SqlIdempotencyRepository(session)
    repo.add(record("old-key-12345", ttl=timedelta(hours=1)))
    repo.add(record("fresh-key-1234", ttl=timedelta(hours=48)))
    session.commit()

    purged = repo.purge_expired(NOW + timedelta(hours=24))
    session.commit()

    assert purged == 1
    assert repo.get("carol", "old-key-12345") is None
    assert repo.get("carol", "fresh-key-1234") is not None


# ----------------------------------------------------------------------------------- outbox


def test_an_outbox_event_is_stored_with_a_versioned_payload(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(
        OutboxEvent.create(
            id="evt-1", type="deployment.requested", payload={"deployment_id": "dep-1"}, now=NOW
        )
    )
    session.commit()

    loaded = repo.get("evt-1")

    assert loaded is not None
    assert loaded.status is OutboxStatus.PENDING
    assert loaded.attempts == 0
    assert loaded.payload == {"schema_version": 1, "deployment_id": "dep-1"}
    assert loaded.available_at == NOW
    assert repo.get("missing") is None


def test_outbox_events_are_counted_by_status_and_ids_are_unique(session: Session) -> None:
    repo = SqlOutboxRepository(session)
    repo.add(OutboxEvent.create(id="evt-1", type="t", payload={}, now=NOW))
    repo.add(OutboxEvent.create(id="evt-2", type="t", payload={}, now=NOW))
    session.commit()

    assert repo.count_by_status() == {"PENDING": 2}
    with pytest.raises(AlreadyExists):
        repo.add(OutboxEvent.create(id="evt-1", type="t", payload={}, now=NOW))
