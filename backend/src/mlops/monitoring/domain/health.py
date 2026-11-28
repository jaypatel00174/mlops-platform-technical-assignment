"""Model health: a verdict on how a deployed version is behaving, with reasons.

The rules are deliberately simple and written down, because an operator must be able to predict
what the platform will say:

* No samples at all is NO_DATA. Samples that are older than `stale_after` are STALE, however good
  the numbers were: silence is not health.
* Otherwise the metrics are averaged over a short window ending at the newest sample (so one
  spike does not flip the status) and each average is compared with its thresholds.
* The worst breach decides: CRITICAL over DEGRADED over HEALTHY. Every breach is listed as a
  reason, so the UI can say why.

Thresholds are one default set today. Per-model thresholds are a roadmap item (docs/leadership).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.domain.series import METRIC_NAMES


class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    STALE = "STALE"
    NO_DATA = "NO_DATA"


# What to look at first when several things are wrong, most urgent first.
_URGENCY = (
    HealthStatus.CRITICAL,
    HealthStatus.DEGRADED,
    HealthStatus.STALE,
    HealthStatus.NO_DATA,
    HealthStatus.HEALTHY,
)


@dataclass(frozen=True, slots=True)
class Limit:
    """Where a metric becomes DEGRADED and where it becomes CRITICAL."""

    degraded: float
    critical: float
    higher_is_worse: bool = True

    def severity(self, value: float) -> HealthStatus | None:
        if self.higher_is_worse:
            if value >= self.critical:
                return HealthStatus.CRITICAL
            if value >= self.degraded:
                return HealthStatus.DEGRADED
        else:
            if value < self.critical:
                return HealthStatus.CRITICAL
            if value < self.degraded:
                return HealthStatus.DEGRADED
        return None


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    error_rate: Limit = Limit(degraded=0.02, critical=0.05)
    latency_ms: Limit = Limit(degraded=200.0, critical=500.0)
    drift_score: Limit = Limit(degraded=0.30, critical=0.50)
    quality_score: Limit = Limit(degraded=0.80, critical=0.70, higher_is_worse=False)
    availability: Limit = Limit(degraded=99.0, critical=95.0, higher_is_worse=False)
    stale_after: timedelta = timedelta(hours=24)
    window: timedelta = timedelta(hours=1)

    def limits(self) -> dict[str, Limit]:
        return {
            "error_rate": self.error_rate,
            "latency_ms": self.latency_ms,
            "drift_score": self.drift_score,
            "quality_score": self.quality_score,
            "availability": self.availability,
        }


@dataclass(frozen=True, slots=True)
class Reason:
    metric: str
    severity: HealthStatus
    message: str
    value: float | None = None
    threshold: float | None = None


@dataclass(frozen=True, slots=True)
class HealthReport:
    status: HealthStatus
    reasons: tuple[Reason, ...] = ()
    last_sample_at: datetime | None = None
    samples_evaluated: int = 0
    window_means: dict[str, float] = field(default_factory=dict)


def worst_status(statuses: Iterable[HealthStatus]) -> HealthStatus:
    """The most urgent of several statuses; NO_DATA when there are none to consider."""
    present = set(statuses)
    for status in _URGENCY:
        if status in present:
            return status
    return HealthStatus.NO_DATA


def _age(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds >= 3600:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    minutes = max(seconds // 60, 0)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def evaluate(
    samples: Sequence[MetricSample],
    now: datetime,
    thresholds: HealthThresholds | None = None,
) -> HealthReport:
    limits = thresholds or HealthThresholds()
    if not samples:
        return HealthReport(
            status=HealthStatus.NO_DATA,
            reasons=(Reason("data", HealthStatus.NO_DATA, "no metrics have been received"),),
        )

    latest = max(sample.ts for sample in samples)
    age = now - latest
    if age > limits.stale_after:
        return HealthReport(
            status=HealthStatus.STALE,
            reasons=(
                Reason(
                    "data",
                    HealthStatus.STALE,
                    f"the last sample was {_age(age)} ago (limit {_age(limits.stale_after)})",
                ),
            ),
            last_sample_at=latest,
        )

    recent = [sample for sample in samples if sample.ts > latest - limits.window]
    means = {
        name: sum(getattr(sample, name) for sample in recent) / len(recent) for name in METRIC_NAMES
    }
    reasons: list[Reason] = []
    for name, limit in limits.limits().items():
        severity = limit.severity(means[name])
        if severity is None:
            continue
        boundary = limit.critical if severity is HealthStatus.CRITICAL else limit.degraded
        comparison = "at or above" if limit.higher_is_worse else "below"
        reasons.append(
            Reason(
                metric=name,
                severity=severity,
                message=(
                    f"{name} averaged {means[name]:.4g} over the last "
                    f"{_age(limits.window)}, {comparison} the {severity.value.lower()} "
                    f"limit of {boundary:g}"
                ),
                value=means[name],
                threshold=boundary,
            )
        )
    reasons.sort(key=lambda reason: _URGENCY.index(reason.severity))
    return HealthReport(
        status=worst_status(reason.severity for reason in reasons)
        if reasons
        else HealthStatus.HEALTHY,
        reasons=tuple(reasons),
        last_sample_at=latest,
        samples_evaluated=len(recent),
        window_means=means,
    )
