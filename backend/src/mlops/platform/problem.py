"""The one error envelope every client sees: RFC 7807 style `application/problem+json`.

Domain errors carry a stable `code`. This module owns the only table that turns codes into HTTP
statuses, and a test fails if a new error class is added without a deliberate status.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from mlops.kernel.errors import DomainError
from mlops.platform.context import NO_REQUEST, get_correlation_id

PROBLEM_MEDIA_TYPE = "application/problem+json"
ERROR_TYPE_BASE = "https://errors.mlops.example/"

STATUS_BY_CODE: Mapping[str, int] = {
    "VALIDATION_ERROR": 422,
    "NOT_FOUND": 404,
    "ALREADY_EXISTS": 409,
    "INVALID_TRANSITION": 409,
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "SEPARATION_OF_DUTIES": 403,
    "POLICY_VIOLATION": 409,
    "DEPLOYMENT_CONFLICT": 409,
    "STALE_VERSION": 409,
    "UNSAFE_ROLLBACK": 409,
    "IDEMPOTENCY_KEY_REUSED": 422,
    "RUNTIME_FAILURE": 502,
    "RUNTIME_TIMEOUT": 504,
    "RUNTIME_UNAVAILABLE": 503,
    "RUNTIME_REJECTED": 422,
    "DOMAIN_ERROR": 500,
}

_CODE_BY_HTTP_STATUS = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
_GENERIC_500 = "an unexpected error occurred; quote the correlation ID when reporting it"


class Problem(BaseModel):
    """The error body, described for OpenAPI so generated clients can type it."""

    type: str = Field(examples=["https://errors.mlops.example/not-found"])
    title: str = Field(examples=["Not found"])
    status: int = Field(examples=[404])
    code: str = Field(description="Stable machine-readable code", examples=["NOT_FOUND"])
    detail: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = Field(description="Quote this when reporting a problem")


# Declared once and reused by every router so the OpenAPI document lists the error responses.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": Problem, "description": "Missing or invalid bearer token"},
    403: {"model": Problem, "description": "The caller's roles do not allow this"},
    404: {"model": Problem, "description": "The resource does not exist"},
    409: {"model": Problem, "description": "The current state does not allow this"},
    422: {"model": Problem, "description": "The request is invalid"},
}


def status_for(error: DomainError) -> int:
    return STATUS_BY_CODE.get(error.code, 500)


def _title(code: str) -> str:
    return code.replace("_", " ").capitalize()


def problem_body(
    *,
    status: int,
    code: str,
    detail: str,
    correlation_id: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": ERROR_TYPE_BASE + code.lower().replace("_", "-"),
        "title": _title(code),
        "status": status,
        "code": code,
        "detail": detail,
        "details": dict(details or {}),
        "correlation_id": correlation_id,
    }


def problem_response(
    *,
    status: int,
    code: str,
    detail: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    correlation_id = get_correlation_id()
    if correlation_id == NO_REQUEST:  # raised outside the middleware, still give it a reference
        correlation_id = uuid.uuid4().hex
    return JSONResponse(
        problem_body(
            status=status,
            code=code,
            detail=detail,
            details=details,
            correlation_id=correlation_id,
        ),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=dict(headers or {}),
    )


def _field(location: Sequence[Any]) -> str:
    return ".".join(str(part) for part in location)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, DomainError)  # noqa: S101  (narrowing for mypy)
        status = status_for(error)
        details = dict(error.details)
        if error.code == "VALIDATION_ERROR" and "field" in details and "errors" not in details:
            # Same shape as request-validation errors, so clients handle one format.
            details["errors"] = [{"field": details["field"], "message": error.message}]
        return problem_response(
            status=status,
            code=error.code,
            detail=error.message if status != 500 else _GENERIC_500,
            details=details,
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)  # noqa: S101  (narrowing for mypy)
        problems = [
            {"field": _field(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return problem_response(
            status=422,
            code="VALIDATION_ERROR",
            detail="the request is invalid; see details.errors",
            details={"errors": problems},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, StarletteHTTPException)  # noqa: S101  (narrowing for mypy)
        code = _CODE_BY_HTTP_STATUS.get(error.status_code, f"HTTP_{error.status_code}")
        detail = error.detail if isinstance(error.detail, str) else code.lower()
        return problem_response(status=error.status_code, code=code, detail=detail)
