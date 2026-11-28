"""Authentication, health, the OpenAPI contract and observability of the running app."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import structlog
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from mlops.app import DEV_AUDIENCE, DEV_ISSUER
from mlops.kernel.vocabulary import Role
from mlops.platform.auth import issue_dev_token
from mlops.platform.config import Settings
from mlops.platform.db import make_engine, make_session_factory
from mlops.platform.health import EXPECTED_SCHEMA_REVISION
from mlops.platform.logging import configure_logging
from support.api import API_NOW, SECRET, version_payload
from support.database import alembic_config

pytestmark = pytest.mark.integration

Headers = dict[str, str]


# ------------------------------------------------------------------------ authentication


def test_a_request_without_a_token_is_a_401_that_asks_for_one(client: TestClient) -> None:
    response = client.get("/api/v1/models")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header", ["Bearer", "Bearer not-a-jwt", "Basic dXNlcjpwYXNz", "Bearer a.b.c"]
)
def test_malformed_credentials_are_a_401(client: TestClient, header: str) -> None:
    response = client.get("/api/v1/models", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_an_expired_token_is_a_401(client: TestClient) -> None:
    expired = issue_dev_token(
        secret=SECRET,
        subject="alice",
        roles=[Role.ADMIN],
        issuer=DEV_ISSUER,
        audience=DEV_AUDIENCE,
        now=API_NOW - timedelta(hours=5),
        ttl=timedelta(hours=1),
    )

    response = client.get("/api/v1/models", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401
    assert "expired" in response.json()["detail"]


def test_a_token_signed_with_the_wrong_secret_is_a_401(client: TestClient) -> None:
    forged = issue_dev_token(
        secret="some-other-secret-that-is-also-long-enough-to-sign",
        subject="mallory",
        roles=[Role.ADMIN],
        issuer=DEV_ISSUER,
        audience=DEV_AUDIENCE,
        now=API_NOW,
    )

    response = client.get("/api/v1/models", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401
    assert "mallory" not in response.text


def test_health_endpoints_need_no_token(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200


# ----------------------------------------------------------------------------------- health


def test_liveness_only_says_the_process_is_up(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_reports_what_it_checked(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.json() == {"status": "ready", "checks": {"database": "ok", "schema": "ok"}}


def test_readiness_fails_when_the_schema_has_not_been_migrated(
    app_factory: Callable[..., Any], tmp_path: Path
) -> None:
    unmigrated = make_session_factory(make_engine(f"sqlite+pysqlite:///{tmp_path / 'empty.db'}"))

    with TestClient(app_factory(session_factory=unmigrated)) as bare:
        response = bare.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "NOT_READY"
    assert response.json()["details"]["checks"] == {"database": "ok", "schema": "missing"}


def test_readiness_fails_when_the_database_is_unreachable(
    app_factory: Callable[..., Any], tmp_path: Path
) -> None:
    unreachable = make_session_factory(
        make_engine(f"sqlite+pysqlite:///{tmp_path / 'no-such-dir' / 'db.sqlite'}")
    )

    with TestClient(app_factory(session_factory=unreachable)) as broken:
        response = broken.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["details"]["checks"]["database"] == "error"


def test_the_revision_readiness_expects_is_the_latest_migration() -> None:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()

    assert head == EXPECTED_SCHEMA_REVISION


# ----------------------------------------------------------------------------- OpenAPI


def test_the_openapi_contract_lists_every_endpoint(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    assert sorted(spec["paths"]) == [
        "/api/v1/audit-events",
        "/api/v1/deployments",
        "/api/v1/deployments/{deployment_id}",
        "/api/v1/deployments/{deployment_id}/events",
        "/api/v1/deployments/{deployment_id}/retry",
        "/api/v1/deployments/{deployment_id}/rollback",
        "/api/v1/environments/{environment}/bindings",
        "/api/v1/metrics",
        "/api/v1/models",
        "/api/v1/models/{model_id}",
        "/api/v1/models/{model_id}/metrics",
        "/api/v1/models/{model_id}/status",
        "/api/v1/models/{model_id}/versions",
        "/api/v1/models/{model_id}/versions/compare",
        "/api/v1/models/{model_id}/versions/{version}",
        "/api/v1/models/{model_id}/versions/{version}/approve",
        "/api/v1/models/{model_id}/versions/{version}/archive",
        "/api/v1/models/{model_id}/versions/{version}/reject",
        "/api/v1/models/{model_id}/versions/{version}/validate",
        "/health/live",
        "/health/ready",
    ]


def test_the_contract_documents_the_error_envelope_and_bearer_auth(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()

    assert "Problem" in spec["components"]["schemas"]
    assert spec["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    create_model = spec["paths"]["/api/v1/models"]["post"]
    assert {"201", "401", "403", "409", "422"} <= set(create_model["responses"])
    assert create_model["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Problem"
    }


def test_the_interactive_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


# -------------------------------------------------------------------------- observability


def test_the_request_log_names_the_actor_and_tenant(
    app_factory: Callable[..., Any], alice: Headers, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    try:
        with TestClient(app_factory()) as client:
            client.get("/api/v1/models", headers={**alice, "X-Correlation-ID": "trace-log-12345"})
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    finally:
        structlog.reset_defaults()

    [entry] = [line for line in lines if line["event"] == "http.request"]
    # alice is an ML engineer, which may not list models, so this is a 200 for VIEW... check below
    assert entry["correlation_id"] == "trace-log-12345"
    assert entry["actor"] == "alice"
    assert entry["tenant"] == "default"
    assert entry["path"] == "/api/v1/models"


def test_every_response_carries_a_correlation_id_including_errors(
    client: TestClient, alice: Headers
) -> None:
    ok = client.post("/api/v1/models", json={"nope": 1}, headers=alice)
    unauthenticated = client.get("/api/v1/models")

    for response in (ok, unauthenticated):
        assert response.json()["correlation_id"] == response.headers["X-Correlation-ID"]


def test_a_complete_flow_never_leaks_internals_in_error_bodies(
    client: TestClient, alice: Headers
) -> None:
    response = client.post("/api/v1/models/ghost/versions", json=version_payload(), headers=alice)

    text = response.text
    assert "Traceback" not in text
    assert "sqlalchemy" not in text.lower()


def test_settings_for_the_api_tests_are_not_production_settings(settings: Settings) -> None:
    assert settings.app_env == "test"
    assert settings.auth_backend == "dev_jwt"
