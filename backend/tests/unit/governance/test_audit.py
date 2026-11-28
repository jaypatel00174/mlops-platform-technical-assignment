from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

from mlops.governance.domain.audit import AuditEvent, record
from mlops.kernel.errors import ValidationFailure


def make_event(now: datetime, **overrides: object) -> AuditEvent:
    values: dict[str, object] = {
        "id": "aud-1",
        "actor": "bob",
        "action": "version.approved",
        "entity_type": "model_version",
        "entity_id": "ver-1",
        "correlation_id": "c-123",
        "now": now,
    }
    values.update(overrides)
    return record(**values)  # type: ignore[arg-type]


def test_an_audit_event_captures_who_did_what_to_which_entity(now: datetime) -> None:
    event = make_event(now, before={"stage": "VALIDATED"}, after={"stage": "APPROVED"})

    assert event.actor == "bob"
    assert event.action == "version.approved"
    assert event.entity_type == "model_version"
    assert event.entity_id == "ver-1"
    assert event.correlation_id == "c-123"
    assert event.occurred_at == now
    assert event.before == {"stage": "VALIDATED"}
    assert event.after == {"stage": "APPROVED"}
    assert event.tenant_id == "default"


def test_audit_events_are_immutable(now: datetime) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        make_event(now).actor = "mallory"  # type: ignore[misc]


def test_the_snapshot_is_a_copy_so_later_changes_cannot_rewrite_history(now: datetime) -> None:
    state = {"stage": "VALIDATED"}

    event = make_event(now, before=state)
    state["stage"] = "PRODUCTION"

    assert event.before == {"stage": "VALIDATED"}


@pytest.mark.parametrize("field", ["actor", "action", "entity_type", "entity_id", "correlation_id"])
def test_required_fields_cannot_be_blank(now: datetime, field: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_event(now, **{field: " "})

    assert error.value.details["field"] == field
