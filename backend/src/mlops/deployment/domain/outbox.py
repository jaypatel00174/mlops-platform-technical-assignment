"""Transactional outbox: the durable "work to do" note written in the same transaction as the
state change that causes it (ADR-002). Workers claim pending events; this module only defines the
value. Claiming is done by SQL (FOR UPDATE SKIP LOCKED); the value rules live here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from mlops.kernel.errors import InvalidTransition

PAYLOAD_SCHEMA_VERSION = 1


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DONE = "DONE"
    DEAD = "DEAD"  # gave up after too many attempts; needs an operator


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: str
    type: str
    payload: Mapping[str, Any]
    created_at: datetime
    available_at: datetime
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    locked_by: str | None = None
    lease_until: datetime | None = None
    tenant_id: str = "default"

    @classmethod
    def create(
        cls,
        *,
        id: str,
        type: str,
        payload: Mapping[str, Any],
        now: datetime,
        available_at: datetime | None = None,
        tenant_id: str = "default",
    ) -> Self:
        # Payloads are versioned so a newer worker can still read events written by an older API.
        versioned = {"schema_version": PAYLOAD_SCHEMA_VERSION, **payload}
        return cls(
            id=id,
            type=type,
            payload=versioned,
            created_at=now,
            available_at=available_at or now,
            tenant_id=tenant_id,
        )

    # -- delivery lifecycle ----------------------------------------------------------------
    # Delivery is at-least-once: a claimed event whose lease expires (the worker died) becomes
    # claimable again, so handlers must be idempotent.

    def is_claimable(self, now: datetime) -> bool:
        if self.status is OutboxStatus.PENDING:
            return self.available_at <= now
        if self.status is OutboxStatus.CLAIMED:
            return self.lease_until is not None and self.lease_until <= now
        return False

    def claim(self, worker_id: str, now: datetime, lease: timedelta) -> Self:
        if not self.is_claimable(now):
            raise InvalidTransition(
                f"outbox event {self.id!r} cannot be claimed in status {self.status.value}",
                current=self.status.value,
                target=OutboxStatus.CLAIMED.value,
                allowed=[],
            )
        return replace(
            self,
            status=OutboxStatus.CLAIMED,
            attempts=self.attempts + 1,
            locked_by=worker_id,
            lease_until=now + lease,
        )

    def complete(self) -> Self:
        return self._released(OutboxStatus.DONE)

    def reschedule(self, available_at: datetime) -> Self:
        """Put it back to wait until `available_at` (backoff after a transient failure)."""
        return replace(self._released(OutboxStatus.PENDING), available_at=available_at)

    def give_up(self) -> Self:
        """Park it for an operator. A dead event is never claimed again."""
        return self._released(OutboxStatus.DEAD)

    def _released(self, status: OutboxStatus) -> Self:
        if self.status is not OutboxStatus.CLAIMED:
            raise InvalidTransition(
                f"outbox event {self.id!r} is not claimed",
                current=self.status.value,
                target=status.value,
                allowed=[],
            )
        return replace(self, status=status, locked_by=None, lease_until=None)
