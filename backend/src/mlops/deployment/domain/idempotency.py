"""Idempotency: the same request sent twice has the effect of being sent once.

The API stores the response for an `Idempotency-Key` together with a fingerprint of the request
body, in the same transaction as the work itself. A repeat with the same body replays the stored
response; a repeat with a different body is a client error.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Self

from mlops.kernel.errors import IdempotencyKeyReused, ValidationFailure

DEFAULT_TTL = timedelta(hours=24)
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class Replay(StrEnum):
    PROCESS = "PROCESS"  # first time this key is seen: do the work
    REPLAY = "REPLAY"  # same key and body: return the stored response


def validate_key(key: str) -> str:
    if not _KEY.match(key):
        raise ValidationFailure(
            "Idempotency-Key must be 8 to 128 characters: letters, digits, '.', '_', ':' or '-'",
            field="Idempotency-Key",
        )
    return key


def fingerprint(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON form, so key order and whitespace do not matter."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decide(*, stored_hash: str | None, new_hash: str) -> Replay:
    if stored_hash is None:
        return Replay.PROCESS
    if stored_hash == new_hash:
        return Replay.REPLAY
    raise IdempotencyKeyReused(
        "this Idempotency-Key was already used with a different request body"
    )


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    key: str
    actor: str
    request_hash: str
    response_status: int
    response_body: Mapping[str, Any]
    created_at: datetime
    expires_at: datetime
    tenant_id: str = "default"

    @classmethod
    def create(
        cls,
        *,
        key: str,
        actor: str,
        request_hash: str,
        response_status: int,
        response_body: Mapping[str, Any],
        now: datetime,
        ttl: timedelta = DEFAULT_TTL,
        tenant_id: str = "default",
    ) -> Self:
        return cls(
            key=key,
            actor=actor,
            request_hash=request_hash,
            response_status=response_status,
            response_body=dict(response_body),
            created_at=now,
            expires_at=now + ttl,
            tenant_id=tenant_id,
        )

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at
