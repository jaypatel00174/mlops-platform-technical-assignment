"""Monitoring use cases: ingest, read a series, evaluate health, compare versions.

Authorization happens at the API boundary. Ingest is the only write and it is a plain append:
no audit entry per sample (the volume would drown the audit trail), idempotent by the natural key
so an agent can safely resend a batch.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mlops.kernel.errors import ValidationFailure
from mlops.kernel.ports import Clock
from mlops.monitoring.application.ports import LiveVersions, MetricsStore
from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.domain.health import (
    HealthReport,
    HealthStatus,
    HealthThresholds,
    evaluate,
    worst_status,
)
from mlops.monitoring.domain.series import (
    Delta,
    Point,
    Resolution,
    Summary,
    compare,
    downsample,
    summarize,
)
from mlops.registry.application.services import ModelVersion, RegistryService

__all__ = [
    "Comparison",
    "IngestResult",
    "ModelHealth",
    "MonitoringService",
    "SeriesResult",
    "VersionHealth",
]

# Clock skew allowed between an agent and the platform.
_FUTURE_TOLERANCE = timedelta(minutes=5)
_DEFAULT_RANGE = {
    Resolution.RAW: timedelta(hours=24),
    Resolution.HOUR: timedelta(days=7),
    Resolution.DAY: timedelta(days=30),
}


@dataclass(frozen=True, slots=True)
class IngestResult:
    received: int
    inserted: int

    @property
    def duplicates(self) -> int:
        return self.received - self.inserted


@dataclass(frozen=True, slots=True)
class SeriesResult:
    model_id: str
    version: str
    environment: str
    resolution: Resolution
    start: datetime
    end: datetime
    points: list[Point]
    summary: Summary | None


@dataclass(frozen=True, slots=True)
class VersionHealth:
    version: str
    environment: str
    live: bool
    report: HealthReport


@dataclass(frozen=True, slots=True)
class ModelHealth:
    model_id: str
    status: HealthStatus
    evaluated_at: datetime
    items: list[VersionHealth]


@dataclass(frozen=True, slots=True)
class Comparison:
    environment: str
    a: ModelVersion
    b: ModelVersion
    summary_a: Summary | None
    summary_b: Summary | None
    deltas: dict[str, Delta] | None


class MonitoringService:
    def __init__(
        self,
        *,
        metrics: MetricsStore,
        registry: RegistryService,
        live_versions: LiveVersions,
        clock: Clock,
        tenant_id: str,
        thresholds: HealthThresholds | None = None,
        max_batch: int = 1000,
        max_series_rows: int = 100_000,
    ) -> None:
        self._metrics = metrics
        self._registry = registry
        self._live_versions = live_versions
        self._clock = clock
        self._tenant_id = tenant_id
        self._thresholds = thresholds or HealthThresholds()
        self._max_batch = max_batch
        self._max_series_rows = max_series_rows

    # -- ingest ----------------------------------------------------------------------------

    def ingest(self, items: Sequence[Mapping[str, Any]]) -> IngestResult:
        """Store a batch atomically: if any sample is invalid or unknown, none is stored."""
        if not items:
            raise ValidationFailure("the batch is empty", field="samples")
        if len(items) > self._max_batch:
            raise ValidationFailure(
                f"a batch holds at most {self._max_batch} samples; send several batches",
                field="samples",
                max_batch=self._max_batch,
                received=len(items),
            )
        latest_allowed = self._clock.now() + _FUTURE_TOLERANCE
        samples: list[MetricSample] = []
        for index, item in enumerate(items):
            try:
                sample = MetricSample(tenant_id=self._tenant_id, **item)
                if sample.ts > latest_allowed:
                    raise ValidationFailure("ts is in the future", field="ts", value=str(sample.ts))
            except ValidationFailure as error:
                field = error.details.get("field", "sample")
                raise ValidationFailure(
                    f"sample {index}: {error.message}", field=f"samples[{index}].{field}"
                ) from error
            samples.append(sample)

        # One lookup per distinct version gives a precise 404 instead of a bare foreign-key error.
        for model_id, version in sorted({(s.model_id, s.version) for s in samples}):
            self._registry.get_version(model_id, version)
        return IngestResult(received=len(samples), inserted=self._metrics.add_many(samples))

    # -- series ----------------------------------------------------------------------------

    def series(
        self,
        *,
        model_id: str,
        version: str,
        environment: str,
        start: datetime | None,
        end: datetime | None,
        resolution: Resolution,
    ) -> SeriesResult:
        self._registry.get_version(model_id, version)
        end = end or self._clock.now()
        start = start or end - _DEFAULT_RANGE[resolution]
        if start >= end:
            raise ValidationFailure("from must be earlier than to", field="from")
        samples = self._bounded(model_id, version, environment, start, end)
        return SeriesResult(
            model_id=model_id,
            version=version,
            environment=environment,
            resolution=resolution,
            start=start,
            end=end,
            points=downsample(samples, resolution),
            summary=summarize(samples),
        )

    # -- health ----------------------------------------------------------------------------

    def status(self, model_id: str) -> ModelHealth:
        self._registry.get_model(model_id)
        now = self._clock.now()
        live = set(self._live_versions(model_id))
        keys = sorted(live | set(self._metrics.series_keys(model_id)))
        items = [
            VersionHealth(
                version=version,
                environment=environment,
                live=(version, environment) in live,
                report=self._health_of(model_id, version, environment, now),
            )
            for version, environment in keys
        ]
        items.sort(key=lambda item: (not item.live, item.environment, item.version))
        # Only what is live decides the model's status: an old version gone quiet is expected.
        deciding = [item for item in items if item.live] or items
        return ModelHealth(
            model_id=model_id,
            status=worst_status(item.report.status for item in deciding),
            evaluated_at=now,
            items=items,
        )

    def _health_of(
        self, model_id: str, version: str, environment: str, now: datetime
    ) -> HealthReport:
        latest = self._metrics.latest(model_id, version, environment)
        if latest is None:
            return evaluate([], now, self._thresholds)
        recent = self._metrics.list_for(
            model_id=model_id,
            version=version,
            environment=environment,
            start=latest.ts - self._thresholds.window,
        )
        return evaluate(recent, now, self._thresholds)

    # -- compare ---------------------------------------------------------------------------

    def compare_versions(
        self,
        *,
        model_id: str,
        a: str,
        b: str,
        environment: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Comparison:
        """Metadata and metric means side by side. With no range, all available samples count."""
        if a == b:
            raise ValidationFailure("choose two different versions to compare", field="b")
        first = self._registry.get_version(model_id, a)
        second = self._registry.get_version(model_id, b)
        summary_a = summarize(self._bounded(model_id, a, environment, start, end))
        summary_b = summarize(self._bounded(model_id, b, environment, start, end))
        return Comparison(
            environment=environment,
            a=first,
            b=second,
            summary_a=summary_a,
            summary_b=summary_b,
            deltas=compare(summary_a, summary_b) if summary_a and summary_b else None,
        )

    # -- helpers ---------------------------------------------------------------------------

    def _bounded(
        self,
        model_id: str,
        version: str,
        environment: str,
        start: datetime | None,
        end: datetime | None,
    ) -> list[MetricSample]:
        """Read a range, refusing (rather than silently truncating) when it is too large."""
        rows = self._metrics.list_for(
            model_id=model_id,
            version=version,
            environment=environment,
            start=start,
            end=end,
            limit=self._max_series_rows + 1,
        )
        if len(rows) > self._max_series_rows:
            raise ValidationFailure(
                f"more than {self._max_series_rows} samples in range; narrow from/to "
                "or use a coarser resolution",
                field="from",
                max_rows=self._max_series_rows,
            )
        return rows
