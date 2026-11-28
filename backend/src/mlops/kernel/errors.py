"""Typed domain errors shared by every module.

Domain code raises these; the API layer maps `code` to an HTTP status and the standard
problem-details envelope. They carry JSON-friendly `details` and never know about HTTP.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class. `code` is the stable, machine-readable identifier clients can rely on."""

    code = "DOMAIN_ERROR"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details


class ValidationFailure(DomainError):
    code = "VALIDATION_ERROR"


class NotFound(DomainError):
    code = "NOT_FOUND"


class AlreadyExists(DomainError):
    code = "ALREADY_EXISTS"


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"


class Forbidden(DomainError):
    code = "FORBIDDEN"


class SeparationOfDuties(Forbidden):
    code = "SEPARATION_OF_DUTIES"


class PolicyViolation(DomainError):
    """A promotion or governance rule refuses the action. Classified as failure class POLICY."""

    code = "POLICY_VIOLATION"


class DeploymentConflict(DomainError):
    code = "DEPLOYMENT_CONFLICT"


class StaleVersion(DomainError):
    code = "STALE_VERSION"


class UnsafeRollback(DomainError):
    code = "UNSAFE_ROLLBACK"


class IdempotencyKeyReused(DomainError):
    code = "IDEMPOTENCY_KEY_REUSED"


class RuntimeFailure(DomainError):
    """Raised by ModelRuntime adapters. The domain classifies these into failure classes."""

    code = "RUNTIME_FAILURE"


class RuntimeTimeout(RuntimeFailure):
    code = "RUNTIME_TIMEOUT"


class RuntimeUnavailable(RuntimeFailure):
    code = "RUNTIME_UNAVAILABLE"


class RuntimeRejected(RuntimeFailure):
    code = "RUNTIME_REJECTED"
