"""Tables owned by the governance module: approval and the append-only audit_event."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mlops.platform.db import Base, JsonDocument, UTCDateTime


class ApprovalRow(Base):
    __tablename__ = "approval"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(63))
    model_version_id: Mapped[str] = mapped_column(ForeignKey("model_version.id"))
    decision: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        CheckConstraint("decision IN ('APPROVED', 'REJECTED')", name="decision_valid"),
        Index("ix_approval_model_version_id", "model_version_id"),
    )


class AuditEventRow(Base):
    """Append-only. A database trigger (see the initial migration) rejects UPDATE and DELETE."""

    __tablename__ = "audit_event"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(63))
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(127))
    correlation_id: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument, nullable=True)
    after_state: Mapped[dict[str, Any] | None] = mapped_column(JsonDocument, nullable=True)

    __table_args__ = (
        Index("ix_audit_event_entity", "entity_type", "entity_id"),
        Index("ix_audit_event_occurred_at", "occurred_at"),
    )
