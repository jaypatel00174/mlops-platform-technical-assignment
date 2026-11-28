from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mlops.governance.domain.approval import ApprovalDecision
from mlops.governance.domain.audit import record
from mlops.governance.infrastructure.repositories import SqlApprovalRepository, SqlAuditRepository
from mlops.kernel.errors import AlreadyExists, NotFound
from mlops.kernel.vocabulary import ApprovalStatus
from mlops.monitoring.domain.entities import MetricSample
from mlops.monitoring.infrastructure.repositories import SqlMetricSampleRepository
from support.builders import NOW, at, persist_model_and_version

pytestmark = pytest.mark.integration


def audit_event(event_id: str = "aud-1", minutes: int = 0, **overrides: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "id": event_id,
        "actor": "bob",
        "action": "version.approved",
        "entity_type": "model_version",
        "entity_id": "ver-1",
        "correlation_id": "c-1",
        "now": at(minutes),
        "before": {"stage": "VALIDATED"},
        "after": {"stage": "APPROVED"},
    }
    values.update(overrides)
    return record(**values)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------ audit


def test_audit_events_survive_a_round_trip_in_chronological_order(session: Session) -> None:
    repo = SqlAuditRepository(session)
    repo.add(audit_event("aud-2", minutes=5, action="version.deployed"))
    repo.add(audit_event("aud-1", minutes=1))
    repo.add(audit_event("aud-3", minutes=2, entity_id="other"))
    session.commit()

    events = repo.list_for_entity("model_version", "ver-1")

    assert [e.id for e in events] == ["aud-1", "aud-2"]
    assert events[0].before == {"stage": "VALIDATED"}
    assert events[0].after == {"stage": "APPROVED"}
    assert events[0].occurred_at == at(1)


def test_an_audit_event_without_snapshots_is_allowed(session: Session) -> None:
    repo = SqlAuditRepository(session)
    repo.add(audit_event(before=None, after=None))
    session.commit()

    [event] = repo.list_for_entity("model_version", "ver-1")

    assert event.before is None
    assert event.after is None


def test_audit_ids_are_unique(session: Session) -> None:
    repo = SqlAuditRepository(session)
    repo.add(audit_event())
    session.commit()

    with pytest.raises(AlreadyExists):
        repo.add(audit_event())


def test_audit_history_is_tenant_scoped(session: Session) -> None:
    SqlAuditRepository(session).add(audit_event())
    session.commit()

    assert SqlAuditRepository(session, "tenant-b").list_for_entity("model_version", "ver-1") == []


def test_audit_history_cannot_be_rewritten_or_erased(session: Session) -> None:
    SqlAuditRepository(session).add(audit_event())
    session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("UPDATE audit_event SET actor = 'mallory'"))
    session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        session.execute(text("DELETE FROM audit_event"))


# --------------------------------------------------------------------------------- approvals


def decision(**overrides: object) -> ApprovalDecision:
    values: dict[str, object] = {
        "version_id": "pump-failure-predictor@2.0.0",
        "decision": ApprovalStatus.APPROVED,
        "decided_by": "bob",
        "comment": "metrics look good",
        "decided_at": NOW,
    }
    values.update(overrides)
    return ApprovalDecision(**values)  # type: ignore[arg-type]


def test_approval_decisions_are_stored_in_order(session: Session) -> None:
    persist_model_and_version(session)
    repo = SqlApprovalRepository(session)
    repo.add(
        decision(decision=ApprovalStatus.REJECTED, comment="drift", decided_at=at(1)), id="apr-1"
    )
    repo.add(decision(decided_at=at(2)), id="apr-2")
    session.commit()

    decisions = repo.list_for_version("pump-failure-predictor@2.0.0")

    assert [d.decision for d in decisions] == [ApprovalStatus.REJECTED, ApprovalStatus.APPROVED]
    assert decisions[0].comment == "drift"
    assert decisions[1].decided_at == at(2)


def test_an_approval_for_an_unknown_version_is_refused(session: Session) -> None:
    with pytest.raises(NotFound):
        SqlApprovalRepository(session).add(decision(version_id="ghost@1.0.0"), id="apr-1")


def test_approval_ids_are_unique(session: Session) -> None:
    persist_model_and_version(session)
    repo = SqlApprovalRepository(session)
    repo.add(decision(), id="apr-1")
    session.commit()

    with pytest.raises(AlreadyExists):
        repo.add(decision(), id="apr-1")


# ------------------------------------------------------------------------------------ metrics


def sample(day: int = 1, **overrides: object) -> MetricSample:
    values: dict[str, object] = {
        "model_id": "pump-failure-predictor",
        "version": "2.0.0",
        "environment": "production",
        "ts": NOW + timedelta(days=day),
        "latency_ms": 90.0,
        "throughput_rpm": 1000.0,
        "error_rate": 0.01,
        "quality_score": 0.9,
        "drift_score": 0.1,
        "availability": 99.5,
    }
    values.update(overrides)
    return MetricSample(**values)  # type: ignore[arg-type]


def test_samples_are_stored_and_read_back_in_time_order(session: Session) -> None:
    persist_model_and_version(session)
    repo = SqlMetricSampleRepository(session)

    inserted = repo.add_many([sample(3), sample(1), sample(2)])
    session.commit()
    loaded = repo.list_for(
        model_id="pump-failure-predictor", version="2.0.0", environment="production"
    )

    assert inserted == 3
    assert [s.ts for s in loaded] == [NOW + timedelta(days=d) for d in (1, 2, 3)]
    assert loaded[0] == sample(1)


def test_ingest_is_idempotent_so_agent_retries_are_safe(session: Session) -> None:
    persist_model_and_version(session)
    repo = SqlMetricSampleRepository(session)
    repo.add_many([sample(1), sample(2)])
    session.commit()

    again = repo.add_many([sample(1), sample(2), sample(3)])
    session.commit()

    assert again == 1
    assert repo.count() == 3


def test_a_time_window_selects_the_matching_samples(session: Session) -> None:
    persist_model_and_version(session)
    repo = SqlMetricSampleRepository(session)
    repo.add_many([sample(day) for day in range(1, 6)])
    session.commit()

    window = repo.list_for(
        model_id="pump-failure-predictor",
        version="2.0.0",
        environment="production",
        start=NOW + timedelta(days=2),
        end=NOW + timedelta(days=4),
    )

    assert [s.ts.day for s in window] == [17, 18]


def test_samples_for_an_unknown_version_are_refused(session: Session) -> None:
    persist_model_and_version(session)
    repo = SqlMetricSampleRepository(session)

    with pytest.raises(NotFound):
        repo.add_many([sample(1, version="9.9.9")])


def test_an_empty_batch_inserts_nothing(session: Session) -> None:
    assert SqlMetricSampleRepository(session).add_many([]) == 0


def test_counting_can_be_limited_to_one_model_and_one_tenant(session: Session) -> None:
    persist_model_and_version(session)
    persist_model_and_version(session, model_id="valve-health-model")
    repo = SqlMetricSampleRepository(session)
    repo.add_many([sample(1), sample(2), replace(sample(1), model_id="valve-health-model")])
    session.commit()

    assert repo.count() == 3
    assert repo.count("valve-health-model") == 1
    assert SqlMetricSampleRepository(session, "tenant-b").count() == 0
