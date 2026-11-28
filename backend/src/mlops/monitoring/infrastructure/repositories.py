"""SQLAlchemy repository for the monitoring module (the SQL adapter of the MetricsStore port)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mlops.kernel.errors import NotFound
from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.infrastructure.orm import MetricSampleRow
from mlops.platform.db import integrity_kind

_CHUNK = 500
_NATURAL_KEY = ["tenant_id", "model_id", "version", "environment", "ts"]


class SqlMetricSampleRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add_many(self, samples: Sequence[MetricSample]) -> int:
        """Insert samples, skipping ones that already exist. Returns how many were inserted.

        Skipping duplicates makes both the seed loader and agent retries idempotent.
        """
        inserted = 0
        for start in range(0, len(samples), _CHUNK):
            values = [self._values(sample) for sample in samples[start : start + _CHUNK]]
            dialect = self._session.get_bind().dialect.name
            try:
                if dialect == "postgresql":
                    statement: Any = (
                        postgresql_insert(MetricSampleRow)
                        .values(values)
                        .on_conflict_do_nothing(index_elements=_NATURAL_KEY)
                    )
                else:
                    statement = (
                        sqlite_insert(MetricSampleRow)
                        .values(values)
                        .on_conflict_do_nothing(index_elements=_NATURAL_KEY)
                    )
                # RETURNING lists only the rows that were really inserted. cursor.rowcount is
                # not reliable for multi-row inserts (psycopg reports -1).
                result = self._session.execute(statement.returning(MetricSampleRow.id))
                inserted += len(result.all())
            except IntegrityError as error:
                self._session.rollback()
                if integrity_kind(error) == "foreign_key":
                    raise NotFound(
                        "a metric refers to a model version that does not exist"
                    ) from error
                raise
        return inserted

    def count(self, model_id: str | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(MetricSampleRow)
            .where(MetricSampleRow.tenant_id == self._tenant_id)
        )
        if model_id is not None:
            statement = statement.where(MetricSampleRow.model_id == model_id)
        return int(self._session.scalar(statement) or 0)

    def list_for(
        self,
        *,
        model_id: str,
        version: str,
        environment: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[MetricSample]:
        statement = select(MetricSampleRow).where(
            MetricSampleRow.tenant_id == self._tenant_id,
            MetricSampleRow.model_id == model_id,
            MetricSampleRow.version == version,
            MetricSampleRow.environment == environment,
        )
        if start is not None:
            statement = statement.where(MetricSampleRow.ts >= start)
        if end is not None:
            statement = statement.where(MetricSampleRow.ts < end)
        rows = self._session.scalars(statement.order_by(MetricSampleRow.ts))
        return [
            MetricSample(
                model_id=row.model_id,
                version=row.version,
                environment=row.environment,
                ts=row.ts,
                latency_ms=row.latency_ms,
                throughput_rpm=row.throughput_rpm,
                error_rate=row.error_rate,
                quality_score=row.quality_score,
                drift_score=row.drift_score,
                availability=row.availability,
                tenant_id=row.tenant_id,
            )
            for row in rows
        ]

    def _values(self, sample: MetricSample) -> dict[str, Any]:
        return {
            "tenant_id": sample.tenant_id,
            "model_id": sample.model_id,
            "version": sample.version,
            "environment": sample.environment,
            "ts": sample.ts,
            "latency_ms": sample.latency_ms,
            "throughput_rpm": sample.throughput_rpm,
            "error_rate": sample.error_rate,
            "quality_score": sample.quality_score,
            "drift_score": sample.drift_score,
            "availability": sample.availability,
        }
