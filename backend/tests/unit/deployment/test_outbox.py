from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mlops.deployment.domain.outbox import PAYLOAD_SCHEMA_VERSION, OutboxEvent, OutboxStatus
from mlops.kernel.errors import InvalidTransition

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
LEASE = timedelta(seconds=60)


def an_event(**overrides: object) -> OutboxEvent:
    values: dict[str, object] = {
        "id": "out-1",
        "type": "deployment.requested",
        "payload": {"deployment_id": "dep-1"},
        "now": NOW,
    }
    values.update(overrides)
    return OutboxEvent.create(**values)  # type: ignore[arg-type]


def test_a_new_event_is_pending_and_versioned() -> None:
    event = an_event()

    assert event.status is OutboxStatus.PENDING
    assert event.attempts == 0
    assert event.payload["schema_version"] == PAYLOAD_SCHEMA_VERSION


def test_a_pending_event_is_claimable_only_once_it_is_due() -> None:
    event = an_event(available_at=NOW + timedelta(seconds=10))

    assert not event.is_claimable(NOW)
    assert event.is_claimable(NOW + timedelta(seconds=10))


def test_claiming_takes_a_lease_and_counts_the_attempt() -> None:
    claimed = an_event().claim("worker-a", NOW, LEASE)

    assert claimed.status is OutboxStatus.CLAIMED
    assert claimed.locked_by == "worker-a"
    assert claimed.lease_until == NOW + LEASE
    assert claimed.attempts == 1


def test_a_live_lease_blocks_other_workers() -> None:
    claimed = an_event().claim("worker-a", NOW, LEASE)

    assert not claimed.is_claimable(NOW + timedelta(seconds=59))
    with pytest.raises(InvalidTransition):
        claimed.claim("worker-b", NOW + timedelta(seconds=59), LEASE)


def test_an_expired_lease_lets_another_worker_take_over() -> None:
    claimed = an_event().claim("worker-a", NOW, LEASE)

    taken = claimed.claim("worker-b", NOW + LEASE, LEASE)

    assert taken.locked_by == "worker-b"
    assert taken.attempts == 2


def test_completing_releases_the_lease() -> None:
    done = an_event().claim("worker-a", NOW, LEASE).complete()

    assert done.status is OutboxStatus.DONE
    assert done.locked_by is None
    assert done.lease_until is None
    assert not done.is_claimable(NOW + timedelta(days=1))


def test_rescheduling_waits_until_the_new_time() -> None:
    later = NOW + timedelta(minutes=5)

    waiting = an_event().claim("worker-a", NOW, LEASE).reschedule(later)

    assert waiting.status is OutboxStatus.PENDING
    assert waiting.attempts == 1  # the failed delivery still counts
    assert not waiting.is_claimable(NOW + timedelta(minutes=4))
    assert waiting.is_claimable(later)


def test_a_dead_event_is_never_claimed_again() -> None:
    dead = an_event().claim("worker-a", NOW, LEASE).give_up()

    assert dead.status is OutboxStatus.DEAD
    assert not dead.is_claimable(NOW + timedelta(days=30))


@pytest.mark.parametrize("release", ["complete", "give_up"])
def test_only_a_claimed_event_can_be_released(release: str) -> None:
    with pytest.raises(InvalidTransition):
        getattr(an_event(), release)()


def test_only_a_claimed_event_can_be_rescheduled() -> None:
    with pytest.raises(InvalidTransition):
        an_event().reschedule(NOW)
