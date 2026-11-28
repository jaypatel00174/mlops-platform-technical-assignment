"""Composition root for request-scoped services.

This is the one place that knows which concrete repositories back which services. Each request
runs in a single database transaction: the change, its audit entry and any approval record are
committed together, or none of them are. The API layers depend on this module only to receive
their services; the domain and application layers never see it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.infrastructure.repositories import SqlEnvironmentBindingRepository
from mlops.governance.application.approvals import ApprovalService
from mlops.governance.application.audit import AuditService
from mlops.governance.infrastructure.repositories import SqlApprovalRepository, SqlAuditRepository
from mlops.kernel.identity import Principal
from mlops.kernel.ports import Clock, IdGenerator
from mlops.platform.db import unit_of_work
from mlops.registry.application.services import RegistryService
from mlops.registry.infrastructure.repositories import SqlModelRepository, SqlModelVersionRepository


@dataclass(frozen=True, slots=True)
class Services:
    registry: RegistryService
    approvals: ApprovalService
    audit: AuditService


class UnitOfWorkFactory:
    def __init__(
        self, *, session_factory: sessionmaker[Session], clock: Clock, ids: IdGenerator
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._ids = ids

    @contextmanager
    def transaction(self, principal: Principal) -> Iterator[Services]:
        """One transaction for the caller's tenant. Commits on success, rolls back on error."""
        tenant = principal.tenant_id
        with unit_of_work(self._session_factory) as session:
            audit = AuditService(
                repository=SqlAuditRepository(session, tenant),
                clock=self._clock,
                ids=self._ids,
                tenant_id=tenant,
            )
            registry = RegistryService(
                models=SqlModelRepository(session, tenant),
                versions=SqlModelVersionRepository(session, tenant),
                live_environments=SqlEnvironmentBindingRepository(
                    session, tenant
                ).live_environments,
                audit=audit,
                clock=self._clock,
                tenant_id=tenant,
            )
            approvals = ApprovalService(
                registry=registry,
                approvals=SqlApprovalRepository(session, tenant),
                audit=audit,
                clock=self._clock,
                ids=self._ids,
            )
            yield Services(registry=registry, approvals=approvals, audit=audit)


def get_uow(request: Request) -> UnitOfWorkFactory:
    uow: UnitOfWorkFactory = request.app.state.uow
    return uow


Uow = Annotated[UnitOfWorkFactory, Depends(get_uow)]
