from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mlops.deployment.domain.entities import EnvironmentBinding
from mlops.deployment.infrastructure.repositories import (
    SqlDeploymentRepository,
    SqlEnvironmentBindingRepository,
)
from mlops.kernel.vocabulary import Role
from support.api import API_NOW, PUMP, version_payload
from support.builders import make_deployment

pytestmark = pytest.mark.integration

Headers = dict[str, str]


def create_model(client: TestClient, headers: Headers, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/v1/models", json={**PUMP, **overrides}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def create_version(
    client: TestClient, headers: Headers, version: str = "1.0.0", model_id: str = PUMP["id"]
) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/models/{model_id}/versions", json=version_payload(version), headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


# ----------------------------------------------------------------------------------- models


def test_scenario_1_register_a_model_and_two_versions(client: TestClient, alice: Headers) -> None:
    created = client.post("/api/v1/models", json=PUMP, headers=alice)

    assert created.status_code == 201
    assert created.headers["Location"] == "/api/v1/models/pump-failure-predictor"
    assert created.json() == {
        "id": "pump-failure-predictor",
        "name": "Pump Failure Predictor",
        "owner": "Reliability AI Team",
        "framework": "scikit-learn",
        "tags": ["plant-a"],
        "version_count": 0,
        "latest_version": None,
        "created_at": "2026-01-15T12:00:00Z",
        "updated_at": "2026-01-15T12:00:00Z",
    }

    create_version(client, alice, "1.0.0")
    create_version(client, alice, "2.0.0")
    detail = client.get("/api/v1/models/pump-failure-predictor", headers=alice).json()
    versions = client.get("/api/v1/models/pump-failure-predictor/versions", headers=alice).json()

    assert detail["version_count"] == 2
    assert detail["latest_version"] == "2.0.0"
    assert [v["version"] for v in versions["items"]] == ["1.0.0", "2.0.0"]


def test_registering_the_same_model_twice_is_a_409(client: TestClient, alice: Headers) -> None:
    create_model(client, alice)

    again = client.post("/api/v1/models", json=PUMP, headers=alice)

    assert again.status_code == 409
    assert again.json()["code"] == "ALREADY_EXISTS"


@pytest.mark.parametrize("bad_id", ["Pump", "-pump", "pump_failure", "p", "pump failure"])
def test_an_invalid_model_id_is_a_422_that_names_the_field(
    client: TestClient, alice: Headers, bad_id: str
) -> None:
    response = client.post("/api/v1/models", json={**PUMP, "id": bad_id}, headers=alice)

    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["field"] == "id"


def test_a_missing_required_field_is_a_422_listing_the_field(
    client: TestClient, alice: Headers
) -> None:
    incomplete = {key: value for key, value in PUMP.items() if key != "owner"}

    response = client.post("/api/v1/models", json=incomplete, headers=alice)

    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["field"] == "body.owner"


def test_unknown_fields_are_rejected_instead_of_silently_ignored(
    client: TestClient, alice: Headers
) -> None:
    response = client.post("/api/v1/models", json={**PUMP, "stage": "PRODUCTION"}, headers=alice)

    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["field"] == "body.stage"


def test_a_blank_name_is_refused_by_the_domain_in_the_same_error_shape(
    client: TestClient, alice: Headers
) -> None:
    response = client.post("/api/v1/models", json={**PUMP, "name": "   "}, headers=alice)

    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["field"] == "name"


def test_models_can_be_listed_searched_and_filtered(client: TestClient, alice: Headers) -> None:
    create_model(client, alice)
    create_model(client, alice, id="valve-health-model", name="Valve Health", framework="pytorch")
    create_model(client, alice, id="compressor-anomaly", name="Compressor Anomaly")

    everything = client.get("/api/v1/models", headers=alice).json()
    by_text = client.get("/api/v1/models?q=VALVE", headers=alice).json()
    by_name = client.get("/api/v1/models?q=anomaly", headers=alice).json()
    by_framework = client.get("/api/v1/models?framework=PyTorch", headers=alice).json()

    assert [m["id"] for m in everything["items"]] == [
        "compressor-anomaly",
        "pump-failure-predictor",
        "valve-health-model",
    ]
    assert [m["id"] for m in by_text["items"]] == ["valve-health-model"]
    assert [m["id"] for m in by_name["items"]] == ["compressor-anomaly"]
    assert [m["id"] for m in by_framework["items"]] == ["valve-health-model"]


def test_a_search_for_a_percent_sign_is_literal_not_a_wildcard(
    client: TestClient, alice: Headers
) -> None:
    create_model(client, alice)

    assert client.get("/api/v1/models?q=%25", headers=alice).json()["items"] == []


def test_keyset_pagination_visits_every_model_exactly_once(
    client: TestClient, alice: Headers
) -> None:
    for number in range(1, 6):
        create_model(client, alice, id=f"model-{number:03d}", name=f"Model {number}")

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = "/api/v1/models?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=alice).json()
        seen += [m["id"] for m in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [f"model-{n:03d}" for n in range(1, 6)]
    assert pages == 3


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "limit=abc", "cursor=!!!not-base64!!!"])
def test_bad_paging_parameters_are_a_422(client: TestClient, alice: Headers, query: str) -> None:
    response = client.get(f"/api/v1/models?{query}", headers=alice)

    assert response.status_code == 422


def test_an_unknown_model_is_a_404(client: TestClient, alice: Headers) -> None:
    response = client.get("/api/v1/models/ghost-model", headers=alice)

    assert response.status_code == 404
    assert response.json()["details"] == {"model_id": "ghost-model"}


def test_one_tenant_cannot_see_anothers_models(
    client: TestClient, alice: Headers, auth: Callable[..., Headers]
) -> None:
    create_model(client, alice)
    outsider = auth("olga", Role.ADMIN, tenant="other-plant")

    assert client.get("/api/v1/models/pump-failure-predictor", headers=outsider).status_code == 404
    assert client.get("/api/v1/models", headers=outsider).json()["items"] == []


# --------------------------------------------------------------------------------- versions


def test_a_registered_version_starts_as_an_unapproved_draft_and_returns_an_etag(
    client: TestClient, alice: Headers
) -> None:
    create_model(client, alice)

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions",
        json=version_payload("2.0.0"),
        headers=alice,
    )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["Location"] == "/api/v1/models/pump-failure-predictor/versions/2.0.0"
    assert response.headers["ETag"] == 'W/"1"'
    assert body["stage"] == "DRAFT"
    assert body["approval_status"] == "PENDING"
    assert body["registered_by"] == "alice"
    assert body["metadata"] == {"accuracy": 0.93}
    assert body["id"] == "pump-failure-predictor@2.0.0"


def test_a_version_of_an_unknown_model_is_a_404(client: TestClient, alice: Headers) -> None:
    response = client.post(
        "/api/v1/models/ghost-model/versions", json=version_payload(), headers=alice
    )

    assert response.status_code == 404


def test_the_same_version_twice_is_a_409(client: TestClient, alice: Headers) -> None:
    create_model(client, alice)
    create_version(client, alice, "1.0.0")

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions",
        json=version_payload("1.0.0"),
        headers=alice,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "ALREADY_EXISTS"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "v1"),
        ("version", "1.0"),
        ("artifact_uri", "ftp://host/model"),
        ("artifact_uri", "no-scheme"),
        ("artifact_digest", "sha256:short"),
        ("artifact_digest", "md5:" + "a" * 32),
        ("algorithm", "  "),
    ],
)
def test_invalid_version_fields_are_a_422_naming_the_field(
    client: TestClient, alice: Headers, field: str, value: str
) -> None:
    create_model(client, alice)

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions",
        json={**version_payload("1.0.0"), field: value},
        headers=alice,
    )

    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["field"] == field


def test_versions_are_listed_in_semantic_order(client: TestClient, alice: Headers) -> None:
    create_model(client, alice)
    for version in ("1.10.0", "2.0.0", "1.2.0"):
        create_version(client, alice, version)

    listed = client.get("/api/v1/models/pump-failure-predictor/versions", headers=alice).json()

    assert [v["version"] for v in listed["items"]] == ["1.2.0", "1.10.0", "2.0.0"]


def test_a_single_version_can_be_read_and_an_unknown_one_is_a_404(
    client: TestClient, alice: Headers
) -> None:
    create_model(client, alice)
    create_version(client, alice, "1.0.0")

    found = client.get("/api/v1/models/pump-failure-predictor/versions/1.0.0", headers=alice)
    missing = client.get("/api/v1/models/pump-failure-predictor/versions/9.9.9", headers=alice)

    assert found.status_code == 200
    assert found.headers["ETag"] == 'W/"1"'
    assert missing.status_code == 404


def test_validation_moves_a_draft_to_validated_and_bumps_the_etag(
    client: TestClient, alice: Headers
) -> None:
    create_model(client, alice)
    create_version(client, alice)

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions/1.0.0/validate", headers=alice
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "VALIDATED"
    assert response.headers["ETag"] == 'W/"2"'


def test_validating_twice_is_an_invalid_transition(client: TestClient, alice: Headers) -> None:
    create_model(client, alice)
    create_version(client, alice)
    url = "/api/v1/models/pump-failure-predictor/versions/1.0.0/validate"
    client.post(url, headers=alice)

    again = client.post(url, headers=alice)

    assert again.status_code == 409
    assert again.json()["code"] == "INVALID_TRANSITION"
    assert again.json()["details"]["current"] == "VALIDATED"


def test_a_stale_if_match_is_refused_and_a_current_one_is_accepted(
    client: TestClient, alice: Headers
) -> None:
    create_model(client, alice)
    create_version(client, alice)
    url = "/api/v1/models/pump-failure-predictor/versions/1.0.0/validate"

    stale = client.post(url, headers={**alice, "If-Match": 'W/"99"'})
    current = client.post(url, headers={**alice, "If-Match": 'W/"1"'})

    assert stale.status_code == 409
    assert stale.json()["code"] == "STALE_VERSION"
    assert stale.json()["details"]["current_etag"] == 'W/"1"'
    assert current.status_code == 200


def test_if_match_star_means_any_existing_version(client: TestClient, alice: Headers) -> None:
    create_model(client, alice)
    create_version(client, alice)

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions/1.0.0/validate",
        headers={**alice, "If-Match": "*"},
    )

    assert response.status_code == 200


# ----------------------------------------------------------------------------- permissions


@pytest.mark.parametrize(
    ("method", "path", "roles"),
    [
        ("post", "/api/v1/models", [Role.VIEWER, Role.APPROVER, Role.RELEASE_MANAGER]),
        ("post", "/api/v1/models/m/versions", [Role.VIEWER, Role.APPROVER, Role.RELEASE_MANAGER]),
        ("post", "/api/v1/models/m/versions/1.0.0/validate", [Role.VIEWER, Role.APPROVER]),
        ("post", "/api/v1/models/m/versions/1.0.0/approve", [Role.VIEWER, Role.ML_ENGINEER]),
        ("post", "/api/v1/models/m/versions/1.0.0/reject", [Role.VIEWER, Role.RELEASE_MANAGER]),
        ("post", "/api/v1/models/m/versions/1.0.0/archive", [Role.VIEWER, Role.ML_ENGINEER]),
        (
            "get",
            "/api/v1/audit-events?entity_type=model&entity_id=m",
            [Role.VIEWER, Role.ML_ENGINEER],
        ),
        ("get", "/api/v1/models", [Role.SERVICE]),
    ],
)
def test_roles_without_permission_get_403_before_any_state_is_revealed(
    client: TestClient,
    auth: Callable[..., Headers],
    method: str,
    path: str,
    roles: list[Role],
) -> None:
    body: dict[str, object] = {}
    if method == "post" and path == "/api/v1/models":
        body = {"json": PUMP}
    elif method == "post" and path.endswith("/versions"):
        body = {"json": version_payload("1.0.0")}
    for role in roles:
        response = getattr(client, method)(path, headers=auth("someone", role), **body)

        assert response.status_code == 403, f"{role} on {method} {path}"
        assert response.json()["code"] == "FORBIDDEN"


def test_a_caller_with_no_roles_can_do_nothing(
    client: TestClient, auth: Callable[..., Headers]
) -> None:
    assert client.get("/api/v1/models", headers=auth("nobody")).status_code == 403


# --------------------------------------------------------------------------------- archive


def test_a_version_that_is_not_live_can_be_archived_by_a_release_manager(
    client: TestClient, alice: Headers, carol: Headers
) -> None:
    create_model(client, alice)
    create_version(client, alice)

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions/1.0.0/archive", headers=carol
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "ARCHIVED"


def test_a_version_that_is_live_cannot_be_archived(
    client: TestClient,
    alice: Headers,
    carol: Headers,
    session_factory: sessionmaker[Session],
) -> None:
    create_model(client, alice)
    create_version(client, alice, "1.0.0")
    with session_factory() as session:
        # A deployment that went live, written directly because the deployment API is M3.
        deployments = SqlDeploymentRepository(session)
        deployments.add(make_deployment("dep-1", version="1.0.0"))
        session.commit()
        deployment = deployments.get("dep-1")
        assert deployment is not None
        deployment = deployments.save(deployment.begin_validation(API_NOW))
        deployment = deployments.save(deployment.begin_deploying(API_NOW))
        deployment = deployments.save(deployment.succeed(API_NOW))
        binding = EnvironmentBinding(model_id="pump-failure-predictor", environment="production")
        SqlEnvironmentBindingRepository(session).save(binding.after_success(deployment.id), API_NOW)
        session.commit()

    response = client.post(
        "/api/v1/models/pump-failure-predictor/versions/1.0.0/archive", headers=carol
    )

    assert response.status_code == 409
    assert response.json()["code"] == "POLICY_VIOLATION"
    assert response.json()["details"]["live_in"] == ["production"]
