"""The dev sign-in is dev-only, and the published OpenAPI file never drifts from the code."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mlops.export_openapi import build_spec
from mlops.platform.config import Settings
from mlops.platform.dev_login import dev_login_enabled
from support.api import PUMP, SECRET

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]


def settings_for(app_env: str, **overrides: Any) -> Settings:
    return Settings(app_env=app_env, dev_jwt_secret=SECRET, **overrides)


@pytest.mark.parametrize(
    ("app_env", "auth_backend", "enabled"),
    [
        ("local", "dev_jwt", True),
        ("test", "dev_jwt", True),
        ("staging", "dev_jwt", False),
        ("local", "oidc_jwt", False),
    ],
)
def test_dev_sign_in_is_mounted_only_for_local_dev_tokens(
    app_env: str, auth_backend: str, enabled: bool
) -> None:
    settings = Settings.model_construct(app_env=app_env, auth_backend=auth_backend)

    assert dev_login_enabled(settings) is enabled


def test_a_persona_token_works_against_the_api(client: TestClient) -> None:
    response = client.post("/api/v1/dev/token", json={"subject": "alice", "roles": ["ml-engineer"]})

    body = response.json()
    assert response.status_code == 200
    assert body["token_type"] == "Bearer"
    assert body["roles"] == ["ml-engineer"]
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.post("/api/v1/models", json=PUMP, headers=headers).status_code == 201
    # The token carries exactly the roles asked for: an ml-engineer cannot approve.
    approve = client.post(
        "/api/v1/models/pump-failure-predictor/versions/1.0.0/approve", json={}, headers=headers
    )
    assert approve.status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {"subject": "alice", "roles": ["superuser"]},
        {"subject": "", "roles": []},
        {"subject": "has spaces", "roles": []},
        {"subject": "alice", "roles": [], "extra": 1},
    ],
)
def test_dev_sign_in_validates_its_input(client: TestClient, body: dict[str, Any]) -> None:
    assert client.post("/api/v1/dev/token", json=body).status_code == 422


def test_staging_does_not_mount_dev_sign_in(app_factory: Callable[..., Any]) -> None:
    with TestClient(app_factory(settings=settings_for("staging"))) as staging:
        response = staging.post("/api/v1/dev/token", json={"subject": "alice", "roles": ["admin"]})

    assert response.status_code == 404


def test_the_published_contract_matches_the_code() -> None:
    published = json.loads((REPO_ROOT / "docs" / "openapi.json").read_text())

    assert published == json.loads(json.dumps(build_spec())), (
        "docs/openapi.json is out of date: run `make openapi` and commit the result"
    )


def test_the_published_contract_does_not_expose_the_dev_sign_in() -> None:
    assert "/api/v1/dev/token" not in build_spec()["paths"]  # type: ignore[operator]
