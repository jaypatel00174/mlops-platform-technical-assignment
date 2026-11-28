"""What the monitoring application layer needs from storage (the MetricsStore port)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from mlops.monitoring.domain.entities import MetricSample

# model_id -> [(version, environment)] currently live. Supplied by the deployment module.
LiveVersions = Callable[[str], list[tuple[str, str]]]


class MetricsStore(Protocol):
    def add_many(self, samples: Sequence[MetricSample]) -> int:
        """Insert samples, skipping ones that already exist. Returns how many were new."""
        ...

    def latest(self, model_id: str, version: str, environment: str) -> MetricSample | None: ...

    def series_keys(self, model_id: str) -> list[tuple[str, str]]: ...

    def list_for(
        self,
        *,
        model_id: str,
        version: str,
        environment: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[MetricSample]: ...
