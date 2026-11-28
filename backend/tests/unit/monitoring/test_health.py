from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.domain.health import (
    HealthStatus,
    HealthThresholds,
    evaluate,
    worst_status,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
GOOD = {
    "latency_ms": 90.0,
    "throughput_rpm": 1000.0,
    "error_rate": 0.005,
    "quality_score": 0.88,
    "drift_score": 0.10,
    "availability": 99.8,
}


def sample(minutes_ago: float = 0, **overrides: float) -> MetricSample:
    values = {**GOOD, **overrides}
    return MetricSample(
        model_id="pump",
        version="1.0.0",
        environment="production",
        ts=NOW - timedelta(minutes=minutes_ago),
        **values,
    )


def test_no_samples_means_no_data() -> None:
    report = evaluate([], NOW)

    assert report.status is HealthStatus.NO_DATA
    assert report.last_sample_at is None
    assert report.reasons[0].metric == "data"


def test_good_numbers_are_healthy() -> None:
    report = evaluate([sample()], NOW)

    assert report.status is HealthStatus.HEALTHY
    assert report.reasons == ()
    assert report.last_sample_at == NOW


def test_old_data_is_stale_not_healthy_even_if_the_numbers_were_good() -> None:
    report = evaluate([sample(minutes_ago=25 * 60)], NOW)

    assert report.status is HealthStatus.STALE
    assert "25 hours" in report.reasons[0].message


def test_the_staleness_limit_is_configurable() -> None:
    thresholds = HealthThresholds(stale_after=timedelta(minutes=10))

    assert evaluate([sample(minutes_ago=11)], NOW, thresholds).status is HealthStatus.STALE
    assert evaluate([sample(minutes_ago=9)], NOW, thresholds).status is HealthStatus.HEALTHY


@pytest.mark.parametrize(
    ("metric", "value", "expected"),
    [
        ("error_rate", 0.019, HealthStatus.HEALTHY),
        ("error_rate", 0.02, HealthStatus.DEGRADED),
        ("error_rate", 0.05, HealthStatus.CRITICAL),
        ("latency_ms", 199.0, HealthStatus.HEALTHY),
        ("latency_ms", 200.0, HealthStatus.DEGRADED),
        ("latency_ms", 500.0, HealthStatus.CRITICAL),
        ("drift_score", 0.29, HealthStatus.HEALTHY),
        ("drift_score", 0.30, HealthStatus.DEGRADED),
        ("drift_score", 0.50, HealthStatus.CRITICAL),
        # lower is worse for quality and availability
        ("quality_score", 0.80, HealthStatus.HEALTHY),
        ("quality_score", 0.79, HealthStatus.DEGRADED),
        ("quality_score", 0.69, HealthStatus.CRITICAL),
        ("availability", 99.0, HealthStatus.HEALTHY),
        ("availability", 98.9, HealthStatus.DEGRADED),
        ("availability", 94.0, HealthStatus.CRITICAL),
    ],
)
def test_each_metric_crosses_its_thresholds_at_the_documented_value(
    metric: str, value: float, expected: HealthStatus
) -> None:
    assert evaluate([sample(**{metric: value})], NOW).status is expected


def test_the_worst_breach_decides_and_every_breach_is_explained() -> None:
    report = evaluate([sample(error_rate=0.06, drift_score=0.35)], NOW)

    assert report.status is HealthStatus.CRITICAL
    assert {(r.metric, r.severity) for r in report.reasons} == {
        ("error_rate", HealthStatus.CRITICAL),
        ("drift_score", HealthStatus.DEGRADED),
    }
    assert "error_rate" in report.reasons[0].message


def test_a_single_spike_in_the_window_is_averaged_out() -> None:
    # 59 normal minutes and one 1000 ms spike: the window mean stays under the 200 ms limit.
    samples = [sample(minutes_ago=m) for m in range(1, 60)] + [sample(latency_ms=1000.0)]

    assert evaluate(samples, NOW).status is HealthStatus.HEALTHY


def test_only_samples_inside_the_window_count() -> None:
    old_bad = sample(minutes_ago=120, error_rate=0.5)

    assert evaluate([old_bad, sample()], NOW).status is HealthStatus.HEALTHY


def test_input_order_does_not_matter() -> None:
    samples = [sample(minutes_ago=m, latency_ms=100.0 + m) for m in range(0, 30)]

    assert evaluate(samples, NOW) == evaluate(list(reversed(samples)), NOW)


def test_worst_status_ranks_severity_over_missing_data_over_healthy() -> None:
    assert worst_status([]) is HealthStatus.NO_DATA
    assert worst_status([HealthStatus.HEALTHY, HealthStatus.HEALTHY]) is HealthStatus.HEALTHY
    assert worst_status([HealthStatus.HEALTHY, HealthStatus.NO_DATA]) is HealthStatus.NO_DATA
    assert worst_status([HealthStatus.NO_DATA, HealthStatus.STALE]) is HealthStatus.STALE
    assert worst_status([HealthStatus.STALE, HealthStatus.DEGRADED]) is HealthStatus.DEGRADED
    assert worst_status([HealthStatus.DEGRADED, HealthStatus.CRITICAL]) is HealthStatus.CRITICAL
