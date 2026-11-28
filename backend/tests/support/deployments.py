"""Helpers for the deployment API tests: build a releasable version, ask for a deployment, and
run the worker until the deployment settles."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from mlops.deployment.application.worker import DeploymentWorker
from support.api import PUMP, AdjustableClock, version_payload

Headers = dict[str, str]
VERSIONS = "/api/v1/models/pump-failure-predictor/versions"
KEY = "key-0000001"
SETTLED = {"SUCCEEDED", "FAILED", "ROLLED_BACK"}


def release_version(
    client: TestClient, alice: Headers, bob: Headers, version: str, **overrides: Any
) -> None:
    """Register, validate and approve a version of the pump model (creating the model once)."""
    client.post("/api/v1/models", json=PUMP, headers=alice)
    created = client.post(VERSIONS, json=version_payload(version, **overrides), headers=alice)
    assert created.status_code == 201, created.text
    assert client.post(f"{VERSIONS}/{version}/validate", headers=alice).status_code == 200
    approved = client.post(f"{VERSIONS}/{version}/approve", json={}, headers=bob)
    assert approved.status_code == 200, approved.text


def request_deployment(
    client: TestClient,
    headers: Headers,
    version: str,
    environment: str = "staging",
    key: str = KEY,
) -> Any:
    return client.post(
        "/api/v1/deployments",
        json={"model_id": "pump-failure-predictor", "version": version, "environment": environment},
        headers={**headers, "Idempotency-Key": key},
    )


def deployment(client: TestClient, headers: Headers, deployment_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/deployments/{deployment_id}", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def run_until_settled(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    headers: Headers,
    deployment_id: str,
    *,
    rounds: int = 8,
) -> dict[str, Any]:
    """Let the worker work, moving the clock past any retry backoff, until nothing more happens."""
    for _ in range(rounds):
        worker.run_once()
        current = deployment(client, headers, deployment_id)
        if current["status"] in SETTLED:
            return current
        clock.advance(minutes=6)  # longer than the largest backoff
    return deployment(client, headers, deployment_id)


def deploy_and_settle(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    headers: Headers,
    version: str,
    environment: str = "staging",
    key: str = KEY,
) -> dict[str, Any]:
    accepted = request_deployment(client, headers, version, environment, key)
    assert accepted.status_code == 202, accepted.text
    return run_until_settled(client, worker, clock, headers, accepted.json()["deployment_id"])
