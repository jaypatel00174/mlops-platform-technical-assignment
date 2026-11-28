from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mlops.kernel.errors import ValidationFailure
from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.domain.series import (
    Resolution,
    bucket_start,
    compare,
    downsample,
    summarize,
)

T0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def sample(ts: datetime, latency: float = 100.0, error_rate: float = 0.01) -> MetricSample:
    return MetricSample(
        model_id="pump",
        version="1.0.0",
        environment="production",
        ts=ts,
        latency_ms=latency,
        throughput_rpm=1000.0,
        error_rate=error_rate,
        quality_score=0.9,
        drift_score=0.1,
        availability=99.9,
    )


def test_buckets_start_on_the_hour_and_the_utc_day() -> None:
    moment = datetime(2026, 7, 3, 14, 37, 12, tzinfo=UTC)

    assert bucket_start(moment, Resolution.HOUR) == datetime(2026, 7, 3, 14, 0, tzinfo=UTC)
    assert bucket_start(moment, Resolution.DAY) == datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    assert bucket_start(moment, Resolution.RAW) == moment


def test_raw_resolution_returns_every_sample_in_time_order() -> None:
    samples = [sample(T0 + timedelta(minutes=2)), sample(T0), sample(T0 + timedelta(minutes=1))]

    points = downsample(samples, Resolution.RAW)

    assert [p.ts for p in points] == [T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]
    assert {p.samples for p in points} == {1}


def test_hourly_points_are_the_mean_of_the_samples_in_that_hour() -> None:
    samples = [
        sample(T0 + timedelta(minutes=10), latency=100.0),
        sample(T0 + timedelta(minutes=50), latency=200.0),
        sample(T0 + timedelta(hours=1, minutes=5), latency=400.0),
    ]

    first, second = downsample(samples, Resolution.HOUR)

    assert (first.ts, first.latency_ms, first.samples) == (T0, 150.0, 2)
    assert (second.ts, second.latency_ms, second.samples) == (T0 + timedelta(hours=1), 400.0, 1)


def test_downsampling_nothing_gives_nothing() -> None:
    assert downsample([], Resolution.DAY) == []


def test_summary_reports_min_max_mean_and_p95_latency() -> None:
    samples = [sample(T0 + timedelta(minutes=i), latency=float(i + 1)) for i in range(100)]

    summary = summarize(samples)

    assert summary is not None
    assert summary.count == 100
    assert summary.metrics["latency_ms"].minimum == 1.0
    assert summary.metrics["latency_ms"].maximum == 100.0
    assert summary.metrics["latency_ms"].mean == 50.5
    assert summary.latency_p95_ms == 95.0  # nearest rank
    assert summary.first_ts == T0
    assert summary.last_ts == T0 + timedelta(minutes=99)


def test_the_summary_of_nothing_is_none() -> None:
    assert summarize([]) is None


def test_comparison_shows_the_change_from_a_to_b_per_metric() -> None:
    a = summarize([sample(T0, latency=100.0, error_rate=0.02)])
    b = summarize([sample(T0, latency=80.0, error_rate=0.01)])
    assert a is not None
    assert b is not None

    deltas = compare(a, b)

    assert deltas["latency_ms"].change == pytest.approx(-20.0)
    assert deltas["latency_ms"].change_pct == pytest.approx(-20.0)
    assert deltas["error_rate"].change == pytest.approx(-0.01)
    assert deltas["error_rate"].change_pct == pytest.approx(-50.0)


def test_comparing_against_a_zero_baseline_has_no_percentage() -> None:
    a = summarize([sample(T0, error_rate=0.0)])
    b = summarize([sample(T0, error_rate=0.02)])
    assert a is not None
    assert b is not None

    assert compare(a, b)["error_rate"].change_pct is None


def test_an_unknown_resolution_is_a_validation_error() -> None:
    with pytest.raises(ValidationFailure) as error:
        Resolution.parse("minute")

    assert error.value.details["field"] == "resolution"
    assert Resolution.parse("hour") is Resolution.HOUR
