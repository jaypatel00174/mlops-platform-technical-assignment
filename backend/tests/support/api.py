"""Constants and fakes shared by the API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

API_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
SECRET = "api-test-secret-that-is-long-enough-for-hs256-signing"

PUMP: dict[str, Any] = {
    "id": "pump-failure-predictor",
    "name": "Pump Failure Predictor",
    "owner": "Reliability AI Team",
    "framework": "scikit-learn",
    "tags": ["plant-a"],
}


def version_payload(version: str = "1.0.0", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "version": version,
        "algorithm": "gradient-boosting",
        "artifact_uri": f"s3://models/pump/{version}",
        "artifact_digest": "sha256:" + "a" * 64,
        "training_data_ref": "s3://datasets/pump/2026-06",
        "metadata": {"accuracy": 0.93},
        "tags": ["candidate"],
    }
    body.update(overrides)
    return body


class FixedClock:
    def now(self) -> datetime:
        return API_NOW


class SequentialIds:
    """Predictable ids, so audit rows sort in the order they were written."""

    def __init__(self) -> None:
        self._count = 0

    def new_id(self, prefix: str) -> str:
        self._count += 1
        return f"{prefix}-{self._count:04d}"
