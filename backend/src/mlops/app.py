"""The FastAPI application factory (composition root).

`create_app` takes its collaborators as arguments so tests can inject a fixed clock, a test
database and a known identity provider; `mlops.main` builds the real ones from settings.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from mlops import __version__
from mlops.deployment.api.routes import router as deployment_router
from mlops.deployment.application.ports import ModelRuntime
from mlops.governance.api.routes import router as governance_router
from mlops.kernel.ports import Clock, IdGenerator
from mlops.monitoring.api.routes import router as monitoring_router
from mlops.platform.auth import DevJwtIdentityProvider, IdentityProvider, OidcJwtIdentityProvider
from mlops.platform.config import Settings
from mlops.platform.health import build_health_router
from mlops.platform.middleware import RequestContextMiddleware
from mlops.platform.problem import install_error_handlers
from mlops.platform.runtime import SystemClock, UuidGenerator
from mlops.registry.api.routes import router as registry_router
from mlops.wiring import UnitOfWorkFactory, build_health_thresholds, build_runtime

DEV_ISSUER = "mlops-dev"
DEV_AUDIENCE = "mlops-platform"

_DESCRIPTION = """
Control plane for machine-learning models: register and version models, approve and promote
them, deploy asynchronously, monitor and roll back.

Every error is `application/problem+json` with a stable `code` and a `correlation_id`.
Send `X-Correlation-ID` to choose your own; it is always echoed back.
"""


def build_identity_provider(settings: Settings, clock: Clock) -> IdentityProvider:
    if settings.auth_backend == "oidc_jwt":
        # Settings validation guarantees these are set in production; fail loudly otherwise.
        if not (settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url):
            raise RuntimeError("oidc_jwt needs OIDC_ISSUER, OIDC_AUDIENCE and OIDC_JWKS_URL")
        return OidcJwtIdentityProvider(
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            jwks_url=settings.oidc_jwks_url,
            default_tenant=settings.default_tenant,
            roles_claim=settings.oidc_roles_claim,
            clock=clock.now,
        )
    return DevJwtIdentityProvider(
        secret=settings.dev_jwt_secret,
        issuer=settings.oidc_issuer or DEV_ISSUER,
        audience=settings.oidc_audience or DEV_AUDIENCE,
        default_tenant=settings.default_tenant,
        clock=clock.now,
    )


def create_app(
    *,
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: Clock | None = None,
    ids: IdGenerator | None = None,
    identity_provider: IdentityProvider | None = None,
    runtime: ModelRuntime | None = None,
) -> FastAPI:
    clock = clock or SystemClock()
    app = FastAPI(
        title="MLOps Platform API",
        version=__version__,
        description=_DESCRIPTION,
        openapi_tags=[
            {"name": "registry", "description": "Models, versions and their lifecycle"},
            {"name": "deployments", "description": "Asynchronous deployments, retry, rollback"},
            {"name": "monitoring", "description": "Metric ingest, series, health, comparison"},
            {"name": "governance", "description": "Approvals and the audit trail"},
            {"name": "health", "description": "Liveness and readiness"},
        ],
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    app.state.identity_provider = identity_provider or build_identity_provider(settings, clock)
    app.state.uow = UnitOfWorkFactory(
        session_factory=session_factory,
        clock=clock,
        ids=ids or UuidGenerator(),
        runtime=runtime or build_runtime(settings),
        health_thresholds=build_health_thresholds(settings),
        metrics_max_batch=settings.metrics_max_batch,
        metrics_max_series_rows=settings.metrics_max_series_rows,
    )

    app.include_router(build_health_router(session_factory))
    # Before the registry router: `/versions/compare` must win over `/versions/{version}`.
    app.include_router(monitoring_router)
    app.include_router(registry_router)
    app.include_router(deployment_router)
    app.include_router(governance_router)
    return app
