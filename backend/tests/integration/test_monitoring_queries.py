"""The queries behind the monitoring API, on the real schema."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from mlops.deployment.domain.entities import EnvironmentBinding
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
)
from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.infrastructure.repositories import SqlMetricSampleRepository
from support.builders import NOW, at, make_deployment, persist_model_and_version

pytestmark = pytest.mark.integration


def sample(minutes: int, version: str = "2.0.0", environment: str = "production") -> MetricSample:
    return MetricSample(
        model_id="pump-failure-predictor",
        version=version,
        environment=environment,
        ts=at(minutes),
        latency_ms=90.0,
        throughput_rpm=1000.0,
        error_rate=0.01,
        quality_score=0.9,
        drift_score=0.1,
        availability=99.9,
    )


def seeded(session: Session) -> SqlMetricSampleRepository:
    persist_model_and_version(session, versions=("1.0.0", "2.0.0"))
    repo = SqlMetricSampleRepository(session)
    repo.add_many([sample(m) for m in (0, 10, 20)])
    repo.add_many([sample(5, version="1.0.0"), sample(7, environment="staging")])
    return repo


def test_latest_is_the_newest_sample_of_that_series(session: Session) -> None:
    repo = seeded(session)

    latest = repo.latest("pump-failure-predictor", "2.0.0", "production")

    assert latest is not None
    assert latest.ts == at(20)
    assert repo.latest("pump-failure-predictor", "2.0.0", "dev") is None
    assert repo.latest("pump-failure-predictor", "1.0.0", "staging") is None


def test_series_keys_lists_each_version_and_environment_once(session: Session) -> None:
    repo = seeded(session)

    assert repo.series_keys("pump-failure-predictor") == [
        ("1.0.0", "production"),
        ("2.0.0", "production"),
        ("2.0.0", "staging"),
    ]
    assert repo.series_keys("other-model") == []


def test_a_limit_caps_the_rows_oldest_first(session: Session) -> None:
    repo = seeded(session)

    rows = repo.list_for(
        model_id="pump-failure-predictor", version="2.0.0", environment="production", limit=2
    )

    assert [r.ts for r in rows] == [at(0), at(10)]


def test_a_range_is_inclusive_at_the_start_and_exclusive_at_the_end(session: Session) -> None:
    repo = seeded(session)

    rows = repo.list_for(
        model_id="pump-failure-predictor",
        version="2.0.0",
        environment="production",
        start=at(10),
        end=at(20),
    )

    assert [r.ts for r in rows] == [at(10)]


def test_live_for_model_lists_what_each_environment_currently_serves(session: Session) -> None:
    persist_model_and_version(session, versions=("1.0.0", "2.0.0"))
    deployments = SqlDeploymentRepository(session)
    deployments.add(make_deployment("dep-1", version="1.0.0", environment="staging"))
    deployments.add(make_deployment("dep-2", version="2.0.0", environment="dev"))
    bindings = SqlEnvironmentBindingRepository(session)
    for deployment_id, environment in (("dep-1", "staging"), ("dep-2", "dev")):
        bindings.save(
            EnvironmentBinding(
                model_id="pump-failure-predictor",
                environment=environment,
                current_deployment_id=deployment_id,
            ),
            NOW + timedelta(minutes=1),
        )

    assert bindings.live_for_model("pump-failure-predictor") == [
        ("2.0.0", "dev"),
        ("1.0.0", "staging"),
    ]
    assert bindings.live_for_model("other-model") == []
