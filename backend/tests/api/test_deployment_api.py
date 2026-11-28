"""Asynchronous deployments end to end: request over HTTP, work done by the real worker.

Covers acceptance scenarios 3 (unapproved version blocked from production), 4 (idempotent
deployment request), 6 (transient failure retried), 7 (permanent failure not retried) and 8
(rollback), plus the safety rules around them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mlops.deployment.application.worker import DeploymentWorker
from mlops.deployment.infrastructure.simulated_runtime import SimulatedRuntime
from mlops.kernel.vocabulary import Role
from support.api import PUMP, AdjustableClock, version_payload
from support.deployments import (
    VERSIONS,
    Headers,
    deploy_and_settle,
    deployment,
    release_version,
    request_deployment,
    run_until_settled,
)

pytestmark = pytest.mark.integration

# ----------------------------------------------------------------------- accepting a request


def test_a_request_is_accepted_and_recorded_not_executed(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")

    response = request_deployment(client, alice, "2.0.0")

    body = response.json()
    assert response.status_code == 202
    assert response.headers["Location"] == f"/api/v1/deployments/{body['deployment_id']}"
    assert body["status"] == "REQUESTED"
    assert body["attempt"] == 0
    assert body["type"] == "DEPLOY"
    assert "Idempotent-Replayed" not in response.headers


def test_scenario_4_the_same_request_twice_creates_one_deployment(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")

    first = request_deployment(client, alice, "2.0.0")
    second = request_deployment(client, alice, "2.0.0")

    listed = client.get("/api/v1/deployments", headers=alice).json()["items"]
    assert second.status_code == 202
    assert second.json() == first.json()
    assert second.headers["Location"] == first.headers["Location"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert len(listed) == 1


def test_the_same_key_with_a_different_body_is_refused(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "1.0.0")
    release_version(client, alice, bob, "2.0.0")
    request_deployment(client, alice, "2.0.0")

    response = request_deployment(client, alice, "1.0.0")

    assert response.status_code == 422
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_an_idempotency_key_is_scoped_to_the_caller(
    client: TestClient, auth: Callable[..., Headers], alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")
    other_engineer = auth("erin", Role.ML_ENGINEER)
    first = request_deployment(client, alice, "2.0.0")

    second = request_deployment(client, other_engineer, "2.0.0")

    # Not a replay of alice's request: it is judged on its own, and alice's is still in flight.
    assert second.status_code == 409
    assert second.json()["details"]["existing_deployment_id"] == first.json()["deployment_id"]


@pytest.mark.parametrize("key", ["short", "has spaces in it", "x" * 129])
def test_a_malformed_idempotency_key_is_a_422(
    client: TestClient, alice: Headers, bob: Headers, key: str
) -> None:
    release_version(client, alice, bob, "2.0.0")

    response = request_deployment(client, alice, "2.0.0", key=key)

    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["field"] == "Idempotency-Key"


def test_the_idempotency_key_is_required(client: TestClient, alice: Headers, bob: Headers) -> None:
    release_version(client, alice, bob, "2.0.0")

    response = client.post(
        "/api/v1/deployments",
        json={"model_id": "pump-failure-predictor", "version": "2.0.0", "environment": "staging"},
        headers=alice,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_a_second_deployment_while_one_is_in_flight_is_a_conflict(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "1.0.0")
    release_version(client, alice, bob, "2.0.0")
    first = request_deployment(client, alice, "2.0.0", key="key-aaaaaaa")

    second = request_deployment(client, alice, "1.0.0", key="key-bbbbbbb")

    assert second.status_code == 409
    assert second.json()["code"] == "DEPLOYMENT_CONFLICT"
    assert second.json()["details"]["existing_deployment_id"] == first.json()["deployment_id"]


# ------------------------------------------------------------- scenario 3: policy and roles


def test_scenario_3_an_unapproved_version_cannot_be_deployed(
    client: TestClient, alice: Headers
) -> None:
    client.post("/api/v1/models", json=PUMP, headers=alice)
    client.post(VERSIONS, json=version_payload("2.0.0"), headers=alice)

    response = request_deployment(client, alice, "2.0.0", environment="staging")

    assert response.status_code == 409
    assert response.json()["code"] == "POLICY_VIOLATION"
    assert response.json()["details"]["reason"] == "NOT_APPROVED"


def test_scenario_3_production_requires_a_successful_staging_deployment(
    client: TestClient, alice: Headers, bob: Headers, carol: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")

    response = request_deployment(client, carol, "2.0.0", environment="production")

    assert response.status_code == 409
    assert response.json()["details"]["reason"] == "PRIOR_ENVIRONMENT_NOT_SUCCEEDED"


def test_an_ml_engineer_cannot_deploy_to_production(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")

    response = request_deployment(client, alice, "2.0.0", environment="production")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_a_viewer_cannot_deploy_or_change_a_deployment(
    client: TestClient, alice: Headers, bob: Headers, viewer: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")
    accepted = request_deployment(client, alice, "2.0.0").json()["deployment_id"]
    base = f"/api/v1/deployments/{accepted}"

    assert request_deployment(client, viewer, "2.0.0", key="key-viewer1").status_code == 403
    assert client.post(f"{base}/retry", headers=viewer).status_code == 403
    assert client.post(f"{base}/rollback", headers=viewer).status_code == 403
    assert client.get(base, headers=viewer).status_code == 200


def test_unknown_versions_and_environments_are_reported_clearly(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")

    missing_version = request_deployment(client, alice, "9.9.9")
    bad_environment = request_deployment(client, alice, "2.0.0", environment="moon")

    assert missing_version.status_code == 404
    assert bad_environment.status_code == 422
    assert bad_environment.json()["details"]["field"] == "environment"


def test_an_unknown_deployment_is_a_404(client: TestClient, alice: Headers) -> None:
    assert client.get("/api/v1/deployments/dep-nope", headers=alice).status_code == 404
    assert client.get("/api/v1/deployments/dep-nope/events", headers=alice).status_code == 404
    assert client.post("/api/v1/deployments/dep-nope/retry", headers=alice).status_code == 404


# ------------------------------------------------------------------------ the happy path


def test_a_deployment_runs_to_success_and_promotes_the_version(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    carol: Headers,
    runtime: SimulatedRuntime,
) -> None:
    release_version(client, alice, bob, "2.0.0")

    staged = deploy_and_settle(client, worker, clock, alice, "2.0.0", "staging", key="key-stage-1")
    version_after_staging = client.get(f"{VERSIONS}/2.0.0", headers=alice).json()
    produced = deploy_and_settle(
        client, worker, clock, carol, "2.0.0", "production", key="key-prod-01"
    )
    version_after_production = client.get(f"{VERSIONS}/2.0.0", headers=alice).json()

    assert staged["status"] == "SUCCEEDED"
    assert staged["attempt"] == 1
    assert version_after_staging["stage"] == "STAGING"
    assert produced["status"] == "SUCCEEDED"
    assert version_after_production["stage"] == "PRODUCTION"
    assert runtime.deployed[("pump-failure-predictor", "production")] == "2.0.0"

    bindings = client.get("/api/v1/environments/production/bindings", headers=alice).json()
    assert bindings["items"] == [
        {
            "model_id": "pump-failure-predictor",
            "environment": "production",
            "current_deployment_id": produced["deployment_id"],
            "previous_deployment_id": None,
        }
    ]


def test_the_timeline_lists_attempts_and_changes_in_order(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0")
    done = deploy_and_settle(client, worker, clock, alice, "2.0.0")

    timeline = client.get(f"/api/v1/deployments/{done['deployment_id']}/events", headers=alice)

    body = timeline.json()
    assert body["deployment"]["status"] == "SUCCEEDED"
    assert [(a["attempt_no"], a["outcome"]) for a in body["attempts"]] == [(1, "SUCCEEDED")]
    assert body["attempts"][0]["runtime_ref"] == "sim://staging/pump-failure-predictor/2.0.0"
    assert [e["action"] for e in body["events"]] == [
        "deployment.requested",
        "deployment.succeeded",
    ]


def test_deployments_can_be_filtered_by_status_and_model(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0")
    deploy_and_settle(client, worker, clock, alice, "2.0.0")

    succeeded = client.get("/api/v1/deployments", params={"status": "SUCCEEDED"}, headers=alice)
    failed = client.get("/api/v1/deployments", params={"status": "FAILED"}, headers=alice)
    other = client.get("/api/v1/deployments", params={"model_id": "other"}, headers=alice)

    assert len(succeeded.json()["items"]) == 1
    assert failed.json()["items"] == []
    assert other.json()["items"] == []


# --------------------------------------------------------------------- scenarios 6 and 7


def test_scenario_6_a_transient_failure_is_retried_with_backoff_until_it_works(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0", artifact_uri="s3://models/pump/2.0.0?sim=flaky:2")
    accepted = request_deployment(client, alice, "2.0.0").json()["deployment_id"]

    worker.run_once()
    after_first = deployment(client, alice, accepted)
    handled_before_backoff_ends = worker.run_once()
    settled = run_until_settled(client, worker, clock, alice, accepted)

    assert after_first["status"] == "REQUESTED"  # queued again by the platform, not by a person
    assert after_first["attempt"] == 1
    assert handled_before_backoff_ends == 0  # the retry waits for its backoff
    assert settled["status"] == "SUCCEEDED"
    assert settled["attempt"] == 3
    attempts = client.get(f"/api/v1/deployments/{accepted}/events", headers=alice).json()[
        "attempts"
    ]
    assert [a["outcome"] for a in attempts] == ["FAILED", "FAILED", "SUCCEEDED"]
    actions = [
        e["action"]
        for e in client.get(f"/api/v1/deployments/{accepted}/events", headers=alice).json()[
            "events"
        ]
    ]
    assert actions.count("deployment.retry_scheduled") == 2


def test_a_transient_failure_stops_when_the_attempt_budget_is_spent(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0", artifact_uri="s3://models/pump/2.0.0?sim=timeout")

    settled = deploy_and_settle(client, worker, clock, alice, "2.0.0")

    assert settled["status"] == "FAILED"
    assert settled["failure_class"] == "TRANSIENT"
    assert settled["attempt"] == 3  # RETRY_MAX_ATTEMPTS
    assert worker.run_once() == 0


def test_scenario_7_a_permanent_failure_is_not_retried(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0", artifact_uri="s3://models/pump/2.0.0?sim=reject")

    settled = deploy_and_settle(client, worker, clock, alice, "2.0.0")

    assert settled["status"] == "FAILED"
    assert settled["failure_class"] == "PERMANENT_RUNTIME"
    assert settled["attempt"] == 1
    assert "refuses this model" in settled["error"]
    version = client.get(f"{VERSIONS}/2.0.0", headers=alice).json()
    assert version["stage"] == "APPROVED"  # a failed deployment promotes nothing


def test_an_unverifiable_artifact_fails_validation_before_anything_is_deployed(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    runtime: SimulatedRuntime,
) -> None:
    release_version(client, alice, bob, "2.0.0", artifact_uri="s3://models/pump/2.0.0?sim=missing")

    settled = deploy_and_settle(client, worker, clock, alice, "2.0.0")

    assert settled["status"] == "FAILED"
    assert settled["failure_class"] == "VALIDATION"
    assert runtime.deployed == {}


def test_the_worker_repeats_the_policy_check_before_acting(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    carol: Headers,
    runtime: SimulatedRuntime,
) -> None:
    release_version(client, alice, bob, "2.0.0")
    accepted = request_deployment(client, alice, "2.0.0").json()["deployment_id"]
    # Between the request and the worker, someone archives the version.
    assert client.post(f"{VERSIONS}/2.0.0/archive", headers=carol).status_code == 200

    settled = run_until_settled(client, worker, clock, alice, accepted)

    assert settled["status"] == "FAILED"
    assert settled["failure_class"] == "POLICY"
    assert settled["attempt"] == 1
    assert runtime.deployed == {}


def test_a_failed_deployment_can_be_retried_by_a_person(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0", artifact_uri="s3://models/pump/2.0.0?sim=reject")
    failed = deploy_and_settle(client, worker, clock, alice, "2.0.0")

    retried = client.post(f"/api/v1/deployments/{failed['deployment_id']}/retry", headers=alice)

    assert retried.status_code == 202
    assert retried.json()["status"] == "REQUESTED"
    again = run_until_settled(client, worker, clock, alice, failed["deployment_id"])
    assert again["status"] == "FAILED"
    assert again["attempt"] == 2


def test_only_a_failed_deployment_can_be_retried(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")
    accepted = request_deployment(client, alice, "2.0.0").json()["deployment_id"]

    response = client.post(f"/api/v1/deployments/{accepted}/retry", headers=alice)

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


# --------------------------------------------------------------------------- scenario 8


def two_versions_live_in_staging(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    **overrides_for_v1: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    release_version(client, alice, bob, "1.0.0", **overrides_for_v1)
    release_version(client, alice, bob, "2.0.0")
    first = deploy_and_settle(client, worker, clock, alice, "1.0.0", key="key-first-1")
    second = deploy_and_settle(client, worker, clock, alice, "2.0.0", key="key-second")
    assert first["status"] == second["status"] == "SUCCEEDED"
    return first, second


def test_scenario_8_a_rollback_redeploys_the_previous_version(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    runtime: SimulatedRuntime,
) -> None:
    _first, second = two_versions_live_in_staging(client, worker, clock, alice, bob)
    assert runtime.deployed[("pump-failure-predictor", "staging")] == "2.0.0"

    response = client.post(f"/api/v1/deployments/{second['deployment_id']}/rollback", headers=alice)

    rollback = response.json()
    assert response.status_code == 202
    assert rollback["type"] == "ROLLBACK"
    assert rollback["version"] == "1.0.0"
    assert rollback["rollback_of"] == second["deployment_id"]
    settled = run_until_settled(client, worker, clock, alice, rollback["deployment_id"])
    assert settled["status"] == "SUCCEEDED"
    assert deployment(client, alice, second["deployment_id"])["status"] == "ROLLED_BACK"
    assert runtime.deployed[("pump-failure-predictor", "staging")] == "1.0.0"
    binding = client.get("/api/v1/environments/staging/bindings", headers=alice).json()["items"][0]
    assert binding["current_deployment_id"] == rollback["deployment_id"]
    assert binding["previous_deployment_id"] == second["deployment_id"]


def test_a_rollback_with_nothing_to_go_back_to_is_unsafe(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0")
    only = deploy_and_settle(client, worker, clock, alice, "2.0.0")

    response = client.post(f"/api/v1/deployments/{only['deployment_id']}/rollback", headers=alice)

    assert response.status_code == 409
    assert response.json()["code"] == "UNSAFE_ROLLBACK"
    assert response.json()["details"]["failed_checks"] == ["NO_PREVIOUS_DEPLOYMENT"]


def test_a_rollback_to_an_incompatible_runtime_is_unsafe(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    _first, second = two_versions_live_in_staging(
        client, worker, clock, alice, bob, runtime_type="legacy-engine"
    )

    response = client.post(f"/api/v1/deployments/{second['deployment_id']}/rollback", headers=alice)

    assert response.status_code == 409
    assert response.json()["details"]["failed_checks"] == ["RUNTIME_INCOMPATIBLE"]


def test_only_the_live_deployment_can_be_rolled_back(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    first, _second = two_versions_live_in_staging(client, worker, clock, alice, bob)

    response = client.post(f"/api/v1/deployments/{first['deployment_id']}/rollback", headers=alice)

    assert response.status_code == 409
    assert response.json()["details"]["failed_checks"] == ["NOT_CURRENT_DEPLOYMENT"]


def test_a_rollback_is_refused_while_another_deployment_is_in_flight(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    _first, second = two_versions_live_in_staging(client, worker, clock, alice, bob)
    release_version(client, alice, bob, "3.0.0")
    in_flight = request_deployment(client, alice, "3.0.0", key="key-third-1").json()

    response = client.post(f"/api/v1/deployments/{second['deployment_id']}/rollback", headers=alice)

    assert response.status_code == 409
    assert response.json()["code"] == "DEPLOYMENT_CONFLICT"
    assert response.json()["details"]["existing_deployment_id"] == in_flight["deployment_id"]


def test_a_deployment_that_never_succeeded_cannot_be_rolled_back(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    release_version(client, alice, bob, "2.0.0")
    accepted = request_deployment(client, alice, "2.0.0").json()["deployment_id"]

    response = client.post(f"/api/v1/deployments/{accepted}/rollback", headers=alice)

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"


def test_production_rollback_needs_the_release_manager_role(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    carol: Headers,
) -> None:
    release_version(client, alice, bob, "1.0.0")
    release_version(client, alice, bob, "2.0.0")
    for number, version in enumerate(("1.0.0", "2.0.0")):
        deploy_and_settle(
            client, worker, clock, alice, version, "staging", key=f"key-stg-{number}x"
        )
    deploy_and_settle(client, worker, clock, carol, "1.0.0", "production", key="key-prod-v1")
    live = deploy_and_settle(client, worker, clock, carol, "2.0.0", "production", key="key-prod-v2")
    url = f"/api/v1/deployments/{live['deployment_id']}/rollback"

    refused = client.post(url, headers=alice)
    allowed = client.post(url, headers=carol)

    assert refused.status_code == 403
    assert allowed.status_code == 202
