"""Correlation IDs, request logging and the error envelope on a tiny app."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from mlops.kernel.errors import Forbidden, NotFound, Unauthenticated
from mlops.platform.logging import configure_logging
from mlops.platform.middleware import RequestContextMiddleware
from mlops.platform.problem import install_error_handlers

PROBLEM = "application/problem+json"


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "fine"}

    @app.get("/missing")
    def missing() -> None:
        raise NotFound("model 'ghost' does not exist", model_id="ghost")

    @app.get("/denied")
    def denied() -> None:
        raise Forbidden("nope", action="approve_version")

    @app.get("/anonymous")
    def anonymous() -> None:
        raise Unauthenticated("missing bearer token")

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail: password=hunter2")

    @app.get("/items/{number}")
    def item(number: int) -> dict[str, int]:
        return {"number": number}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(build_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def test_a_correlation_id_is_generated_and_returned_when_none_is_sent(client: TestClient) -> None:
    response = client.get("/ok")

    assert response.status_code == 200
    assert len(response.headers["X-Correlation-ID"]) >= 8


def test_a_well_formed_correlation_id_from_the_caller_is_kept(client: TestClient) -> None:
    response = client.get("/ok", headers={"X-Correlation-ID": "trace-abc-12345"})

    assert response.headers["X-Correlation-ID"] == "trace-abc-12345"


@pytest.mark.parametrize("bad", ["short", "has space in it!", "x" * 200, "<script>alert(1)"])
def test_a_malformed_correlation_id_is_replaced_not_trusted(client: TestClient, bad: str) -> None:
    response = client.get("/ok", headers={"X-Correlation-ID": bad})

    assert response.headers["X-Correlation-ID"] != bad


def test_two_requests_without_an_id_get_different_ones(client: TestClient) -> None:
    first = client.get("/ok").headers["X-Correlation-ID"]
    second = client.get("/ok").headers["X-Correlation-ID"]

    assert first != second


def test_a_domain_error_becomes_a_problem_document_with_the_correlation_id(
    client: TestClient,
) -> None:
    response = client.get("/missing", headers={"X-Correlation-ID": "trace-abc-12345"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM)
    assert response.json() == {
        "type": "https://errors.mlops.example/not-found",
        "title": "Not found",
        "status": 404,
        "code": "NOT_FOUND",
        "detail": "model 'ghost' does not exist",
        "details": {"model_id": "ghost"},
        "correlation_id": "trace-abc-12345",
    }
    assert response.headers["X-Correlation-ID"] == "trace-abc-12345"


def test_forbidden_is_403(client: TestClient) -> None:
    response = client.get("/denied")

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_unauthenticated_is_401_and_asks_for_a_bearer_token(client: TestClient) -> None:
    response = client.get("/anonymous")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_request_validation_errors_list_each_field(client: TestClient) -> None:
    response = client.get("/items/not-a-number")

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM)
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["field"] == "path.number"
    assert body["correlation_id"] == response.headers["X-Correlation-ID"]


def test_an_unknown_route_uses_the_same_envelope(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert response.headers["content-type"].startswith(PROBLEM)


def test_a_wrong_method_uses_the_same_envelope(client: TestClient) -> None:
    response = client.post("/ok")

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"


def test_an_unexpected_error_is_a_500_that_leaks_nothing(client: TestClient) -> None:
    response = client.get("/boom", headers={"X-Correlation-ID": "trace-abc-12345"})

    body = response.json()
    assert response.status_code == 500
    assert body["code"] == "INTERNAL"
    assert body["correlation_id"] == "trace-abc-12345"
    assert response.headers["X-Correlation-ID"] == "trace-abc-12345"
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text


def test_every_request_is_logged_with_method_path_status_and_duration(client: TestClient) -> None:
    with capture_logs() as logs:
        client.get("/ok", headers={"X-Correlation-ID": "trace-abc-12345"})

    [entry] = [log for log in logs if log["event"] == "http.request"]
    assert entry["method"] == "GET"
    assert entry["path"] == "/ok"
    assert entry["status"] == 200
    assert entry["correlation_id"] == "trace-abc-12345"
    assert entry["duration_ms"] >= 0


def test_an_unexpected_error_is_logged_with_the_exception_for_operators(
    client: TestClient,
) -> None:
    with capture_logs() as logs:
        client.get("/boom", headers={"X-Correlation-ID": "trace-abc-12345"})

    [entry] = [log for log in logs if log["event"] == "http.unhandled_error"]
    assert entry["correlation_id"] == "trace-abc-12345"
    assert entry["log_level"] == "error"
    assert isinstance(entry["exc_info"], RuntimeError | tuple | bool)


def test_logs_are_json_lines_that_carry_the_correlation_id(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO")
    try:
        client.get("/ok", headers={"X-Correlation-ID": "trace-abc-12345"})
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    finally:
        structlog.reset_defaults()

    [entry] = [line for line in lines if line["event"] == "http.request"]
    assert entry["correlation_id"] == "trace-abc-12345"
    assert entry["level"] == "info"
    assert entry["timestamp"].endswith("Z")
