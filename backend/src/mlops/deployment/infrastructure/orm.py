"""Tables owned by the deployment module."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from mlops.deployment.domain.outbox import OutboxStatus
from mlops.deployment.domain.status import (
    IN_FLIGHT,
    AttemptOutcome,
    DeploymentStatus,
    DeploymentType,
)
from mlops.platform.db import Base, JsonDocument, UTCDateTime


def _in_list(
    column: str,
    values: type[DeploymentStatus]
    | type[DeploymentType]
    | type[AttemptOutcome]
    | type[OutboxStatus],
) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


# Same list as the domain's IN_FLIGHT set, so the database and the code cannot drift apart.
IN_FLIGHT_SQL = "status IN ({})".format(", ".join(repr(s.value) for s in sorted(IN_FLIGHT)))


class DeploymentRow(Base):
    __tablename__ = "deployment"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(63))
    model_id: Mapped[str] = mapped_column(String(63))
    version: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    attempt: Mapped[int] = mapped_column(Integer)
    failure_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(255))
    correlation_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rollback_of: Mapped[str | None] = mapped_column(ForeignKey("deployment.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    row_version: Mapped[int] = mapped_column(Integer)

    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "model_id", "version"],
            ["model_version.tenant_id", "model_version.model_id", "model_version.version"],
            name="fk_deployment_model_version",
        ),
        CheckConstraint(_in_list("status", DeploymentStatus), name="status_valid"),
        CheckConstraint(_in_list("type", DeploymentType), name="type_valid"),
        # Concurrency guard: one in-flight deployment per model and environment, enforced by the
        # database so it holds across any number of API and worker replicas (ADR-003).
        Index(
            "uq_deployment_in_flight",
            "tenant_id",
            "model_id",
            "environment",
            unique=True,
            postgresql_where=text(IN_FLIGHT_SQL),
            sqlite_where=text(IN_FLIGHT_SQL),
        ),
        Index(
            "ix_deployment_model_environment_created",
            "tenant_id",
            "model_id",
            "environment",
            "created_at",
        ),
    )


class DeploymentAttemptRow(Base):
    __tablename__ = "deployment_attempt"

    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("deployment_id", "attempt_no", name="pk_deployment_attempt"),
        CheckConstraint(_in_list("outcome", AttemptOutcome), name="outcome_valid"),
    )


class EnvironmentBindingRow(Base):
    """What is live for a model in an environment. Updated only by compare-and-swap."""

    __tablename__ = "environment_binding"

    tenant_id: Mapped[str] = mapped_column(String(63))
    model_id: Mapped[str] = mapped_column(ForeignKey("model.id"))
    environment: Mapped[str] = mapped_column(String(32))
    current_deployment_id: Mapped[str | None] = mapped_column(
        ForeignKey("deployment.id"), nullable=True
    )
    previous_deployment_id: Mapped[str | None] = mapped_column(
        ForeignKey("deployment.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    row_version: Mapped[int] = mapped_column(Integer)

    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012
    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "model_id", "environment", name="pk_environment_binding"),
    )


class OutboxEventRow(Base):
    __tablename__ = "outbox_event"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(63))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonDocument)
    status: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        CheckConstraint(_in_list("status", OutboxStatus), name="status_valid"),
        Index("ix_outbox_event_status_available_at", "status", "available_at"),
    )


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_record"

    tenant_id: Mapped[str] = mapped_column(String(63))
    actor: Mapped[str] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JsonDocument)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "actor", "idempotency_key", name="pk_idempotency_record"),
        Index("ix_idempotency_record_expires_at", "expires_at"),
    )
