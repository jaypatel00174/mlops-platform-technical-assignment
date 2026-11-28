"""Tables owned by the registry module: model and model_version."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.platform.db import Base, JsonDocument, UTCDateTime


def _in_list(column: str, values: type[Stage] | type[ApprovalStatus]) -> str:
    return f"{column} IN ({', '.join(repr(member.value) for member in values)})"


class ModelRow(Base):
    __tablename__ = "model"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(255))
    owner: Mapped[str] = mapped_column(String(255))
    framework: Mapped[str] = mapped_column(String(255))
    tags: Mapped[list[str]] = mapped_column(JsonDocument)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_model_tenant_id", "tenant_id"),)


class ModelVersionRow(Base):
    __tablename__ = "model_version"

    id: Mapped[str] = mapped_column(String(127), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(63))
    model_id: Mapped[str] = mapped_column(ForeignKey("model.id"))
    version: Mapped[str] = mapped_column(String(64))
    algorithm: Mapped[str] = mapped_column(String(255))
    artifact_uri: Mapped[str] = mapped_column(String(1024))
    artifact_digest: Mapped[str] = mapped_column(String(71))
    training_data_ref: Mapped[str] = mapped_column(String(1024))
    registered_by: Mapped[str] = mapped_column(String(255))
    stage: Mapped[str] = mapped_column(String(16))
    approval_status: Mapped[str] = mapped_column(String(16))
    runtime_type: Mapped[str] = mapped_column(String(64))
    # "metadata" is reserved on declarative classes, so the attribute has another name.
    extra_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JsonDocument)
    tags: Mapped[list[str]] = mapped_column(JsonDocument)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # Optimistic lock: SQLAlchemy adds "WHERE row_version = :loaded" to every UPDATE and bumps it.
    row_version: Mapped[int] = mapped_column(Integer)

    __mapper_args__ = {"version_id_col": row_version}  # noqa: RUF012
    __table_args__ = (
        UniqueConstraint("tenant_id", "model_id", "version", name="uq_model_version_natural"),
        CheckConstraint(_in_list("stage", Stage), name="stage_valid"),
        CheckConstraint(_in_list("approval_status", ApprovalStatus), name="approval_status_valid"),
        Index("ix_model_version_model_id", "model_id"),
    )
