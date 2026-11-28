"""Transactional outbox: the durable "work to do" note written in the same transaction as the
state change that causes it (ADR-002). Workers claim pending events; this module only defines the
value. The claiming logic arrives with the worker milestone.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Self

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
