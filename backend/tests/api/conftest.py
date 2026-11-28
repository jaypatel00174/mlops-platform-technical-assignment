"""API test setup: a real app on a real migrated database, a fixed clock and known identities."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mlops.app import DEV_AUDIENCE, DEV_ISSUER, create_app
from mlops.kernel.vocabulary import Role
from mlops.platform.auth import issue_dev_token
from mlops.platform.config import Settings
from support.api import API_NOW, SECRET, FixedClock, SequentialIds


@pytest.fixture
def settings() -> Settings:
    return Settings(dev_jwt_secret=SECRET, default_tenant="default", app_env="test")


@pytest.fixture
def app_factory(settings: Settings, session_factory: sessionmaker[Session]) -> Callable[..., Any]:
    def build(**overrides: Any) -> Any:
        values: dict[str, Any] = {
            "settings": settings,
            "session_factory": session_factory,
            "clock": FixedClock(),
            "ids": SequentialIds(),
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
        )
        return {"Authorization": f"Bearer {token}"}

    return headers


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
