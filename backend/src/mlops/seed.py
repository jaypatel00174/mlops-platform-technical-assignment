"""Load the sample data from data/seed into the database.

    python -m mlops.seed --data-dir ../data/seed          (or: make seed)

The loader is idempotent: every record is created only if it does not exist, so running it twice
changes nothing. The sample files disagree with each other (models and versions that appear only
in the metrics or the events), so the loader creates the missing records instead of dropping data
and tags them `source:seed-inferred`. See data/README.md for the full list.

This module is a composition root: it is allowed to use several feature modules together.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mlops.deployment.domain.entities import Deployment, EnvironmentBinding
from mlops.deployment.domain.failure import FailureClass
from mlops.deployment.domain.status import AttemptOutcome, DeploymentStatus, DeploymentType
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
)
from mlops.governance.domain.audit import AuditEvent
from mlops.governance.infrastructure.repositories import SqlAuditRepository
from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.infrastructure.repositories import SqlMetricSampleRepository
from mlops.platform.config import get_settings
from mlops.platform.db import make_engine, make_session_factory, unit_of_work
from mlops.registry.domain.entities import Model, ModelVersion
from mlops.registry.infrastructure.repositories import SqlModelRepository, SqlModelVersionRepository

SEED_ACTOR = "seed-loader"
CURATED_TAG = "source:seed"
INFERRED_TAG = "source:seed-inferred"

# Free-text event names from deployment_events.json mapped to failure classes.
EVENT_FAILURE_CLASS = {
    "approval_validation_failed": FailureClass.POLICY,
    "runtime_timeout": FailureClass.TRANSIENT,
}


@dataclass(slots=True)
class SeedReport:
    """How many records this run created. All zeros means the data was already loaded."""

    models: int = 0
    versions: int = 0
    deployments: int = 0
    audit_events: int = 0
    bindings: int = 0
    metrics: int = 0

    @property
    def total(self) -> int:
        return (
            self.models
            + self.versions
            + self.deployments
            + self.audit_events
            + self.bindings
            + self.metrics
        )

    def summary(self) -> str:
        return (
            f"created {self.models} models, {self.versions} versions, {self.deployments} "
            f"deployments, {self.audit_events} audit events, {self.bindings} bindings, "
            f"{self.metrics} metric samples"
        )


def digest_for_uri(uri: str) -> str:
    """A deterministic stand-in digest. The sample registry has none, real ones come from upload."""
    return "sha256:" + hashlib.sha256(uri.encode("utf-8")).hexdigest()


def _display_name(model_id: str) -> str:
    return model_id.replace("-", " ").title()


def load_seed(
    session: Session,
    data_dir: Path,
    *,
    tenant_id: str = "default",
    now: datetime | None = None,
) -> SeedReport:
    registry: list[dict[str, Any]] = json.loads((data_dir / "registry.json").read_text("utf-8"))
    events: list[dict[str, Any]] = json.loads(
        (data_dir / "deployment_events.json").read_text("utf-8")
    )
    with (data_dir / "model_metrics.csv").open(encoding="utf-8", newline="") as handle:
        metric_rows = list(csv.DictReader(handle))

    clock = now or datetime.now(UTC)
    models = SqlModelRepository(session, tenant_id)
    versions = SqlModelVersionRepository(session, tenant_id)
    deployments = SqlDeploymentRepository(session, tenant_id)
    bindings = SqlEnvironmentBindingRepository(session, tenant_id)
    audit = SqlAuditRepository(session, tenant_id)
    metrics = SqlMetricSampleRepository(session, tenant_id)
    report = SeedReport()

    def ensure_model(model_id: str, *, name: str, owner: str, framework: str, tag: str) -> None:
        if models.get(model_id) is None:
            models.add(
                Model.register(
                    id=model_id,
                    name=name,
                    owner=owner,
                    framework=framework,
                    now=clock,
                    tags=(tag,),
                    tenant_id=tenant_id,
                )
            )
            report.models += 1

    def ensure_version(
        model_id: str,
        version: str,
        *,
        uri: str,
        stage: Stage,
        approval: ApprovalStatus,
        tag: str,
    ) -> None:
        if versions.get(model_id, version) is None:
            created = ModelVersion.register(
                id=f"{model_id}@{version}",
                model_id=model_id,
                version=version,
                algorithm="unspecified",
                artifact_uri=uri,
                artifact_digest=digest_for_uri(uri),
                training_data_ref="unspecified",
                registered_by=SEED_ACTOR,
                now=clock,
                tags=(tag,),
                tenant_id=tenant_id,
            )
            # Imported history: stage and approval are taken as given, not re-derived.
            versions.add(replace(created, stage=stage, approval_status=approval))
            report.versions += 1

    # 1. The curated registry.
    for entry in registry:
        ensure_model(
            entry["model_id"],
            name=entry["name"],
            owner=entry["owner"],
            framework=entry["framework"],
            tag=CURATED_TAG,
        )
        for item in entry["versions"]:
            ensure_version(
                entry["model_id"],
                item["version"],
                uri=item["artifact_uri"],
                stage=Stage(item["stage"]),
                approval=ApprovalStatus.APPROVED if item["approved"] else ApprovalStatus.PENDING,
                tag=CURATED_TAG,
            )

    # 2. Models and versions that only the events or the metrics mention.
    referenced: dict[tuple[str, str], None] = {}
    for event in events:
        referenced[(event["model_id"], event["version"])] = None
    for row in metric_rows:
        referenced[(row["model_id"], row["version"])] = None
    for model_id, version in referenced:
        ensure_model(
            model_id,
            name=_display_name(model_id),
            owner="unknown",
            framework="unknown",
            tag=INFERRED_TAG,
        )
        ensure_version(
            model_id,
            version,
            uri=f"s3://models/{model_id}/{version}",
            stage=Stage.DRAFT,
            approval=ApprovalStatus.PENDING,
            tag=INFERRED_TAG,
        )

    # 3. Deployment history, its audit trail, and what is live.
    for event in events:
        deployment_id: str = event["deployment_id"]
        if deployments.get(deployment_id) is not None:
            continue
        happened = datetime.fromisoformat(event["timestamp"])
        status = DeploymentStatus(event["status"])
        failed = status is DeploymentStatus.FAILED
        failure_class = (
            EVENT_FAILURE_CLASS.get(event["event"], FailureClass.INTERNAL) if failed else None
        )
        deployments.add(
            Deployment(
                id=deployment_id,
                model_id=event["model_id"],
                version=event["version"],
                environment=event["environment"],
                type=DeploymentType.DEPLOY,
                status=status,
                attempt=1,
                requested_by=SEED_ACTOR,
                correlation_id=f"seed-{deployment_id}",
                created_at=happened,
                updated_at=happened,
                failure_class=failure_class,
                error=event["event"] if failed else None,
                tenant_id=tenant_id,
            )
        )
        deployments.record_attempt_started(deployment_id, 1, happened)
        deployments.record_attempt_finished(
            deployment_id,
            1,
            outcome=AttemptOutcome.FAILED if failed else AttemptOutcome.SUCCEEDED,
            now=happened,
            error=event["event"] if failed else None,
        )
        report.deployments += 1

        audit.add(
            AuditEvent(
                id=f"seed-audit-{deployment_id}",
                actor=SEED_ACTOR,
                action=f"deployment.{event['event']}",
                entity_type="deployment",
                entity_id=deployment_id,
                correlation_id=f"seed-{deployment_id}",
                occurred_at=happened,
                after={
                    "status": status.value,
                    "environment": event["environment"],
                    "version": event["version"],
                },
                tenant_id=tenant_id,
            )
        )
        report.audit_events += 1

        if status is DeploymentStatus.SUCCEEDED:
            binding = bindings.get(event["model_id"], event["environment"]) or EnvironmentBinding(
                model_id=event["model_id"], environment=event["environment"], tenant_id=tenant_id
            )
            bindings.save(binding.after_success(deployment_id), happened)
            report.bindings += 1

    # 4. Metric samples (duplicates are skipped by the natural key).
    samples = [
        MetricSample(
            model_id=row["model_id"],
            version=row["version"],
            environment=row["environment"],
            ts=datetime.fromisoformat(row["timestamp"]),
            latency_ms=float(row["latency_ms"]),
            throughput_rpm=float(row["throughput_rpm"]),
            error_rate=float(row["error_rate"]),
            quality_score=float(row["quality_score"]),
            drift_score=float(row["drift_score"]),
            availability=float(row["availability"]),
            tenant_id=tenant_id,
        )
        for row in metric_rows
    ]
    report.metrics = metrics.add_many(samples)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mlops.seed", description="Load the sample data (idempotent)."
    )
    parser.add_argument("--data-dir", type=Path, required=True, help="folder with the seed files")
    parser.add_argument("--database-url", default=None, help="defaults to DATABASE_URL")
    args = parser.parse_args(argv)

    settings = get_settings()
    engine = make_engine(args.database_url or settings.database_url)
    try:
        with unit_of_work(make_session_factory(engine)) as session:
            report = load_seed(session, args.data_dir, tenant_id=settings.default_tenant)
    finally:
        engine.dispose()
    print(report.summary() if report.total else "nothing to do: the seed data is already loaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
