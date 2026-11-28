"""The seed loader against the real sample files in data/seed."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.domain.failure import FailureClass
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
)
from mlops.governance.infrastructure.repositories import SqlAuditRepository
from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.monitoring.infrastructure.repositories import SqlMetricSampleRepository
from mlops.platform.db import unit_of_work
from mlops.registry.infrastructure.repositories import SqlModelRepository, SqlModelVersionRepository
from mlops.seed import (
    CURATED_TAG,
    INFERRED_TAG,
    SeedReport,
    digest_for_uri,
    load_seed,
    main,
)
from support.builders import NOW

pytestmark = pytest.mark.integration

SEED_DIR = Path(__file__).resolve().parents[3] / "data" / "seed"


@pytest.fixture
def loaded(session_factory: sessionmaker[Session]) -> SeedReport:
    with unit_of_work(session_factory) as session:
        return load_seed(session, SEED_DIR, now=NOW)


def test_the_first_load_creates_everything_in_the_sample_files(loaded: SeedReport) -> None:
    assert loaded.models == 3  # 2 curated + valve-health-model inferred
    assert loaded.versions == 8  # 4 curated + 4 inferred
    assert loaded.deployments == 3
    assert loaded.audit_events == 3
    assert loaded.bindings == 1  # only dep-1001 succeeded
    assert loaded.metrics == 90


def test_loading_twice_changes_nothing(
    session_factory: sessionmaker[Session], loaded: SeedReport
) -> None:
    with unit_of_work(session_factory) as session:
        second = load_seed(session, SEED_DIR, now=NOW)

    assert second.total == 0
    assert loaded.total > 0


def test_curated_models_and_versions_keep_their_stage_and_approval(
    session_factory: sessionmaker[Session], loaded: SeedReport
) -> None:
    with session_factory() as session:
        versions = SqlModelVersionRepository(session)
        pump_v2 = versions.get("pump-failure-predictor", "2.0.0")
        pump_v1 = versions.get("pump-failure-predictor", "1.0.0")
        compressor = versions.get("compressor-anomaly-detector", "1.1.0")

    assert pump_v2 is not None
    assert pump_v2.stage is Stage.PRODUCTION
    assert pump_v2.approval_status is ApprovalStatus.APPROVED
    assert CURATED_TAG in pump_v2.tags
    assert pump_v2.artifact_uri == "s3://models/pump/2.0.0"
    assert pump_v2.artifact_digest == digest_for_uri("s3://models/pump/2.0.0")
    assert pump_v1 is not None
    assert pump_v1.stage is Stage.ARCHIVED
    # scenario 3 fixture: validated but never approved
    assert compressor is not None
    assert compressor.stage is Stage.VALIDATED
    assert compressor.approval_status is ApprovalStatus.PENDING
    assert loaded.versions > 0


def test_records_only_the_metrics_or_events_mention_are_created_and_marked_inferred(
    session_factory: sessionmaker[Session], loaded: SeedReport
) -> None:
    with session_factory() as session:
        valve = SqlModelRepository(session).get("valve-health-model")
        versions = SqlModelVersionRepository(session).list_for_model("valve-health-model")
        compressor_v2 = SqlModelVersionRepository(session).get(
            "compressor-anomaly-detector", "2.0.0"
        )

    assert valve is not None
    assert valve.owner == "unknown"
    assert INFERRED_TAG in valve.tags
    assert [v.version for v in versions] == ["1.0.0", "2.0.0", "3.0.0"]
    assert all(v.stage is Stage.DRAFT and INFERRED_TAG in v.tags for v in versions)
    assert compressor_v2 is not None
    assert INFERRED_TAG in compressor_v2.tags
    assert loaded.models == 3


def test_deployment_events_become_classified_history(
    session_factory: sessionmaker[Session], loaded: SeedReport
) -> None:
    with session_factory() as session:
        deployments = SqlDeploymentRepository(session)
        succeeded = deployments.get("dep-1001")
        policy = deployments.get("dep-1002")
        timeout = deployments.get("dep-1003")
        attempts = deployments.attempts("dep-1003")

    assert succeeded is not None
    assert succeeded.status is DeploymentStatus.SUCCEEDED
    assert succeeded.failure_class is None
    assert policy is not None
    assert policy.status is DeploymentStatus.FAILED
    assert policy.failure_class is FailureClass.POLICY
    assert policy.error == "approval_validation_failed"
    assert timeout is not None
    assert timeout.failure_class is FailureClass.TRANSIENT
    assert [a.outcome for a in attempts] == [AttemptOutcome.FAILED]
    assert loaded.deployments == 3


def test_the_successful_deployment_is_live_and_everything_has_an_audit_trail(
    session_factory: sessionmaker[Session], loaded: SeedReport
) -> None:
    with session_factory() as session:
        binding = SqlEnvironmentBindingRepository(session).get(
            "pump-failure-predictor", "production"
        )
        live_in = SqlEnvironmentBindingRepository(session).live_environments(
            "pump-failure-predictor", "2.0.0"
        )
        trail = SqlAuditRepository(session).list_for_entity("deployment", "dep-1002")

    assert binding is not None
    assert binding.current_deployment_id == "dep-1001"
    assert live_in == ["production"]
    assert [event.action for event in trail] == ["deployment.approval_validation_failed"]
    assert loaded.audit_events == 3


def test_all_metric_rows_are_loaded_per_model(
    session_factory: sessionmaker[Session], loaded: SeedReport
) -> None:
    with session_factory() as session:
        metrics = SqlMetricSampleRepository(session)
        counts = {
            model: metrics.count(model)
            for model in (
                "pump-failure-predictor",
                "compressor-anomaly-detector",
                "valve-health-model",
            )
        }
        pump_v1 = metrics.list_for(
            model_id="pump-failure-predictor", version="1.0.0", environment="production"
        )

    assert counts == {
        "pump-failure-predictor": 30,
        "compressor-anomaly-detector": 30,
        "valve-health-model": 30,
    }
    assert len(pump_v1) == 15
    assert pump_v1[0].latency_ms == pytest.approx(86.33)
    assert loaded.metrics == 90


def test_the_command_line_entry_point_loads_and_then_reports_nothing_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mlops.platform.db import make_engine
    from support.database import migrate

    url = f"sqlite+pysqlite:///{tmp_path / 'cli.db'}"
    engine = make_engine(url)
    migrate(engine)
    engine.dispose()
    monkeypatch.chdir(tmp_path)

    assert main(["--data-dir", str(SEED_DIR), "--database-url", url]) == 0
    first = capsys.readouterr().out
    assert main(["--data-dir", str(SEED_DIR), "--database-url", url]) == 0
    second = capsys.readouterr().out

    assert "created 3 models, 8 versions, 3 deployments, 3 audit events, 1 bindings" in first
    assert "90 metric samples" in first
    assert "nothing to do" in second
