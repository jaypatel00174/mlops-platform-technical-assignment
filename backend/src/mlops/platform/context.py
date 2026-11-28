"""Per-request context shared by logging, error responses and audit entries."""

from __future__ import annotations

from contextvars import ContextVar, Token

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)

# Used when code runs outside a request, for example the seed loader or a worker job.
NO_REQUEST = "system"


def get_correlation_id() -> str:
    return _correlation_id.get() or NO_REQUEST


def set_correlation_id(value: str) -> Token[str | None]:
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str | None]) -> None:
    _correlation_id.reset(token)
