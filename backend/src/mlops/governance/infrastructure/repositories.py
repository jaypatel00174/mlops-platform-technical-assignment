"""SQLAlchemy repositories for the governance module: approvals and the append-only audit log."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mlops.governance.domain.approval import ApprovalDecision
from mlops.governance.domain.audit import AuditEvent
from mlops.governance.infrastructure.orm import ApprovalRow, AuditEventRow
from mlops.kernel.errors import AlreadyExists, NotFound
from mlops.kernel.vocabulary import ApprovalStatus
from mlops.platform.db import IntegrityKind, flush_translating


class SqlAuditRepository:
    """Insert and read only. UPDATE and DELETE are rejected by a database trigger."""

    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add(self, event: AuditEvent) -> None:
        self._session.add(
            AuditEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                actor=event.actor,
                action=event.action,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                correlation_id=event.correlation_id,
                occurred_at=event.occurred_at,
                before_state=dict(event.before) if event.before is not None else None,
                after_state=dict(event.after) if event.after is not None else None,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists(f"audit event {event.id!r} already exists", id=event.id)
            return None

        flush_translating(self._session, translate)

    def list_for_entity(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        rows = self._session.scalars(
            select(AuditEventRow)
            .where(
                AuditEventRow.tenant_id == self._tenant_id,
                AuditEventRow.entity_type == entity_type,
                AuditEventRow.entity_id == entity_id,
            )
            .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
        )
        return [
            AuditEvent(
                id=row.id,
                actor=row.actor,
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                correlation_id=row.correlation_id,
                occurred_at=row.occurred_at,
                before=row.before_state,
                after=row.after_state,
                tenant_id=row.tenant_id,
            )
            for row in rows
        ]


class SqlApprovalRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add(self, decision: ApprovalDecision, *, id: str) -> None:
        self._session.add(
            ApprovalRow(
                id=id,
                tenant_id=self._tenant_id,
                model_version_id=decision.version_id,
                decision=decision.decision.value,
                decided_by=decision.decided_by,
                comment=decision.comment,
                decided_at=decision.decided_at,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists(f"approval {id!r} already exists", id=id)
            if kind == "foreign_key":
                return NotFound(
                    f"version {decision.version_id!r} does not exist",
                    version_id=decision.version_id,
                )
            return None

        flush_translating(self._session, translate)

    def list_for_version(self, version_id: str) -> list[ApprovalDecision]:
        rows = self._session.scalars(
            select(ApprovalRow)
            .where(
                ApprovalRow.tenant_id == self._tenant_id, ApprovalRow.model_version_id == version_id
            )
            .order_by(ApprovalRow.decided_at, ApprovalRow.id)
        )
        return [
            ApprovalDecision(
                version_id=row.model_version_id,
                decision=ApprovalStatus(row.decision),
                decided_by=row.decided_by,
                comment=row.comment,
                decided_at=row.decided_at,
            )
            for row in rows
        ]
