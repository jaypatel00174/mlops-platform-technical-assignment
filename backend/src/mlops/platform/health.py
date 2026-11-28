"""Liveness and readiness.

`/health/live` says the process is up. `/health/ready` says it can do useful work: the database
answers and the schema is at the revision this build expects. Orchestrators use the first to
restart a process and the second to decide whether to send it traffic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from mlops.platform.problem import problem_response

# Must equal the head revision in backend/migrations/versions (a test enforces it).
EXPECTED_SCHEMA_REVISION = "0001"


def _check_database(session_factory: sessionmaker[Session], expected: str) -> dict[str, str]:
    checks = {"database": "error", "schema": "unknown"}
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = "ok"
            try:
                revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            except SQLAlchemyError:
                checks["schema"] = "missing"
                return checks
            checks["schema"] = "ok" if revision == expected else f"stale ({revision})"
    except SQLAlchemyError:
        pass
    return checks


def build_health_router(
    session_factory: sessionmaker[Session], expected_revision: str = EXPECTED_SCHEMA_REVISION
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live", summary="The process is up")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", summary="The process can serve traffic", response_model=None)
    def ready() -> Any:
        checks = _check_database(session_factory, expected_revision)
        if checks["database"] == "ok" and checks["schema"] == "ok":
            return JSONResponse({"status": "ready", "checks": checks})
        return problem_response(
            status=503,
            code="NOT_READY",
            detail="the service cannot serve traffic yet",
            details={"checks": checks},
        )

    return router
