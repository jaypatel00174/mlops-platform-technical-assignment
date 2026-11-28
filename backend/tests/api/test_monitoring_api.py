"""Monitoring over HTTP: ingest, series, health status and version comparison.

Acceptance scenario 5 (monitoring data available) at the API level; the UI half is M4b.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from mlops.deployment.application.worker import DeploymentWorker
from mlops.kernel.vocabulary import Role
from mlops.platform.config import Settings
from support.api import API_NOW, PUMP, SECRET, AdjustableClock
from support.deployments import (
    VERSIONS,
    Headers,
    deploy_and_settle,
    release_version,
)

pytestmark = pytest.mark.integration

GOOD: dict[str, float] = {
    "latency_ms": 90.0,
    "throughput_rpm": 1000.0,
    "error_rate": 0.005,
    "quality_score": 0.88,
    "drift_score": 0.10,
    "availability": 99.8,
}
MODEL = "pump-failure-predictor"


@pytest.fixture
def agent(auth: Callable[..., Headers]) -> Headers:
    return auth("metrics-agent", Role.SERVICE)


def metric(
    version: str = "2.0.0",
    minutes_ago: float = 0,
    environment: str = "production",
    **overrides: float,
) -> dict[str, Any]:
    ts = API_NOW - timedelta(minutes=minutes_ago)
    return {
        "model_id": MODEL,
        "version": version,
        "environment": environment,
        "ts": ts.isoformat().replace("+00:00", "Z"),
        **{**GOOD, **overrides},
    }


def send(client: TestClient, headers: Headers, *samples: dict[str, Any]) -> Any:
    return client.post("/api/v1/metrics", json={"samples": list(samples)}, headers=headers)


def two_versions(client: TestClient, alice: Headers) -> None:
    client.post("/api/v1/models", json=PUMP, headers=alice)
    for version in ("1.0.0", "2.0.0"):
        created = client.post(
            VERSIONS,
            json={
                "version": version,
                "algorithm": "gradient-boosting",
                "artifact_uri": f"s3://models/pump/{version}",
                "artifact_digest": "sha256:" + "a" * 64,
                "training_data_ref": "s3://datasets/pump",
            },
            headers=alice,
        )
        assert created.status_code == 201, created.text


# ------------------------------------------------------------------------------- ingest


def test_a_service_can_ingest_a_batch_and_resend_it_safely(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    batch = [metric(minutes_ago=m) for m in range(3)]

    first = send(client, agent, *batch)
    again = send(client, agent, *batch)

    assert first.status_code == 202
    assert first.json() == {"received": 3, "inserted": 3, "duplicates": 0}
    assert again.json() == {"received": 3, "inserted": 0, "duplicates": 3}


def test_only_the_service_role_can_ingest(
    client: TestClient, alice: Headers, viewer: Headers, auth: Callable[..., Headers]
) -> None:
    two_versions(client, alice)

    assert send(client, alice, metric()).status_code == 403  # ml-engineer
    assert send(client, viewer, metric()).status_code == 403
    assert send(client, auth("root", Role.ADMIN), metric()).status_code == 202


def test_the_service_role_can_write_metrics_and_nothing_else(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)

    assert client.get(f"/api/v1/models/{MODEL}/status", headers=agent).status_code == 403
    assert client.get("/api/v1/models", headers=agent).status_code == 403
    assert client.post("/api/v1/models", json=PUMP, headers=agent).status_code == 403


def test_a_batch_is_all_or_nothing_when_a_version_is_unknown(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)

    response = send(client, agent, metric("2.0.0"), metric("9.9.9", minutes_ago=1))

    assert response.status_code == 404
    series = client.get(
        f"/api/v1/models/{MODEL}/metrics",
        params={"version": "2.0.0", "environment": "production", "resolution": "raw"},
        headers=alice,
    )
    assert series.json()["points"] == []  # the valid sample was not stored either


def test_an_invalid_sample_is_named_by_its_position_and_field(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)

    response = send(client, agent, metric(), metric(minutes_ago=1, error_rate=1.5))

    assert response.status_code == 422
    assert response.json()["details"]["field"] == "samples[1].error_rate"


def test_a_sample_from_the_future_is_refused(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)

    response = send(client, agent, metric(minutes_ago=-30))

    assert response.status_code == 422
    assert response.json()["details"]["field"] == "samples[0].ts"


def test_a_little_clock_skew_is_tolerated(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)

    assert send(client, agent, metric(minutes_ago=-4)).status_code == 202


def test_a_timestamp_without_a_time_zone_is_refused(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    naive = {**metric(), "ts": "2026-01-15T12:00:00"}

    response = send(client, agent, naive)

    assert response.status_code == 422
    assert "time zone" in response.json()["detail"] or "timezone" in str(response.json())


@pytest.mark.parametrize(
    "samples",
    [[], None],
    ids=["empty", "missing"],
)
def test_an_empty_or_missing_batch_is_refused(
    client: TestClient, agent: Headers, samples: list[Any] | None
) -> None:
    body: dict[str, Any] = {} if samples is None else {"samples": samples}

    response = client.post("/api/v1/metrics", json=body, headers=agent)

    assert response.status_code == 422


def test_unknown_fields_are_refused_so_typos_do_not_pass_silently(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)

    response = send(client, agent, {**metric(), "latency": 5})

    assert response.status_code == 422


def test_a_batch_over_the_limit_is_refused(
    app_factory: Callable[..., Any], alice: Headers, agent: Headers
) -> None:
    small = Settings(app_env="test", metrics_max_batch=2, dev_jwt_secret=SECRET)
    with TestClient(app_factory(settings=small), raise_server_exceptions=False) as client:
        two_versions(client, alice)

        response = send(client, agent, *[metric(minutes_ago=m) for m in range(3)])

    assert response.status_code == 422
    assert response.json()["details"]["max_batch"] == 2


# -------------------------------------------------------------------------------- series


def hourly_data(client: TestClient, agent: Headers, version: str = "2.0.0") -> None:
    # Two samples in each of three hours ending now, 100 ms then 200 ms in each hour.
    samples = []
    for hour in range(3):
        samples.append(metric(version, minutes_ago=hour * 60 + 40, latency_ms=100.0))
        samples.append(metric(version, minutes_ago=hour * 60 + 10, latency_ms=200.0))
    assert send(client, agent, *samples).status_code == 202


def read_series(client: TestClient, headers: Headers, **params: str) -> Any:
    query = {"version": "2.0.0", "environment": "production", **params}
    return client.get(f"/api/v1/models/{MODEL}/metrics", params=query, headers=headers)


def test_raw_resolution_returns_every_sample_with_a_summary(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    hourly_data(client, agent)

    body = read_series(client, alice, resolution="raw").json()

    assert len(body["points"]) == 6
    assert body["summary"]["count"] == 6
    assert body["summary"]["metrics"]["latency_ms"]["mean"] == 150.0
    assert body["summary"]["metrics"]["latency_ms"]["maximum"] == 200.0
    assert body["summary"]["latency_p95_ms"] == 200.0
    assert (body["resolution"], body["model_id"], body["version"]) == ("raw", MODEL, "2.0.0")


def test_hourly_resolution_averages_each_hour(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    hourly_data(client, agent)

    body = read_series(client, alice, resolution="hour").json()

    assert [(p["ts"], p["samples"], p["latency_ms"]) for p in body["points"]] == [
        ("2026-01-15T09:00:00Z", 2, 150.0),
        ("2026-01-15T10:00:00Z", 2, 150.0),
        ("2026-01-15T11:00:00Z", 2, 150.0),
    ]


def test_a_range_limits_the_series_and_is_echoed_back(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    hourly_data(client, agent)
    start = (API_NOW - timedelta(minutes=65)).isoformat()  # keeps the samples 10 and 40 min old
    end = API_NOW.isoformat()

    body = read_series(client, alice, resolution="raw", **{"from": start, "to": end}).json()

    assert len(body["points"]) == 2
    assert body["from"] == start.replace("+00:00", "Z")
    assert body["to"] == end.replace("+00:00", "Z")


def test_the_default_resolution_is_hourly_over_the_last_week(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    send(client, agent, metric(minutes_ago=6 * 24 * 60), metric(minutes_ago=8 * 24 * 60))

    body = client.get(
        f"/api/v1/models/{MODEL}/metrics",
        params={"version": "2.0.0", "environment": "production"},
        headers=alice,
    ).json()

    assert body["resolution"] == "hour"
    assert body["summary"]["count"] == 1  # the 8-day-old sample is outside the default week


def test_a_range_with_no_samples_is_an_empty_series_not_an_error(
    client: TestClient, alice: Headers
) -> None:
    two_versions(client, alice)

    response = read_series(client, alice)

    assert response.status_code == 200
    assert response.json()["points"] == []
    assert response.json()["summary"] is None


def test_series_input_is_validated(client: TestClient, alice: Headers) -> None:
    two_versions(client, alice)

    assert read_series(client, alice, resolution="minute").status_code == 422
    assert (
        read_series(
            client, alice, **{"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}
        ).status_code
        == 422
    )
    assert read_series(client, alice, version="9.9.9").status_code == 404
    assert client.get(f"/api/v1/models/{MODEL}/metrics", headers=alice).status_code == 422
    assert (
        client.get(
            "/api/v1/models/nope/metrics",
            params={"version": "1", "environment": "x"},
            headers=alice,
        ).status_code
        == 404
    )


def test_a_range_with_too_many_samples_is_refused_not_truncated(
    app_factory: Callable[..., Any], alice: Headers, agent: Headers
) -> None:
    capped = Settings(
        app_env="test",
        metrics_max_series_rows=1000,
        dev_jwt_secret=SECRET,
        metrics_max_batch=1000,
    )
    with TestClient(app_factory(settings=capped), raise_server_exceptions=False) as client:
        two_versions(client, alice)
        for start in (0, 501):
            send(client, agent, *[metric(minutes_ago=m) for m in range(start, start + 501)])

        response = read_series(client, alice, resolution="raw")

    assert response.status_code == 422
    assert response.json()["details"]["max_rows"] == 1000


def test_reading_metrics_needs_the_view_permission(
    client: TestClient, alice: Headers, auth: Callable[..., Headers]
) -> None:
    two_versions(client, alice)

    assert read_series(client, auth("nobody")).status_code == 403
    assert read_series(client, {}).status_code == 401


# --------------------------------------------------------------------------------- status


def status_of(client: TestClient, headers: Headers) -> dict[str, Any]:
    response = client.get(f"/api/v1/models/{MODEL}/status", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_model_with_no_metrics_and_nothing_live_has_no_data(
    client: TestClient, alice: Headers
) -> None:
    two_versions(client, alice)

    body = status_of(client, alice)

    assert body["status"] == "NO_DATA"
    assert body["items"] == []


def test_healthy_metrics_give_a_healthy_status(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    send(client, agent, metric(minutes_ago=5), metric(minutes_ago=1))

    body = status_of(client, alice)

    assert body["status"] == "HEALTHY"
    (item,) = body["items"]
    assert (item["version"], item["environment"], item["live"]) == ("2.0.0", "production", False)
    assert item["reasons"] == []
    assert item["last_sample_at"] == (API_NOW - timedelta(minutes=1)).isoformat().replace(
        "+00:00", "Z"
    )


def test_a_breach_is_reported_with_the_reason(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    send(client, agent, metric(error_rate=0.08, drift_score=0.35))

    body = status_of(client, alice)

    assert body["status"] == "CRITICAL"
    reasons = {r["metric"]: r for r in body["items"][0]["reasons"]}
    assert reasons["error_rate"]["severity"] == "CRITICAL"
    assert reasons["drift_score"]["severity"] == "DEGRADED"
    assert reasons["error_rate"]["threshold"] == 0.05
    assert "error_rate" in reasons["error_rate"]["message"]


def test_silence_is_stale_not_healthy(client: TestClient, alice: Headers, agent: Headers) -> None:
    two_versions(client, alice)
    send(client, agent, metric(minutes_ago=3 * 24 * 60))

    body = status_of(client, alice)

    assert body["status"] == "STALE"
    assert body["items"][0]["reasons"][0]["metric"] == "data"


def test_staleness_follows_the_clock_not_the_data(
    client: TestClient, clock: AdjustableClock, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    send(client, agent, metric(minutes_ago=23 * 60))  # 23 hours old: still inside the 24 h limit
    assert status_of(client, alice)["status"] == "HEALTHY"

    clock.advance(hours=2)  # now 25 hours old

    assert status_of(client, alice)["status"] == "STALE"


def test_only_live_versions_decide_the_model_status(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
    agent: Headers,
) -> None:
    release_version(client, alice, bob, "1.0.0")
    release_version(client, alice, bob, "2.0.0")
    deploy_and_settle(client, worker, clock, alice, "2.0.0", "staging", key="key-live-v2")
    send(
        client,
        agent,
        metric("2.0.0", environment="staging"),  # live and healthy
        metric("1.0.0", environment="staging", error_rate=0.04),  # not live, degraded
    )

    body = status_of(client, alice)

    assert body["status"] == "HEALTHY"
    by_version = {item["version"]: item for item in body["items"]}
    assert by_version["2.0.0"]["live"] is True
    assert by_version["1.0.0"]["live"] is False
    assert by_version["1.0.0"]["status"] == "DEGRADED"
    assert body["items"][0]["version"] == "2.0.0"  # live versions are listed first


def test_a_live_version_that_never_reported_is_no_data(
    client: TestClient,
    worker: DeploymentWorker,
    clock: AdjustableClock,
    alice: Headers,
    bob: Headers,
) -> None:
    release_version(client, alice, bob, "2.0.0")
    deploy_and_settle(client, worker, clock, alice, "2.0.0", "staging", key="key-quiet-v2")

    body = status_of(client, alice)

    assert body["status"] == "NO_DATA"
    assert body["items"][0]["live"] is True


def test_status_of_an_unknown_model_is_a_404(client: TestClient, alice: Headers) -> None:
    assert client.get("/api/v1/models/nope/status", headers=alice).status_code == 404


# ------------------------------------------------------------------------------- compare


def compare(client: TestClient, headers: Headers, **params: str) -> Any:
    query = {"a": "1.0.0", "b": "2.0.0", **params}
    return client.get(f"/api/v1/models/{MODEL}/versions/compare", params=query, headers=headers)


def test_comparison_shows_metadata_and_the_change_in_each_metric(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    send(
        client,
        agent,
        metric("1.0.0", latency_ms=100.0, error_rate=0.02),
        metric("2.0.0", latency_ms=80.0, error_rate=0.01),
    )

    response = compare(client, alice)

    body = response.json()
    assert response.status_code == 200  # not captured by /versions/{version}
    assert body["environment"] == "production"
    assert (body["a"]["version"], body["b"]["version"]) == ("1.0.0", "2.0.0")
    assert body["a"]["algorithm"] == "gradient-boosting"
    assert body["a"]["metrics"]["count"] == 1
    assert body["deltas"]["latency_ms"]["change"] == pytest.approx(-20.0)
    assert body["deltas"]["latency_ms"]["change_pct"] == pytest.approx(-20.0)
    assert body["deltas"]["error_rate"]["change_pct"] == pytest.approx(-50.0)


def test_comparison_without_data_for_one_side_has_no_deltas(
    client: TestClient, alice: Headers, agent: Headers
) -> None:
    two_versions(client, alice)
    send(client, agent, metric("2.0.0"))

    body = compare(client, alice).json()

    assert body["a"]["metrics"] is None
    assert body["b"]["metrics"]["count"] == 1
    assert body["deltas"] is None


def test_comparison_input_is_validated(client: TestClient, alice: Headers) -> None:
    two_versions(client, alice)

    assert compare(client, alice, b="1.0.0").status_code == 422  # same version twice
    assert compare(client, alice, b="9.9.9").status_code == 404
    assert client.get(f"/api/v1/models/{MODEL}/versions/compare", headers=alice).status_code == 422
    assert compare(client, {}).status_code == 401  # no token


def test_a_version_literally_called_compare_cannot_exist_so_the_route_is_unambiguous(
    client: TestClient, alice: Headers
) -> None:
    two_versions(client, alice)

    response = client.get(f"{VERSIONS}/compare", params={"a": "1.0.0", "b": "2.0.0"}, headers=alice)

    assert response.status_code == 200
    assert "deltas" in response.json()
