"""Small ports every module may depend on. Adapters live in the platform package or the modules'
infrastructure layers and are wired together in the composition root (mlops.wiring)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant as a timezone-aware UTC datetime."""
        ...


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str:
        """A new unique identifier such as 'aud-3f9c2a1b7d4e'."""
        ...


class AuditRecorder(Protocol):
    """Lets any module write an audit entry without importing the governance module."""

    def record(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
    ) -> None:
        """Append an audit event inside the caller's database transaction."""
        ...
