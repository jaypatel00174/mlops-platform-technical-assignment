"""Twelve-factor configuration: every setting is an environment variable, validated at startup.

The process fails fast on an invalid or unsafe configuration instead of starting half-configured.
`.env.example` documents every variable and a test keeps it in sync with this class.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Credentials that ship in `.env.example`. They are fine on a laptop and never in production.
_DEVELOPMENT_CREDENTIALS = "mlops:mlops@"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # `make bootstrap` writes .env at the repo root and the app runs from backend/. Later
        # files win: backend/.env overrides the root one, real environment variables beat both.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # application
    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    default_tenant: str = "default"

    # database
    database_url: str = "postgresql+psycopg://mlops:mlops@localhost:5432/mlops"

    # adapters, selected by configuration (ports and adapters)
    runtime_backend: Literal["simulated"] = "simulated"
    queue_backend: Literal["db_outbox"] = "db_outbox"
    artifact_backend: Literal["filesystem", "s3_api"] = "filesystem"
    metrics_backend: Literal["sql"] = "sql"
    artifact_root: Path = Path("var/artifacts")

    # worker: how often it polls, how long it holds an event, and how it backs off
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    worker_batch_size: int = Field(default=10, ge=1, le=100)
    # A lease must outlast one deployment; reconcile_stale_seconds must outlast the lease.
    worker_lease_seconds: float = Field(default=120.0, gt=0)
    reconcile_interval_seconds: float = Field(default=30.0, gt=0)
    reconcile_stale_seconds: float = Field(default=300.0, gt=0)
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=2.0, gt=0)
    retry_max_delay_seconds: float = Field(default=300.0, gt=0)

    # identity: dev_jwt is for local use and tests only; production must use oidc_jwt
    auth_backend: Literal["dev_jwt", "oidc_jwt"] = "dev_jwt"
    dev_jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"  # noqa: S105
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_roles_claim: str = "roles"

    @model_validator(mode="after")
    def _refuse_unsafe_production_configuration(self) -> Settings:
        # A healthy but slow worker must not be mistaken for a dead one, in any environment.
        if self.reconcile_stale_seconds <= self.worker_lease_seconds:
            raise ValueError("RECONCILE_STALE_SECONDS must be longer than WORKER_LEASE_SECONDS")
        if self.app_env != "production":
            return self

        problems: list[str] = []
        if _DEVELOPMENT_CREDENTIALS in self.database_url:
            problems.append("DATABASE_URL uses the development credentials")
        if self.auth_backend != "oidc_jwt":
            problems.append("AUTH_BACKEND must be oidc_jwt in production")
        problems.extend(
            f"{name.upper()} is required in production"
            for name in ("oidc_issuer", "oidc_audience", "oidc_jwks_url")
            if not getattr(self, name)
        )
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, read once."""
    return Settings()
