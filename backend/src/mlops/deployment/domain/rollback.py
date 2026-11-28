"""Rollback safety rules (docs/architecture.md, section 7.3).

A rollback is not an undo button. It is a new deployment of the previous good version, and it is
only allowed when every precondition holds. The application layer gathers the facts (caller
permission is checked separately by governance) and these rules decide.
"""

from __future__ import annotations

from dataclasses import dataclass

from mlops.deployment.domain.status import DeploymentStatus
from mlops.kernel.errors import DeploymentConflict, UnsafeRollback
from mlops.kernel.vocabulary import ApprovalStatus, Stage


@dataclass(frozen=True, slots=True)
class RollbackFacts:
    environment: str
    previous_deployment_id: str | None
    previous_status: DeploymentStatus | None
    target_stage: Stage | None
    target_approval: ApprovalStatus | None
    artifact_digest_verified: bool
    runtime_compatible: bool
    in_flight_deployment_id: str | None


def check_rollback_allowed(facts: RollbackFacts) -> None:
    """Raise UnsafeRollback (listing every failed check) or DeploymentConflict.

    Unsafe-ness wins over conflict: there is no point reporting that something is in flight if
    the rollback would be refused anyway.
    """
    failed: list[str] = []

    if facts.previous_deployment_id is None:
        failed.append("NO_PREVIOUS_DEPLOYMENT")
    else:
        if facts.previous_status is not DeploymentStatus.SUCCEEDED:
            failed.append("PREVIOUS_DEPLOYMENT_NOT_SUCCEEDED")
        if facts.target_stage is Stage.ARCHIVED:
            failed.append("TARGET_ARCHIVED")
        if facts.target_approval is ApprovalStatus.REJECTED:
            failed.append("TARGET_REJECTED")
        elif facts.target_approval is not ApprovalStatus.APPROVED:
            failed.append("TARGET_NOT_APPROVED")
        if not facts.artifact_digest_verified:
            failed.append("ARTIFACT_DIGEST_MISMATCH")
        if not facts.runtime_compatible:
            failed.append("RUNTIME_INCOMPATIBLE")

    if failed:
        raise UnsafeRollback(
            "rolling back is not safe: " + ", ".join(failed),
            failed_checks=failed,
            environment=facts.environment,
        )
    if facts.in_flight_deployment_id is not None:
        raise DeploymentConflict(
            "another deployment is in progress for this model and environment",
            existing_deployment_id=facts.in_flight_deployment_id,
            environment=facts.environment,
        )
