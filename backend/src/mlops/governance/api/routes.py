"""Governance endpoints: the audit trail. (Approve and reject are on the version resource.)"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from mlops.governance.application.access import Action, authorize
from mlops.governance.domain.audit import AuditEvent
from mlops.platform.auth import CurrentPrincipal
from mlops.platform.problem import PROBLEM_RESPONSES
from mlops.wiring import Uow

router = APIRouter(prefix="/api/v1", tags=["governance"], responses=PROBLEM_RESPONSES)


class AuditEventOut(BaseModel):
    id: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    correlation_id: str
    occurred_at: datetime
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class AuditListOut(BaseModel):
    items: list[AuditEventOut]


def _out(event: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=event.id,
        actor=event.actor,
        action=event.action,
        entity_type=event.entity_type,
        entity_id=event.entity_id,
        correlation_id=event.correlation_id,
        occurred_at=event.occurred_at,
        before=dict(event.before) if event.before is not None else None,
        after=dict(event.after) if event.after is not None else None,
    )


@router.get(
    "/audit-events",
    response_model=AuditListOut,
    summary="The audit trail of one entity, oldest first",
    description="Entity types: model, model_version, deployment. A version's id is model@version.",
)
def list_audit_events(
    principal: CurrentPrincipal,
    uow: Uow,
    entity_type: Annotated[str, Query(min_length=1, max_length=64, examples=["model_version"])],
    entity_id: Annotated[
        str, Query(min_length=1, max_length=127, examples=["pump-failure-predictor@2.0.0"])
    ],
) -> AuditListOut:
    authorize(principal, Action.READ_AUDIT)
    with uow.transaction(principal) as services:
        events = services.audit.history(entity_type, entity_id)
    return AuditListOut(items=[_out(event) for event in events])
