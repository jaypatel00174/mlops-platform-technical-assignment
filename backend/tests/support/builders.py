"""Builders for valid domain objects, so each test states only what matters to it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from mlops.deployment.domain.entities import Deployment
from mlops.registry.domain.entities import Model, ModelVersion
from mlops.registry.infrastructure.repositories import SqlModelRepository, SqlModelVersionRepository

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def at(minutes: int = 0) -> datetime:
    return NOW + timedelta(minutes=minutes)


def make_model(model_id: str = "pump-failure-predictor", **overrides: Any) -> Model:
    values: dict[str, Any] = {
        "id": model_id,
        "name": "Pump Failure Predictor",
        "owner": "Reliability AI Team",
        "framework": "scikit-learn",
        "now": NOW,
        "tags": ("plant-a",),
    }
    values.update(overrides)
    return Model.register(**values)


def make_version(
    model_id: str = "pump-failure-predictor", version: str = "2.0.0", **overrides: Any
) -> ModelVersion:
    values: dict[str, Any] = {
        "id": f"{model_id}@{version}",
        "model_id": model_id,
        "version": version,
        "algorithm": "gradient-boosting",
        "artifact_uri": f"s3://models/{model_id}/{version}",
        "artifact_digest": DIGEST,
        "training_data_ref": "s3://datasets/pump/2026-06",
        "registered_by": "alice",
        "now": NOW,
        "metadata": {"accuracy": 0.93},
        "tags": ("candidate",),
    }
    values.update(overrides)
    return ModelVersion.register(**values)


def make_deployment(
    deployment_id: str = "dep-1",
    *,
    model_id: str = "pump-failure-predictor",
    version: str = "2.0.0",
    environment: str = "production",
    **overrides: Any,
) -> Deployment:
    values: dict[str, Any] = {
        "id": deployment_id,
        "model_id": model_id,
        "version": version,
        "environment": environment,
        "requested_by": "carol",
        "correlation_id": f"corr-{deployment_id}",
        "now": NOW,
        "idempotency_key": f"key-{deployment_id}-12345",
    }
    values.update(overrides)
    return Deployment.request(**values)


def persist_model_and_version(
    session: Session,
    model_id: str = "pump-failure-predictor",
    versions: tuple[str, ...] = ("2.0.0",),
    tenant_id: str = "default",
) -> None:
    """Insert a model and the given versions, then flush (the caller decides when to commit)."""
    SqlModelRepository(session, tenant_id).add(make_model(model_id, tenant_id=tenant_id))
    version_repo = SqlModelVersionRepository(session, tenant_id)
    for version in versions:
        version_repo.add(make_version(model_id, version, tenant_id=tenant_id))
