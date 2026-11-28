from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mlops.platform.config import Settings, get_settings

SETTING_ENV_NAMES = [name.upper() for name in Settings.model_fields]


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No inherited env vars and no .env file: every test starts from the defaults."""
    for name in SETTING_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


def test_defaults_are_valid_for_local_development() -> None:
    settings = Settings()

    assert settings.app_env == "local"
    assert settings.runtime_backend == "simulated"
    assert settings.queue_backend == "db_outbox"
    assert settings.default_tenant == "default"


def test_values_come_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    settings = Settings()

    assert settings.log_level == "DEBUG"
    assert settings.database_url == "sqlite:///:memory:"


def test_a_dotenv_file_in_the_repo_root_is_read_when_running_from_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`make bootstrap` writes .env at the repo root; the app runs from backend/."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (tmp_path / ".env").write_text("LOG_LEVEL=WARNING\nDEFAULT_TENANT=acme\n", encoding="utf-8")
    monkeypatch.chdir(backend)

    settings = Settings()

    assert settings.log_level == "WARNING"
    assert settings.default_tenant == "acme"


def test_a_local_dotenv_overrides_the_repo_root_and_the_environment_overrides_both(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (tmp_path / ".env").write_text("LOG_LEVEL=WARNING\nDEFAULT_TENANT=acme\n", encoding="utf-8")
    (backend / ".env").write_text("LOG_LEVEL=ERROR\n", encoding="utf-8")
    monkeypatch.chdir(backend)
    monkeypatch.setenv("DEFAULT_TENANT", "from-environment")

    settings = Settings()

    assert settings.log_level == "ERROR"
    assert settings.default_tenant == "from-environment"


def test_invalid_adapter_choice_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTIME_BACKEND", "carrier-pigeon")

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    assert get_settings() is first


def test_production_rejects_development_credentials_and_missing_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(ValidationError) as error:
        Settings()

    message = str(error.value)
    assert "development credentials" in message
    assert "AUTH_BACKEND must be oidc_jwt" in message
    assert "OIDC_ISSUER" in message
    assert "OIDC_AUDIENCE" in message
    assert "OIDC_JWKS_URL" in message


def test_production_accepts_a_complete_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_BACKEND", "oidc_jwt")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://svc:s3cret-value@db.internal:5432/mlops"
    )
    monkeypatch.setenv("OIDC_ISSUER", "https://id.example.org/")
    monkeypatch.setenv("OIDC_AUDIENCE", "mlops-platform")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://id.example.org/.well-known/jwks.json")

    settings = Settings()

    assert settings.app_env == "production"
