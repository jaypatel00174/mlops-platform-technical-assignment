"""Approve or reject a version: governance decides, the registry applies, the audit records."""

from __future__ import annotations

from mlops.governance.application.ports import ApprovalRepository
from mlops.governance.domain import approval as approval_rules
from mlops.kernel.identity import Principal
from mlops.kernel.ports import AuditRecorder, Clock, IdGenerator
from mlops.registry.application.services import ModelVersion, RegistryService


class ApprovalService:
    def __init__(
        self,
        *,
        registry: RegistryService,
        approvals: ApprovalRepository,
        audit: AuditRecorder,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._registry = registry
        self._approvals = approvals
        self._audit = audit
        self._clock = clock
        self._ids = ids

    def decide(
        self,
        *,
        principal: Principal,
        model_id: str,
        version: str,
        approve: bool,
        comment: str | None,
        if_match: str | None = None,
    ) -> ModelVersion:
        current = self._registry.get_version(model_id, version)
        # Role, separation of duties, stage and the rejection reason are all enforced here.
        decision = approval_rules.decide(
            version_id=current.id,
            version_stage=current.stage,
            registered_by=current.registered_by,
            decided_by=principal.subject,
            roles=principal.roles,
            approve=approve,
            comment=comment,
            now=self._clock.now(),
        )
        before, after = self._registry.apply_approval(
            model_id=model_id, version=version, approved=approve, if_match=if_match
        )
        self._approvals.add(decision, id=self._ids.new_id("apr"))
        self._audit.record(
            actor=principal.subject,
            action="version.approved" if approve else "version.rejected",
            entity_type="model_version",
            entity_id=after.id,
            before={"stage": before.stage.value, "approval_status": before.approval_status.value},
            after={
                "stage": after.stage.value,
                "approval_status": after.approval_status.value,
                "comment": decision.comment,
            },
        )
        return after
