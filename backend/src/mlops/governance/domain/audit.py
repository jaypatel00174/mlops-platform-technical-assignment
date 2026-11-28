"""Audit events: an immutable record of who did what, and when.

Audit rows are written in the same database transaction as the change they describe and are
append-only (enforced again by a database trigger in the migration).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mlops.kernel.errors import ValidationFailure


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    correlation_id: str
    occurred_at: datetime
    before: Mapping[str, Any] | None = None
    after: Mapping[str, Any] | None = None
    tenant_id: str = "default"


def _required(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValidationFailure(f"{field_name} must not be blank", field=field_name)
    return value.strip()


def record(
    *,
    id: str,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    correlation_id: str,
    now: datetime,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    tenant_id: str = "default",
) -> AuditEvent:
    return AuditEvent(
        id=id,
        actor=_required("actor", actor),
        action=_required("action", action),
        entity_type=_required("entity_type", entity_type),
        entity_id=_required("entity_id", entity_id),
        correlation_id=_required("correlation_id", correlation_id),
        occurred_at=now,
        # Snapshots are copied so later changes to the caller's dict cannot rewrite history.
        before=dict(before) if before is not None else None,
        after=dict(after) if after is not None else None,
        tenant_id=tenant_id,
    )
