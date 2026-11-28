"""Every domain error reaches the client in the one documented envelope (docs/api-design.md)."""

from __future__ import annotations

import pytest

from mlops.kernel import errors
from mlops.kernel.errors import DomainError
from mlops.platform.problem import STATUS_BY_CODE, problem_body, status_for


def _all_subclasses(cls: type[DomainError]) -> set[type[DomainError]]:
    found: set[type[DomainError]] = set()
    for sub in cls.__subclasses__():
        found.add(sub)
        found |= _all_subclasses(sub)
    return found


def test_every_domain_error_has_an_explicit_http_status() -> None:
    """Adding an error class without deciding its status fails this test."""
    codes = {error.code for error in _all_subclasses(DomainError)}

    assert codes <= set(STATUS_BY_CODE), f"unmapped: {sorted(codes - set(STATUS_BY_CODE))}"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (errors.ValidationFailure("x"), 422),
        (errors.NotFound("x"), 404),
        (errors.AlreadyExists("x"), 409),
        (errors.InvalidTransition("x"), 409),
        (errors.Forbidden("x"), 403),
        (errors.SeparationOfDuties("x"), 403),
        (errors.PolicyViolation("x"), 409),
        (errors.DeploymentConflict("x"), 409),
        (errors.StaleVersion("x"), 409),
        (errors.UnsafeRollback("x"), 409),
        (errors.IdempotencyKeyReused("x"), 422),
        (errors.Unauthenticated("x"), 401),
        (errors.RuntimeTimeout("x"), 504),
        (errors.RuntimeUnavailable("x"), 503),
        (errors.RuntimeRejected("x"), 422),
    ],
)
def test_domain_errors_map_to_the_documented_status(error: DomainError, status: int) -> None:
    assert status_for(error) == status


def test_an_unknown_error_code_is_a_server_error_not_a_crash() -> None:
    class OddError(DomainError):
        code = "SOMETHING_NEW"

    assert status_for(OddError("x")) == 500


def test_the_problem_body_has_every_documented_field() -> None:
    body = problem_body(
        status=409,
        code="DEPLOYMENT_CONFLICT",
        detail="a deployment is already in progress",
        details={"deployment_id": "dep-1004"},
        correlation_id="c-12345678",
    )

    assert body == {
        "type": "https://errors.mlops.example/deployment-conflict",
        "title": "Deployment conflict",
        "status": 409,
        "code": "DEPLOYMENT_CONFLICT",
        "detail": "a deployment is already in progress",
        "details": {"deployment_id": "dep-1004"},
        "correlation_id": "c-12345678",
    }


def test_details_default_to_an_empty_object() -> None:
    body = problem_body(status=500, code="INTERNAL", detail="x", correlation_id="c-12345678")

    assert body["details"] == {}
