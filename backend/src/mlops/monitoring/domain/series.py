"""Time series helpers: downsampling for charts, summaries and version comparison.

Pure functions over MetricSample values. At the current scale they run on the rows the store
returns; when volume demands it, a PostgreSQL rollup migration can pre-compute the same numbers
without changing these definitions (docs/architecture.md, section 11).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from mlops.kernel.errors import ValidationFailure
from mlops.monitoring.domain.entities import MetricSample

METRIC_NAMES = (
    "latency_ms",
    "throughput_rpm",
    "error_rate",
    "quality_score",
    "drift_score",
    "availability",
)


class Resolution(StrEnum):
    RAW = "raw"
    HOUR = "hour"
    DAY = "day"

    @classmethod
    def parse(cls, text: str) -> Self:
        try:
            return cls(text)
        except ValueError:
            raise ValidationFailure(
                f"resolution must be one of {', '.join(r.value for r in cls)}",
                field="resolution",
                value=text,
            ) from None


def bucket_start(ts: datetime, resolution: Resolution) -> datetime:
    if resolution is Resolution.HOUR:
        return ts.replace(minute=0, second=0, microsecond=0)
    if resolution is Resolution.DAY:
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts


@dataclass(frozen=True, slots=True)
class Point:
    """One chart point: the mean of the samples that fall in its bucket."""

    ts: datetime
    latency_ms: float
    throughput_rpm: float
    error_rate: float
    quality_score: float
    drift_score: float
    availability: float
    samples: int


def downsample(samples: Sequence[MetricSample], resolution: Resolution) -> list[Point]:
    buckets: dict[datetime, list[MetricSample]] = defaultdict(list)
    for sample in samples:
        buckets[bucket_start(sample.ts, resolution)].append(sample)
    points: list[Point] = []
    for ts in sorted(buckets):
        group = buckets[ts]
        means = {name: sum(getattr(s, name) for s in group) / len(group) for name in METRIC_NAMES}
        points.append(Point(ts=ts, samples=len(group), **means))
    return points


@dataclass(frozen=True, slots=True)
class Stat:
    minimum: float
    maximum: float
    mean: float


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    first_ts: datetime
    last_ts: datetime
    metrics: Mapping[str, Stat]
    latency_p95_ms: float


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(samples: Sequence[MetricSample]) -> Summary | None:
    if not samples:
        return None
    metrics = {}
    for name in METRIC_NAMES:
        values = [getattr(sample, name) for sample in samples]
        metrics[name] = Stat(min(values), max(values), sum(values) / len(values))
    return Summary(
        count=len(samples),
        first_ts=min(s.ts for s in samples),
        last_ts=max(s.ts for s in samples),
        metrics=metrics,
        latency_p95_ms=_nearest_rank([s.latency_ms for s in samples], 95),
    )


@dataclass(frozen=True, slots=True)
class Delta:
    a: float
    b: float
    change: float
    change_pct: float | None  # None when the baseline is zero


def compare(a: Summary, b: Summary) -> dict[str, Delta]:
    """How each metric's mean moved going from a to b."""
    result = {}
    for name in METRIC_NAMES:
        before, after = a.metrics[name].mean, b.metrics[name].mean
        result[name] = Delta(
            a=before,
            b=after,
            change=after - before,
            change_pct=None if before == 0 else (after - before) / before * 100,
        )
    return result
