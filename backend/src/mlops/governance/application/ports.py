"""What the governance application layer needs from storage."""

from __future__ import annotations

from typing import Protocol

from mlops.governance.domain.approval import ApprovalDecision
from mlops.governance.domain.audit import AuditEvent


class AuditRepository(Protocol):
    def add(self, event: AuditEvent) -> None: ...

    def list_for_entity(self, entity_type: str, entity_id: str) -> list[AuditEvent]: ...


class ApprovalRepository(Protocol):
    def add(self, decision: ApprovalDecision, *, id: str) -> None: ...
