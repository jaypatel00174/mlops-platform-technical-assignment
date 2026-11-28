"""API test setup: a real app on a real migrated database, a fixed clock and known identities."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mlops.app import DEV_AUDIENCE, DEV_ISSUER, create_app
from mlops.deployment.application.reconciler import Reconciler
from mlops.deployment.application.worker import DeploymentWorker
from mlops.deployment.infrastructure.simulated_runtime import SimulatedRuntime
from mlops.kernel.vocabulary import Role
from mlops.platform.auth import issue_dev_token
from mlops.platform.config import Settings
from mlops.worker import build_worker
from support.api import API_NOW, SECRET, AdjustableClock, SequentialIds


@pytest.fixture
def settings() -> Settings:
    return Settings(dev_jwt_secret=SECRET, default_tenant="default", app_env="test")


@pytest.fixture
def clock() -> AdjustableClock:
    return AdjustableClock()


@pytest.fixture
def ids() -> SequentialIds:
    return SequentialIds()


@pytest.fixture
def runtime() -> SimulatedRuntime:
    return SimulatedRuntime()


@pytest.fixture
def app_factory(
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
    ids: SequentialIds,
    runtime: SimulatedRuntime,
) -> Callable[..., Any]:
    def build(**overrides: Any) -> Any:
        values: dict[str, Any] = {
            "settings": settings,
            "session_factory": session_factory,
            "clock": clock,
            "ids": ids,
            "runtime": runtime,
        }
        values.update(overrides)
        return create_app(**values)

    return build


@pytest.fixture
def client(app_factory: Callable[..., Any]) -> Iterator[TestClient]:
    # Server errors become 500 responses (as in production) instead of raising into the test.
    with TestClient(app_factory(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def auth() -> Callable[..., dict[str, str]]:
    def headers(subject: str, *roles: Role, tenant: str | None = None) -> dict[str, str]:
        token = issue_dev_token(
            secret=SECRET,
            subject=subject,
            roles=roles,
            issuer=DEV_ISSUER,
            audience=DEV_AUDIENCE,
            now=API_NOW,
            tenant=tenant,
            ttl=timedelta(hours=12),  # long enough to move the clock for backoff and leases
        )
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def worker_pair(
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: AdjustableClock,
    ids: SequentialIds,
    runtime: SimulatedRuntime,
) -> tuple[DeploymentWorker, Reconciler]:
    """The real worker and reconciler on the test database, sharing the test clock and ids."""
    return build_worker(
        settings, session_factory, runtime=runtime, clock=clock, ids=ids, worker_id="worker-test"
    )


@pytest.fixture
def worker(worker_pair: tuple[DeploymentWorker, Reconciler]) -> DeploymentWorker:
    return worker_pair[0]


# Identities used across the API tests.
@pytest.fixture
def alice(auth: Callable[..., dict[str, str]]) -> dict[str, str]:
    return auth("alice", Role.ML_ENGINEER)


@pytest.fixture
def bob(auth: Callable[..., dict[str, str]]) -> dict[str, str]:
    return auth("bob", Role.APPROVER)


@pytest.fixture
def carol(auth: Callable[..., dict[str, str]]) -> dict[str, str]:
    return auth("carol", Role.RELEASE_MANAGER)


@pytest.fixture
def viewer(auth: Callable[..., dict[str, str]]) -> dict[str, str]:
    return auth("vera", Role.VIEWER)
