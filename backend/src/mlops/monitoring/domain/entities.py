"""Monitoring entities: one observation of how a deployed model version is behaving."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from mlops.kernel.errors import ValidationFailure


def _required(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValidationFailure(f"{field_name} must not be blank", field=field_name)


def _in_range(field_name: str, value: float, low: float, high: float | None = None) -> None:
    if math.isnan(value) or math.isinf(value) or value < low or (high is not None and value > high):
        upper = "" if high is None else f" and {high}"
        raise ValidationFailure(
            f"{field_name} must be between {low}{upper}", field=field_name, value=str(value)
        )


@dataclass(frozen=True, slots=True)
class MetricSample:
    model_id: str
    version: str
    environment: str
    ts: datetime
    latency_ms: float
    throughput_rpm: float
    error_rate: float  # fraction of failed requests, 0 to 1
    quality_score: float  # 0 to 1
    drift_score: float  # 0 and up; higher means more drift
    availability: float  # percent, 0 to 100
    tenant_id: str = "default"

    def __post_init__(self) -> None:
        _required("model_id", self.model_id)
        _required("version", self.version)
        _required("environment", self.environment)
        if self.ts.tzinfo is None:
            raise ValidationFailure("ts must be timezone-aware", field="ts")
        _in_range("latency_ms", self.latency_ms, 0.0)
        _in_range("throughput_rpm", self.throughput_rpm, 0.0)
        _in_range("error_rate", self.error_rate, 0.0, 1.0)
        _in_range("quality_score", self.quality_score, 0.0, 1.0)
        _in_range("drift_score", self.drift_score, 0.0)
        _in_range("availability", self.availability, 0.0, 100.0)
