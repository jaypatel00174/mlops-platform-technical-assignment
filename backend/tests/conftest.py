"""Shared fixtures. Tests never read the real clock or the real environment."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

FIXED_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    """A fixed, timezone-aware instant so time-dependent logic is deterministic."""
    return FIXED_NOW
