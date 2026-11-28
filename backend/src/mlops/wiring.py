"""Composition root for request-scoped and worker-scoped services.

This is the one place that knows which concrete repositories back which services. Each request
runs in a single database transaction: the change, its audit entry, its outbox event and any
approval record are committed together, or none of them are. The worker opens the same kind of
transaction per step through `SqlTransactionRunner`. The API layers depend on this module only to
receive their services; the domain and application layers never see it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.application.ports import DeploymentContext, ModelRuntime
from mlops.deployment.application.services import DeploymentService
from mlops.deployment.domain.outbox import OutboxEvent
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
    SqlIdempotencyRepository,
    SqlOutboxRepository,
)
from mlops.deployment.infrastructure.simulated_runtime import SimulatedRuntime
from mlops.governance.application.approvals import ApprovalService
from mlops.governance.application.audit import AuditService
from mlops.governance.infrastructure.repositories import SqlApprovalRepository, SqlAuditRepository
from mlops.kernel.identity import Principal
from mlops.kernel.ports import Clock, IdGenerator
from mlops.monitoring.application.services import MonitoringService
from mlops.monitoring.domain.health import HealthThresholds
from mlops.monitoring.infrastructure.repositories import SqlMetricSampleRepository
from mlops.platform.config import Settings
from mlops.platform.db import unit_of_work
from mlops.registry.application.services import RegistryService
from mlops.registry.infrastructure.repositories import SqlModelRepository, SqlModelVersionRepository


@dataclass(frozen=True, slots=True)
class Services:
    registry: RegistryService
    approvals: ApprovalService
    audit: AuditService
    deployments: DeploymentService
    monitoring: MonitoringService


def _registry_and_audit(
    session: Session, tenant: str, clock: Clock, ids: IdGenerator
) -> tuple[RegistryService, AuditService]:
    audit = AuditService(
        repository=SqlAuditRepository(session, tenant),
        clock=clock,
        ids=ids,
        tenant_id=tenant,
    )
    registry = RegistryService(
        models=SqlModelRepository(session, tenant),
        versions=SqlModelVersionRepository(session, tenant),
        live_environments=SqlEnvironmentBindingRepository(session, tenant).live_environments,
        audit=audit,
        clock=clock,
        tenant_id=tenant,
    )
    return registry, audit


def _deployment_context(
    session: Session, tenant: str, registry: RegistryService, audit: AuditService
) -> DeploymentContext:
    return DeploymentContext(
        tenant_id=tenant,
        deployments=SqlDeploymentRepository(session, tenant),
        bindings=SqlEnvironmentBindingRepository(session, tenant),
        outbox=SqlOutboxRepository(session, tenant),
        idempotency=SqlIdempotencyRepository(session, tenant),
        registry=registry,
        audit=audit,
    )


class UnitOfWorkFactory:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        clock: Clock,
        ids: IdGenerator,
        runtime: ModelRuntime,
        health_thresholds: HealthThresholds | None = None,
        metrics_max_batch: int = 1000,
        metrics_max_series_rows: int = 100_000,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._ids = ids
        self._runtime = runtime
        self._health_thresholds = health_thresholds
        self._metrics_max_batch = metrics_max_batch
        self._metrics_max_series_rows = metrics_max_series_rows

    @contextmanager
    def transaction(self, principal: Principal) -> Iterator[Services]:
        """One transaction for the caller's tenant. Commits on success, rolls back on error."""
        tenant = principal.tenant_id
        with unit_of_work(self._session_factory) as session:
            registry, audit = _registry_and_audit(session, tenant, self._clock, self._ids)
            approvals = ApprovalService(
                registry=registry,
                approvals=SqlApprovalRepository(session, tenant),
                audit=audit,
                clock=self._clock,
                ids=self._ids,
            )
            deployments = DeploymentService(
                context=_deployment_context(session, tenant, registry, audit),
                runtime=self._runtime,
                clock=self._clock,
                ids=self._ids,
            )
            monitoring = MonitoringService(
                metrics=SqlMetricSampleRepository(session, tenant),
                registry=registry,
                live_versions=SqlEnvironmentBindingRepository(session, tenant).live_for_model,
                clock=self._clock,
                tenant_id=tenant,
                thresholds=self._health_thresholds,
                max_batch=self._metrics_max_batch,
                max_series_rows=self._metrics_max_series_rows,
            )
            yield Services(
                registry=registry,
                approvals=approvals,
                audit=audit,
                deployments=deployments,
                monitoring=monitoring,
            )


class SqlTransactionRunner:
    """Opens one transaction for a tenant and hands the worker everything it may touch."""

    def __init__(
        self, *, session_factory: sessionmaker[Session], clock: Clock, ids: IdGenerator
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._ids = ids

    @contextmanager
    def transaction(self, tenant_id: str) -> Iterator[DeploymentContext]:
        with unit_of_work(self._session_factory) as session:
            registry, audit = _registry_and_audit(session, tenant_id, self._clock, self._ids)
            yield _deployment_context(session, tenant_id, registry, audit)


class SqlOutboxClaimer:
    """Claims due events for every tenant in a short transaction of its own."""

    def __init__(self, *, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def claim(
        self, worker_id: str, now: datetime, lease: timedelta, limit: int
    ) -> list[OutboxEvent]:
        with unit_of_work(self._session_factory) as session:
            return SqlOutboxRepository(session).claim_batch(worker_id, now, lease, limit)


def build_health_thresholds(settings: Settings) -> HealthThresholds:
    return HealthThresholds(
        stale_after=timedelta(seconds=settings.health_stale_after_seconds),
        window=timedelta(seconds=settings.health_window_seconds),
    )


def build_runtime(settings: Settings) -> ModelRuntime:
    """Choose the ModelRuntime adapter from configuration. New adapters are added here."""
    if settings.runtime_backend == "simulated":
        return SimulatedRuntime()
    raise RuntimeError(f"unknown RUNTIME_BACKEND {settings.runtime_backend!r}")


def get_uow(request: Request) -> UnitOfWorkFactory:
    uow: UnitOfWorkFactory = request.app.state.uow
    return uow


Uow = Annotated[UnitOfWorkFactory, Depends(get_uow)]
