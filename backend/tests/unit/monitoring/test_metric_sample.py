from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mlops.kernel.errors import ValidationFailure
from mlops.monitoring.domain.entities import MetricSample


def make_sample(**overrides: object) -> MetricSample:
    values: dict[str, object] = {
        "model_id": "pump-failure-predictor",
        "version": "2.0.0",
        "environment": "production",
        "ts": datetime(2026, 7, 16, tzinfo=UTC),
        "latency_ms": 94.23,
        "throughput_rpm": 1241.69,
        "error_rate": 0.0246,
        "quality_score": 0.9496,
        "drift_score": 0.0974,
        "availability": 99.415,
    }
    values.update(overrides)
    return MetricSample(**values)  # type: ignore[arg-type]


def test_a_realistic_sample_is_accepted() -> None:
    sample = make_sample()

    assert sample.model_id == "pump-failure-predictor"
    assert sample.tenant_id == "default"


def test_the_boundary_values_are_accepted() -> None:
    make_sample(
        error_rate=0.0, quality_score=1.0, availability=100.0, drift_score=0.0, latency_ms=0.0
    )
    make_sample(error_rate=1.0, quality_score=0.0, availability=0.0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latency_ms", -0.1),
        ("throughput_rpm", -1.0),
        ("error_rate", 1.01),
        ("error_rate", -0.01),
        ("quality_score", 1.2),
        ("quality_score", -0.2),
        ("drift_score", -0.5),
        ("availability", 100.1),
        ("availability", -1.0),
        ("latency_ms", float("nan")),
        ("error_rate", float("inf")),
    ],
)
def test_out_of_range_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_sample(**{field: value})

    assert error.value.details["field"] == field


def test_a_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationFailure) as error:
        make_sample(ts=datetime(2026, 7, 16))

    assert error.value.details["field"] == "ts"


@pytest.mark.parametrize("field", ["model_id", "version", "environment"])
def test_identifying_fields_cannot_be_blank(field: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_sample(**{field: " "})

    assert error.value.details["field"] == field
