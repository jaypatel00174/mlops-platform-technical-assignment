"""Audit trail: implements the AuditRecorder port that every module uses."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mlops.governance.application.ports import AuditRepository
from mlops.governance.domain import audit as audit_rules
from mlops.governance.domain.audit import AuditEvent
from mlops.kernel.ports import Clock, IdGenerator
from mlops.platform.context import get_correlation_id


class AuditService:
    def __init__(
        self, *, repository: AuditRepository, clock: Clock, ids: IdGenerator, tenant_id: str
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = ids
        self._tenant_id = tenant_id

    def record(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
    ) -> None:
        """Append an event. The correlation ID of the current request is attached automatically,
        so one ID links the client's error, the logs and the audit trail."""
        self._repository.add(
            audit_rules.record(
                id=self._ids.new_id("aud"),
                actor=actor,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                correlation_id=get_correlation_id(),
                now=self._clock.now(),
                before=before,
                after=after,
                tenant_id=self._tenant_id,
            )
        )

    def history(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        return self._repository.list_for_entity(entity_type, entity_id)
