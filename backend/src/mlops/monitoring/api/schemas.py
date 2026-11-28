"""Request and response bodies of the monitoring API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from mlops.monitoring.application.services import (
    Comparison,
    IngestResult,
    ModelHealth,
    SeriesResult,
    VersionHealth,
)
from mlops.monitoring.domain.series import Delta, Point, Stat, Summary
from mlops.registry.application.services import ModelVersion

# ------------------------------------------------------------------------------- ingest


class SampleIn(BaseModel):
    """One observation. Ranges are enforced by the domain and reported per sample."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(max_length=63, examples=["pump-failure-predictor"])
    version: str = Field(max_length=64, examples=["2.0.0"])
    environment: str = Field(max_length=32, examples=["production"])
    ts: datetime = Field(examples=["2026-07-01T00:00:00Z"])
    latency_ms: float = Field(description="Milliseconds, 0 and up")
    throughput_rpm: float = Field(description="Requests per minute, 0 and up")
    error_rate: float = Field(description="Fraction of failed requests, 0 to 1")
    quality_score: float = Field(description="0 to 1")
    drift_score: float = Field(description="0 and up; higher means more drift")
    availability: float = Field(description="Percent, 0 to 100")


class MetricsBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: list[SampleIn] = Field(max_length=10_000)


class IngestOut(BaseModel):
    received: int
    inserted: int
    duplicates: int = Field(description="Already stored (same model, version, environment, ts)")

    @classmethod
    def from_result(cls, result: IngestResult) -> Self:
        return cls(received=result.received, inserted=result.inserted, duplicates=result.duplicates)


# -------------------------------------------------------------------------------- series


class PointOut(BaseModel):
    ts: datetime
    latency_ms: float
    throughput_rpm: float
    error_rate: float
    quality_score: float
    drift_score: float
    availability: float
    samples: int

    @classmethod
    def from_domain(cls, point: Point) -> Self:
        return cls(**{name: getattr(point, name) for name in cls.model_fields})


class StatOut(BaseModel):
    minimum: float
    maximum: float
    mean: float

    @classmethod
    def from_domain(cls, stat: Stat) -> Self:
        return cls(minimum=stat.minimum, maximum=stat.maximum, mean=stat.mean)


class SummaryOut(BaseModel):
    count: int
    first_ts: datetime
    last_ts: datetime
    latency_p95_ms: float
    metrics: dict[str, StatOut]

    @classmethod
    def from_domain(cls, summary: Summary) -> Self:
        return cls(
            count=summary.count,
            first_ts=summary.first_ts,
            last_ts=summary.last_ts,
            latency_p95_ms=summary.latency_p95_ms,
            metrics={name: StatOut.from_domain(stat) for name, stat in summary.metrics.items()},
        )


class SeriesOut(BaseModel):
    model_id: str
    version: str
    environment: str
    resolution: str
    start: datetime = Field(serialization_alias="from")
    end: datetime = Field(serialization_alias="to")
    points: list[PointOut]
    summary: SummaryOut | None = Field(description="Null when the range holds no samples")

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_result(cls, result: SeriesResult) -> Self:
        return cls(
            model_id=result.model_id,
            version=result.version,
            environment=result.environment,
            resolution=result.resolution.value,
            start=result.start,
            end=result.end,
            points=[PointOut.from_domain(p) for p in result.points],
            summary=SummaryOut.from_domain(result.summary) if result.summary else None,
        )


# -------------------------------------------------------------------------------- health


class ReasonOut(BaseModel):
    metric: str
    severity: str
    message: str
    value: float | None
    threshold: float | None


class VersionHealthOut(BaseModel):
    version: str
    environment: str
    live: bool = Field(description="True if this version is what the environment serves now")
    status: str
    reasons: list[ReasonOut]
    last_sample_at: datetime | None = Field(
        description="When the newest sample arrived: the last sign the model was serving"
    )
    window_means: dict[str, float]

    @classmethod
    def from_domain(cls, item: VersionHealth) -> Self:
        report = item.report
        return cls(
            version=item.version,
            environment=item.environment,
            live=item.live,
            status=report.status.value,
            reasons=[
                ReasonOut(
                    metric=r.metric,
                    severity=r.severity.value,
                    message=r.message,
                    value=r.value,
                    threshold=r.threshold,
                )
                for r in report.reasons
            ],
            last_sample_at=report.last_sample_at,
            window_means=report.window_means,
        )


class ModelStatusOut(BaseModel):
    model_id: str
    status: str = Field(description="The worst status among the live versions")
    evaluated_at: datetime
    items: list[VersionHealthOut]

    @classmethod
    def from_domain(cls, health: ModelHealth) -> Self:
        return cls(
            model_id=health.model_id,
            status=health.status.value,
            evaluated_at=health.evaluated_at,
            items=[VersionHealthOut.from_domain(item) for item in health.items],
        )


# ------------------------------------------------------------------------------- compare


class VersionMetaOut(BaseModel):
    version: str
    stage: str
    approval_status: str
    algorithm: str
    runtime_type: str
    artifact_uri: str
    artifact_digest: str
    training_data_ref: str
    registered_by: str
    created_at: datetime
    tags: list[str]
    metadata: dict[str, Any]
    metrics: SummaryOut | None

    @classmethod
    def build(cls, version: ModelVersion, summary: Summary | None) -> Self:
        return cls(
            version=version.version,
            stage=version.stage.value,
            approval_status=version.approval_status.value,
            algorithm=version.algorithm,
            runtime_type=version.runtime_type,
            artifact_uri=version.artifact_uri,
            artifact_digest=version.artifact_digest,
            training_data_ref=version.training_data_ref,
            registered_by=version.registered_by,
            created_at=version.created_at,
            tags=list(version.tags),
            metadata=dict(version.metadata),
            metrics=SummaryOut.from_domain(summary) if summary else None,
        )


class DeltaOut(BaseModel):
    a: float
    b: float
    change: float
    change_pct: float | None = Field(description="Null when the baseline (a) is zero")

    @classmethod
    def from_domain(cls, delta: Delta) -> Self:
        return cls(a=delta.a, b=delta.b, change=delta.change, change_pct=delta.change_pct)


class ComparisonOut(BaseModel):
    model_id: str
    environment: str
    a: VersionMetaOut
    b: VersionMetaOut
    deltas: dict[str, DeltaOut] | None = Field(
        description="Change in each metric's mean from a to b; null unless both have samples"
    )

    @classmethod
    def from_domain(cls, comparison: Comparison) -> Self:
        return cls(
            model_id=comparison.a.model_id,
            environment=comparison.environment,
            a=VersionMetaOut.build(comparison.a, comparison.summary_a),
            b=VersionMetaOut.build(comparison.b, comparison.summary_b),
            deltas=(
                {name: DeltaOut.from_domain(d) for name, d in comparison.deltas.items()}
                if comparison.deltas
                else None
            ),
        )
