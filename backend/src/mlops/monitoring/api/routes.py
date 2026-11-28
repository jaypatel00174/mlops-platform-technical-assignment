"""Monitoring endpoints: metric ingest, series, health status and version comparison.

Ingest needs the `service` role (a machine identity that can write metrics and nothing else).
Reads need `view`. Roles are checked before any data is read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from mlops.governance.application.access import Action, authorize
from mlops.monitoring.api.schemas import (
    ComparisonOut,
    IngestOut,
    MetricsBatch,
    ModelStatusOut,
    SeriesOut,
)
from mlops.monitoring.domain.series import Resolution
from mlops.platform.auth import CurrentPrincipal
from mlops.platform.problem import PROBLEM_RESPONSES
from mlops.wiring import Uow

router = APIRouter(prefix="/api/v1", tags=["monitoring"], responses=PROBLEM_RESPONSES)

EnvironmentQuery = Annotated[str, Query(min_length=1, max_length=32, examples=["production"])]


@router.post(
    "/metrics",
    status_code=202,
    response_model=IngestOut,
    summary="Ingest a batch of metric samples (service role)",
    description=(
        "All or nothing: one invalid or unknown sample rejects the whole batch, and the error "
        "names it (`samples[3].error_rate`). Resending a batch is safe; samples already stored "
        "are counted as duplicates."
    ),
)
def ingest_metrics(body: MetricsBatch, principal: CurrentPrincipal, uow: Uow) -> IngestOut:
    authorize(principal, Action.INGEST_METRICS)
    with uow.transaction(principal) as services:
        result = services.monitoring.ingest([s.model_dump() for s in body.samples])
    return IngestOut.from_result(result)


@router.get(
    "/models/{model_id}/metrics",
    response_model=SeriesOut,
    response_model_by_alias=True,
    summary="Metric series and summary for one version in one environment",
    description=(
        "`resolution=raw` returns every sample, `hour` and `day` return the mean per bucket "
        "(UTC). With no range, the last 24 hours (raw), 7 days (hour) or 30 days (day)."
    ),
)
def get_metrics(
    model_id: str,
    principal: CurrentPrincipal,
    uow: Uow,
    version: Annotated[str, Query(min_length=1, max_length=64, examples=["2.0.0"])],
    environment: EnvironmentQuery,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    resolution: Annotated[str, Query(examples=["hour"])] = "hour",
) -> SeriesOut:
    authorize(principal, Action.VIEW)
    parsed = Resolution.parse(resolution)
    with uow.transaction(principal) as services:
        result = services.monitoring.series(
            model_id=model_id,
            version=version,
            environment=environment,
            start=from_,
            end=to,
            resolution=parsed,
        )
    return SeriesOut.from_result(result)


@router.get(
    "/models/{model_id}/status",
    response_model=ModelStatusOut,
    summary="Health of a model: HEALTHY, DEGRADED, CRITICAL, STALE or NO_DATA, with reasons",
    description=(
        "Each version that is live or has reported metrics is evaluated per environment. The "
        "model's own status is the worst among its live versions. Rules and default thresholds "
        "are in docs/monitoring.md."
    ),
)
def get_status(model_id: str, principal: CurrentPrincipal, uow: Uow) -> ModelStatusOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        return ModelStatusOut.from_domain(services.monitoring.status(model_id))


@router.get(
    "/models/{model_id}/versions/compare",
    response_model=ComparisonOut,
    summary="Compare two versions: metadata and metric means side by side",
    description=(
        "`deltas` is the change from `a` to `b`. With no range, all available samples are used."
    ),
)
def compare_versions(
    model_id: str,
    principal: CurrentPrincipal,
    uow: Uow,
    a: Annotated[str, Query(min_length=1, max_length=64)],
    b: Annotated[str, Query(min_length=1, max_length=64)],
    environment: EnvironmentQuery = "production",
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
) -> ComparisonOut:
    authorize(principal, Action.VIEW)
    with uow.transaction(principal) as services:
        comparison = services.monitoring.compare_versions(
            model_id=model_id, a=a, b=b, environment=environment, start=from_, end=to
        )
    return ComparisonOut.from_domain(comparison)
