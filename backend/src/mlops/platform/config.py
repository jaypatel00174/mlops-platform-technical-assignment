"""Twelve-factor configuration: every setting is an environment variable, validated at startup.

The process fails fast on an invalid or unsafe configuration instead of starting half-configured.
`.env.example` documents every variable and a test keeps it in sync with this class.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Credentials that ship in `.env.example`. They are fine on a laptop and never in production.
_DEVELOPMENT_CREDENTIALS = "mlops:mlops@"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # identity (required in production)
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None

    @model_validator(mode="after")
    def _refuse_unsafe_production_configuration(self) -> Settings:
        if self.app_env != "production":
            return self

        problems: list[str] = []
        if _DEVELOPMENT_CREDENTIALS in self.database_url:
            problems.append("DATABASE_URL uses the development credentials")
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
