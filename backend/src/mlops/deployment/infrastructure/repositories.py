"""SQLAlchemy repositories for the deployment module.

The database does the hard part: a partial unique index allows one in-flight deployment per model
and environment, and optimistic-lock counters make every update a compare-and-swap (ADR-003).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from mlops.deployment.domain.entities import Deployment, DeploymentAttempt, EnvironmentBinding
from mlops.deployment.domain.failure import FailureClass
from mlops.deployment.domain.idempotency import IdempotencyRecord
from mlops.deployment.domain.outbox import OutboxEvent, OutboxStatus
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus, DeploymentType
from mlops.deployment.infrastructure.orm import (
    DeploymentAttemptRow,
    DeploymentRow,
    EnvironmentBindingRow,
    IdempotencyRecordRow,
    OutboxEventRow,
)
from mlops.kernel.errors import AlreadyExists, DeploymentConflict, NotFound, StaleVersion
from mlops.platform.db import IntegrityKind, flush_translating

_STALE_MESSAGE = "this record was changed by someone else; reload it and try again"


def _deployment_from_row(row: DeploymentRow) -> Deployment:
    return Deployment(
        id=row.id,
        model_id=row.model_id,
        version=row.version,
        environment=row.environment,
        type=DeploymentType(row.type),
        status=DeploymentStatus(row.status),
        attempt=row.attempt,
        requested_by=row.requested_by,
        correlation_id=row.correlation_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        failure_class=FailureClass(row.failure_class) if row.failure_class else None,
        error=row.error,
        idempotency_key=row.idempotency_key,
        rollback_of=row.rollback_of,
        tenant_id=row.tenant_id,
        row_version=row.row_version,
    )


class SqlDeploymentRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add(self, deployment: Deployment) -> None:
        """Insert a deployment. Raises DeploymentConflict if one is already in flight."""
        self._session.add(
            DeploymentRow(
                id=deployment.id,
                tenant_id=deployment.tenant_id,
                model_id=deployment.model_id,
                version=deployment.version,
                environment=deployment.environment,
                type=deployment.type.value,
                status=deployment.status.value,
                attempt=deployment.attempt,
                failure_class=deployment.failure_class.value if deployment.failure_class else None,
                error=deployment.error,
                requested_by=deployment.requested_by,
                correlation_id=deployment.correlation_id,
                idempotency_key=deployment.idempotency_key,
                rollback_of=deployment.rollback_of,
                created_at=deployment.created_at,
                updated_at=deployment.updated_at,
            )
        )

        def translate(kind: IntegrityKind, error: IntegrityError) -> Exception | None:
            if kind == "foreign_key":
                return NotFound(
                    f"version {deployment.version} of {deployment.model_id!r} does not exist",
                    model_id=deployment.model_id,
                    version=deployment.version,
                )
            if kind != "unique":
                return None
            message = str(error.orig)
            # PostgreSQL names the index; SQLite names the columns.
            if "uq_deployment_in_flight" in message or "deployment.environment" in message:
                existing = self.find_in_flight(deployment.model_id, deployment.environment)
                return DeploymentConflict(
                    "another deployment is already in progress for this model and environment",
                    existing_deployment_id=existing.id if existing else None,
                    environment=deployment.environment,
                )
            return AlreadyExists(f"deployment {deployment.id!r} already exists", id=deployment.id)

        flush_translating(self._session, translate)

    def get(self, deployment_id: str) -> Deployment | None:
        row = self._row(deployment_id)
        return _deployment_from_row(row) if row else None

    def search(
        self,
        *,
        model_id: str | None = None,
        environment: str | None = None,
        status: DeploymentStatus | None = None,
        limit: int = 50,
    ) -> list[Deployment]:
        statement = select(DeploymentRow).where(DeploymentRow.tenant_id == self._tenant_id)
        if model_id is not None:
            statement = statement.where(DeploymentRow.model_id == model_id)
        if environment is not None:
            statement = statement.where(DeploymentRow.environment == environment)
        if status is not None:
            statement = statement.where(DeploymentRow.status == status.value)
        rows = self._session.scalars(
            statement.order_by(DeploymentRow.created_at.desc(), DeploymentRow.id.desc()).limit(
                limit
            )
        )
        return [_deployment_from_row(row) for row in rows]

    def find_in_flight(self, model_id: str, environment: str) -> Deployment | None:
        row = self._session.scalar(
            select(DeploymentRow).where(
                DeploymentRow.tenant_id == self._tenant_id,
                DeploymentRow.model_id == model_id,
                DeploymentRow.environment == environment,
                DeploymentRow.status.in_(["REQUESTED", "VALIDATING", "DEPLOYING"]),
            )
        )
        return _deployment_from_row(row) if row else None

    def save(self, deployment: Deployment) -> Deployment:
        """Persist a state change. Fails with StaleVersion if the row moved on in the meantime."""
        row = self._row(deployment.id)
        if row is None:
            raise NotFound(f"deployment {deployment.id!r} does not exist", id=deployment.id)
        if row.row_version != deployment.row_version:
            raise StaleVersion(
                _STALE_MESSAGE,
                deployment_id=deployment.id,
                expected=deployment.row_version,
                actual=row.row_version,
            )
        row.status = deployment.status.value
        row.attempt = deployment.attempt
        row.failure_class = deployment.failure_class.value if deployment.failure_class else None
        row.error = deployment.error
        row.updated_at = deployment.updated_at

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                # Moving a settled deployment back in flight (retry) can collide with a newer one.
                existing = self.find_in_flight(deployment.model_id, deployment.environment)
                return DeploymentConflict(
                    "another deployment is already in progress for this model and environment",
                    existing_deployment_id=existing.id if existing else None,
                    environment=deployment.environment,
                )
            return None

        try:
            flush_translating(self._session, translate)
        except StaleDataError as error:
            self._session.rollback()
            raise StaleVersion(_STALE_MESSAGE, deployment_id=deployment.id) from error
        return _deployment_from_row(row)

    # -- attempts --------------------------------------------------------------------------

    def record_attempt_started(self, deployment_id: str, attempt_no: int, now: datetime) -> None:
        self._session.add(
            DeploymentAttemptRow(
                deployment_id=deployment_id,
                attempt_no=attempt_no,
                outcome=AttemptOutcome.STARTED.value,
                started_at=now,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists(
                    f"attempt {attempt_no} of {deployment_id!r} already exists",
                    deployment_id=deployment_id,
                    attempt_no=attempt_no,
                )
            if kind == "foreign_key":
                return NotFound(f"deployment {deployment_id!r} does not exist", id=deployment_id)
            return None

        flush_translating(self._session, translate)

    def record_attempt_finished(
        self,
        deployment_id: str,
        attempt_no: int,
        *,
        outcome: AttemptOutcome,
        now: datetime,
        error: str | None = None,
        runtime_ref: str | None = None,
    ) -> None:
        row = self._session.get(DeploymentAttemptRow, (deployment_id, attempt_no))
        if row is None:
            raise NotFound(
                f"attempt {attempt_no} of {deployment_id!r} does not exist",
                deployment_id=deployment_id,
                attempt_no=attempt_no,
            )
        row.outcome = outcome.value
        row.finished_at = now
        row.error = error
        row.runtime_ref = runtime_ref
        self._session.flush()

    def attempts(self, deployment_id: str) -> list[DeploymentAttempt]:
        rows = self._session.scalars(
            select(DeploymentAttemptRow)
            .where(DeploymentAttemptRow.deployment_id == deployment_id)
            .order_by(DeploymentAttemptRow.attempt_no)
        )
        return [
            DeploymentAttempt(
                deployment_id=row.deployment_id,
                attempt_no=row.attempt_no,
                outcome=AttemptOutcome(row.outcome),
                started_at=row.started_at,
                finished_at=row.finished_at,
                error=row.error,
                runtime_ref=row.runtime_ref,
            )
            for row in rows
        ]

    def _row(self, deployment_id: str) -> DeploymentRow | None:
        return self._session.scalar(
            select(DeploymentRow).where(
                DeploymentRow.id == deployment_id, DeploymentRow.tenant_id == self._tenant_id
            )
        )


class SqlEnvironmentBindingRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get(self, model_id: str, environment: str) -> EnvironmentBinding | None:
        row = self._row(model_id, environment)
        return self._to_domain(row) if row else None

    def save(self, binding: EnvironmentBinding, now: datetime) -> EnvironmentBinding:
        """Insert or update by compare-and-swap. A lost race raises StaleVersion."""
        row = self._row(binding.model_id, binding.environment)
        if row is None:
            row = EnvironmentBindingRow(
                tenant_id=binding.tenant_id,
                model_id=binding.model_id,
                environment=binding.environment,
                current_deployment_id=binding.current_deployment_id,
                previous_deployment_id=binding.previous_deployment_id,
                updated_at=now,
            )
            self._session.add(row)

            def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
                if kind == "unique":  # someone created the binding first
                    return StaleVersion(
                        _STALE_MESSAGE, model_id=binding.model_id, environment=binding.environment
                    )
                if kind == "foreign_key":
                    return NotFound(
                        "model or deployment does not exist",
                        model_id=binding.model_id,
                        environment=binding.environment,
                    )
                return None

            flush_translating(self._session, translate)
            return self._to_domain(row)

        if row.row_version != binding.row_version:
            raise StaleVersion(
                _STALE_MESSAGE,
                model_id=binding.model_id,
                environment=binding.environment,
                expected=binding.row_version,
                actual=row.row_version,
            )
        row.current_deployment_id = binding.current_deployment_id
        row.previous_deployment_id = binding.previous_deployment_id
        row.updated_at = now
        try:
            self._session.flush()
        except StaleDataError as error:
            self._session.rollback()
            raise StaleVersion(_STALE_MESSAGE, model_id=binding.model_id) from error
        return self._to_domain(row)

    def live_environments(self, model_id: str, version: str) -> list[str]:
        """Environments where this version is the current deployment (used by the archive rule)."""
        rows = self._session.scalars(
            select(EnvironmentBindingRow.environment)
            .join(DeploymentRow, DeploymentRow.id == EnvironmentBindingRow.current_deployment_id)
            .where(
                EnvironmentBindingRow.tenant_id == self._tenant_id,
                EnvironmentBindingRow.model_id == model_id,
                DeploymentRow.version == version,
            )
            .order_by(EnvironmentBindingRow.environment)
        )
        return list(rows)

    def _row(self, model_id: str, environment: str) -> EnvironmentBindingRow | None:
        return self._session.get(EnvironmentBindingRow, (self._tenant_id, model_id, environment))

    @staticmethod
    def _to_domain(row: EnvironmentBindingRow) -> EnvironmentBinding:
        return EnvironmentBinding(
            model_id=row.model_id,
            environment=row.environment,
            current_deployment_id=row.current_deployment_id,
            previous_deployment_id=row.previous_deployment_id,
            tenant_id=row.tenant_id,
            row_version=row.row_version,
        )


class SqlOutboxRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add(self, event: OutboxEvent) -> None:
        self._session.add(
            OutboxEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                type=event.type,
                payload=dict(event.payload),
                status=event.status.value,
                attempts=event.attempts,
                available_at=event.available_at,
                locked_by=event.locked_by,
                lease_until=event.lease_until,
                created_at=event.created_at,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists(f"outbox event {event.id!r} already exists", id=event.id)
            return None

        flush_translating(self._session, translate)

    def get(self, event_id: str) -> OutboxEvent | None:
        row = self._session.scalar(
            select(OutboxEventRow).where(
                OutboxEventRow.id == event_id, OutboxEventRow.tenant_id == self._tenant_id
            )
        )
        if row is None:
            return None
        return OutboxEvent(
            id=row.id,
            type=row.type,
            payload=row.payload,
            created_at=row.created_at,
            available_at=row.available_at,
            status=OutboxStatus(row.status),
            attempts=row.attempts,
            locked_by=row.locked_by,
            lease_until=row.lease_until,
            tenant_id=row.tenant_id,
        )

    def count_by_status(self) -> dict[str, int]:
        rows = self._session.execute(
            select(OutboxEventRow.status, OutboxEventRow.id).where(
                OutboxEventRow.tenant_id == self._tenant_id
            )
        )
        counts: dict[str, int] = {}
        for status, _id in rows:
            counts[status] = counts.get(status, 0) + 1
        return counts


class SqlIdempotencyRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def get(self, actor: str, key: str) -> IdempotencyRecord | None:
        row = self._session.get(IdempotencyRecordRow, (self._tenant_id, actor, key))
        if row is None:
            return None
        return IdempotencyRecord(
            key=row.idempotency_key,
            actor=row.actor,
            request_hash=row.request_hash,
            response_status=row.response_status,
            response_body=row.response_body,
            created_at=row.created_at,
            expires_at=row.expires_at,
            tenant_id=row.tenant_id,
        )

    def add(self, record: IdempotencyRecord) -> None:
        self._session.add(
            IdempotencyRecordRow(
                tenant_id=record.tenant_id,
                actor=record.actor,
                idempotency_key=record.key,
                request_hash=record.request_hash,
                response_status=record.response_status,
                response_body=dict(record.response_body),
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists("this idempotency key was already stored", key=record.key)
            return None

        flush_translating(self._session, translate)

    def purge_expired(self, now: datetime) -> int:
        result = self._session.execute(
            delete(IdempotencyRecordRow).where(
                IdempotencyRecordRow.tenant_id == self._tenant_id,
                IdempotencyRecordRow.expires_at <= now,
            )
        )
        return int(cast(CursorResult[Any], result).rowcount)
