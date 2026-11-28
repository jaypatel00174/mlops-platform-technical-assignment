from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from mlops.platform.db import Base
from support.database import downgrade, migrate

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "model",
    "model_version",
    "approval",
    "audit_event",
    "deployment",
    "deployment_attempt",
    "environment_binding",
    "outbox_event",
    "idempotency_record",
    "metric_sample",
}


def test_the_migration_creates_every_table(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_the_orm_models_and_the_migration_describe_the_same_schema(engine: Engine) -> None:
    """Guards against editing a model without writing the migration, or the other way round."""
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []


def test_downgrade_removes_everything_and_upgrade_restores_it(engine: Engine) -> None:
    downgrade(engine)
    assert EXPECTED_TABLES.isdisjoint(inspect(engine).get_table_names())

    migrate(engine)
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_upgrading_twice_is_a_no_op(engine: Engine) -> None:
    migrate(engine)

    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_one_in_flight_deployment_per_model_and_environment_is_a_unique_index(
    engine: Engine,
) -> None:
    indexes = {index["name"]: index for index in inspect(engine).get_indexes("deployment")}

    assert indexes["uq_deployment_in_flight"]["unique"]
    assert indexes["uq_deployment_in_flight"]["column_names"] == [
        "tenant_id",
        "model_id",
        "environment",
    ]


def _insert_audit_row(session: Session) -> None:
    session.execute(
        text(
            "INSERT INTO audit_event (id, tenant_id, actor, action, entity_type, entity_id, "
            "correlation_id, occurred_at) VALUES ('a1', 'default', 'bob', 'x', 'model', 'm', 'c', "
            "'2026-01-15 12:00:00')"
        )
    )
    session.commit()


def test_audit_events_cannot_be_updated(session: Session) -> None:
    _insert_audit_row(session)

    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("UPDATE audit_event SET actor = 'mallory' WHERE id = 'a1'"))


def test_audit_events_cannot_be_deleted(session: Session) -> None:
    _insert_audit_row(session)

    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM audit_event WHERE id = 'a1'"))


def test_the_database_rejects_a_stage_the_domain_does_not_know(session: Session) -> None:
    session.execute(
        text(
            "INSERT INTO model (id, tenant_id, name, owner, framework, tags, created_at, "
            "updated_at) VALUES ('m1', 'default', 'M', 'o', 'f', '[]', '2026-01-15 12:00:00', "
            "'2026-01-15 12:00:00')"
        )
    )

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO model_version (id, tenant_id, model_id, version, algorithm, "
                "artifact_uri, artifact_digest, training_data_ref, registered_by, stage, "
                "approval_status, runtime_type, metadata, tags, created_at, updated_at, "
                "row_version) VALUES ('v1', 'default', 'm1', '1.0.0', 'a', 'u', 'd', 't', 'r', "
                "'BOGUS', 'PENDING', 'generic', '{}', '[]', '2026-01-15 12:00:00', "
                "'2026-01-15 12:00:00', 1)"
            )
        )
