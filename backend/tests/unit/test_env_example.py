"""`.env.example` is the documented contract for configuration; keep it honest."""

from __future__ import annotations

import re
from pathlib import Path

from mlops.platform.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
# Read by docker compose only, not by the application.
COMPOSE_ONLY = {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "DB_PORT"}


def _example_keys() -> set[str]:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


def test_every_setting_is_documented_in_env_example() -> None:
    expected = {name.upper() for name in Settings.model_fields}

    missing = expected - _example_keys()

    assert not missing, f"add to .env.example: {sorted(missing)}"


def test_env_example_has_no_unknown_variables() -> None:
    known = {name.upper() for name in Settings.model_fields} | COMPOSE_ONLY

    unknown = _example_keys() - known

    assert not unknown, f"not a setting and not compose-only: {sorted(unknown)}"
