"""Idempotency (acceptance scenario 8): same key and body replays, different body is rejected."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from mlops.deployment.domain.idempotency import (
    IdempotencyRecord,
    Replay,
    decide,
    fingerprint,
    validate_key,
)
from mlops.kernel.errors import IdempotencyKeyReused, ValidationFailure

BODY = {"model_id": "pump-failure-predictor", "version": "2.0.0", "environment": "production"}


def test_the_fingerprint_ignores_key_order() -> None:
    reordered = dict(reversed(list(BODY.items())))

    assert fingerprint(BODY) == fingerprint(reordered)


def test_the_fingerprint_changes_when_any_value_changes() -> None:
    assert fingerprint(BODY) != fingerprint({**BODY, "version": "2.0.1"})
    assert fingerprint(BODY) != fingerprint({**BODY, "environment": "staging"})


def test_the_fingerprint_handles_nested_and_unicode_values_deterministically() -> None:
    body = {"meta": {"b": [1, 2, {"z": "é"}], "a": None}}

    assert fingerprint(body) == fingerprint(dict(reversed(list(body.items()))))
    assert len(fingerprint(body)) == 64


def test_a_new_key_is_processed() -> None:
    assert decide(stored_hash=None, new_hash=fingerprint(BODY)) is Replay.PROCESS


def test_the_same_key_and_body_replays_the_stored_response() -> None:
    assert decide(stored_hash=fingerprint(BODY), new_hash=fingerprint(BODY)) is Replay.REPLAY


def test_the_same_key_with_a_different_body_is_rejected() -> None:
    with pytest.raises(IdempotencyKeyReused) as error:
        decide(stored_hash=fingerprint(BODY), new_hash=fingerprint({**BODY, "version": "9.9.9"}))

    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.parametrize(
    "key", ["abcd1234", "6f1b3d9e-5a8c-4b60-9f3e-2f1d0a7c1b11", "req_2026.01:abc", "A" * 128]
)
def test_reasonable_keys_are_accepted(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key", ["", "short", "has space in it", "-leading-dash-key", "A" * 129, "emoji-key-🙂-123"]
)
def test_unreasonable_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        validate_key(key)

    assert error.value.details["field"] == "Idempotency-Key"


def test_a_stored_record_expires_after_its_time_to_live(now: datetime) -> None:
    record = IdempotencyRecord.create(
        key="key-12345678",
        actor="carol",
        request_hash=fingerprint(BODY),
        response_status=202,
        response_body={"deployment_id": "dep-1"},
        now=now,
        ttl=timedelta(hours=24),
    )

    assert record.expires_at == now + timedelta(hours=24)
    assert not record.is_expired(now + timedelta(hours=23, minutes=59))
    assert record.is_expired(now + timedelta(hours=24))


def test_the_default_time_to_live_is_24_hours(now: datetime) -> None:
    record = IdempotencyRecord.create(
        key="key-12345678",
        actor="carol",
        request_hash="h",
        response_status=202,
        response_body={},
        now=now,
    )

    assert record.expires_at - record.created_at == timedelta(hours=24)
