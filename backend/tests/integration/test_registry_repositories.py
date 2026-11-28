from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from mlops.kernel.errors import AlreadyExists, NotFound, StaleVersion
from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.registry.infrastructure.repositories import SqlModelRepository, SqlModelVersionRepository
from support.builders import at, make_model, make_version, persist_model_and_version

pytestmark = pytest.mark.integration


def test_a_model_survives_a_round_trip(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        SqlModelRepository(session).add(make_model())
        session.commit()

    with session_factory() as session:
        loaded = SqlModelRepository(session).get("pump-failure-predictor")

    assert loaded == make_model()
    assert loaded is not None
    assert loaded.created_at.tzinfo is not None


def test_models_are_listed_in_id_order_and_can_be_paged(session: Session) -> None:
    repo = SqlModelRepository(session)
    for model_id in ("valve-health-model", "compressor-anomaly-detector", "pump-failure-predictor"):
        repo.add(make_model(model_id))

    assert [m.id for m in repo.list_all()] == [
        "compressor-anomaly-detector",
        "pump-failure-predictor",
        "valve-health-model",
    ]
    assert [m.id for m in repo.list_all(limit=1, offset=1)] == ["pump-failure-predictor"]


def test_adding_a_duplicate_model_is_refused(session: Session) -> None:
    repo = SqlModelRepository(session)
    repo.add(make_model())
    session.commit()

    with pytest.raises(AlreadyExists) as error:
        repo.add(make_model())

    assert error.value.code == "ALREADY_EXISTS"
    assert error.value.details["model_id"] == "pump-failure-predictor"


def test_one_tenant_cannot_see_anothers_models(session: Session) -> None:
    SqlModelRepository(session, "tenant-a").add(make_model(tenant_id="tenant-a"))
    session.commit()

    assert SqlModelRepository(session, "tenant-a").get("pump-failure-predictor") is not None
    assert SqlModelRepository(session, "tenant-b").get("pump-failure-predictor") is None
    assert SqlModelRepository(session, "tenant-b").list_all() == []


def test_a_version_survives_a_round_trip_with_metadata_and_tags(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        persist_model_and_version(session)
        session.commit()

    with session_factory() as session:
        loaded = SqlModelVersionRepository(session).get("pump-failure-predictor", "2.0.0")

    assert loaded is not None
    assert loaded.row_version == 1
    assert replace(loaded, row_version=0) == make_version()
    assert loaded.metadata == {"accuracy": 0.93}
    assert loaded.stage is Stage.DRAFT


def test_versions_are_ordered_by_semantic_version_not_alphabetically(session: Session) -> None:
    persist_model_and_version(session, versions=("1.10.0", "2.0.0", "1.2.0"))

    ordered = SqlModelVersionRepository(session).list_for_model("pump-failure-predictor")

    assert [v.version for v in ordered] == ["1.2.0", "1.10.0", "2.0.0"]


def test_adding_a_version_to_an_unknown_model_raises_not_found(session: Session) -> None:
    with pytest.raises(NotFound) as error:
        SqlModelVersionRepository(session).add(make_version("ghost-model"))

    assert error.value.details["model_id"] == "ghost-model"


def test_registering_the_same_version_twice_is_refused(session: Session) -> None:
    persist_model_and_version(session)
    session.commit()

    with pytest.raises(AlreadyExists):
        SqlModelVersionRepository(session).add(make_version(id="another-id"))


def test_saving_a_lifecycle_change_persists_it_and_bumps_the_row_version(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        persist_model_and_version(session)
        session.commit()

    with session_factory() as session:
        repo = SqlModelVersionRepository(session)
        loaded = repo.get("pump-failure-predictor", "2.0.0")
        assert loaded is not None
        saved = repo.save(loaded.mark_validated(at(5)).mark_approved(at(6)))
        session.commit()

    with session_factory() as session:
        reloaded = SqlModelVersionRepository(session).get("pump-failure-predictor", "2.0.0")

    assert saved.row_version == 2
    assert reloaded is not None
    assert reloaded.stage is Stage.APPROVED
    assert reloaded.approval_status is ApprovalStatus.APPROVED
    assert reloaded.updated_at == at(6)


def test_a_concurrent_change_makes_the_second_save_fail_with_stale_version(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        persist_model_and_version(session)
        session.commit()

    with session_factory() as first, session_factory() as second:
        loaded_by_first = SqlModelVersionRepository(first).get("pump-failure-predictor", "2.0.0")
        loaded_by_second = SqlModelVersionRepository(second).get("pump-failure-predictor", "2.0.0")
        assert loaded_by_first is not None
        assert loaded_by_second is not None

        SqlModelVersionRepository(second).save(loaded_by_second.mark_validated(at(1)))
        second.commit()

        with pytest.raises(StaleVersion) as error:
            SqlModelVersionRepository(first).save(loaded_by_first.mark_validated(at(2)))

    assert error.value.code == "STALE_VERSION"


def test_a_save_based_on_an_old_snapshot_fails_even_in_a_fresh_session(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        persist_model_and_version(session)
        session.commit()
    with session_factory() as session:
        old_snapshot = SqlModelVersionRepository(session).get("pump-failure-predictor", "2.0.0")
    assert old_snapshot is not None

    with session_factory() as session:
        repo = SqlModelVersionRepository(session)
        current = repo.get("pump-failure-predictor", "2.0.0")
        assert current is not None
        repo.save(current.mark_validated(at(1)))
        session.commit()

    with session_factory() as session, pytest.raises(StaleVersion):
        SqlModelVersionRepository(session).save(old_snapshot.mark_validated(at(2)))


def test_saving_an_unknown_version_raises_not_found(session: Session) -> None:
    with pytest.raises(NotFound):
        SqlModelVersionRepository(session).save(make_version())


def test_the_database_itself_rejects_an_unknown_stage(session: Session) -> None:
    persist_model_and_version(session)

    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE model_version SET stage = 'BOGUS'"))
