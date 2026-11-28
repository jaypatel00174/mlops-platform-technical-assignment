"""Approval workflow and the audit trail. Acceptance scenarios 1 and 2 end to end."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from mlops.governance.application.audit import AuditService
from mlops.governance.infrastructure.repositories import SqlApprovalRepository
from mlops.kernel.vocabulary import ApprovalStatus, Role
from support.api import PUMP, version_payload

pytestmark = pytest.mark.integration

Headers = dict[str, str]
VERSIONS = "/api/v1/models/pump-failure-predictor/versions"


def validated_version(client: TestClient, alice: Headers, version: str = "2.0.0") -> None:
    """A model with one validated version, ready for a decision."""
    client.post("/api/v1/models", json=PUMP, headers=alice)
    client.post(VERSIONS, json=version_payload(version), headers=alice)
    response = client.post(f"{VERSIONS}/{version}/validate", headers=alice)
    assert response.status_code == 200, response.text


def audit_trail(
    client: TestClient, headers: Headers, version: str = "2.0.0"
) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v1/audit-events",
        params={"entity_type": "model_version", "entity_id": f"pump-failure-predictor@{version}"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def test_scenarios_1_and_2_register_a_model_with_two_versions_and_approve_one(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    client.post("/api/v1/models", json=PUMP, headers=alice)
    for version in ("1.0.0", "2.0.0"):
        client.post(VERSIONS, json=version_payload(version), headers=alice)
        client.post(f"{VERSIONS}/{version}/validate", headers=alice)

    approved = client.post(
        f"{VERSIONS}/2.0.0/approve", json={"comment": "metrics look good"}, headers=bob
    )
    listed = {v["version"]: v for v in client.get(VERSIONS, headers=alice).json()["items"]}

    assert approved.status_code == 200
    assert approved.json()["stage"] == "APPROVED"
    assert approved.json()["approval_status"] == "APPROVED"
    assert listed["2.0.0"]["stage"] == "APPROVED"
    assert listed["1.0.0"]["stage"] == "VALIDATED"
    assert listed["1.0.0"]["approval_status"] == "PENDING"


def test_the_audit_trail_records_who_did_what_in_order(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    validated_version(client, alice)
    client.post(f"{VERSIONS}/2.0.0/approve", json={"comment": "ship it"}, headers=bob)

    trail = audit_trail(client, bob)

    assert [(e["action"], e["actor"]) for e in trail] == [
        ("version.registered", "alice"),
        ("version.validated", "alice"),
        ("version.approved", "bob"),
    ]
    assert trail[1]["before"] == {"stage": "DRAFT", "approval_status": "PENDING"}
    assert trail[1]["after"] == {"stage": "VALIDATED", "approval_status": "PENDING"}
    assert trail[2]["after"]["comment"] == "ship it"
    assert trail[2]["after"]["stage"] == "APPROVED"


def test_one_correlation_id_links_the_client_call_and_the_audit_entry(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    client.post("/api/v1/models", json=PUMP, headers=alice)

    response = client.post(
        VERSIONS,
        json=version_payload("2.0.0"),
        headers={**alice, "X-Correlation-ID": "trace-link-12345"},
    )
    [registered] = audit_trail(client, bob)

    assert response.headers["X-Correlation-ID"] == "trace-link-12345"
    assert registered["correlation_id"] == "trace-link-12345"


def test_an_approver_can_approve_without_a_comment(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    validated_version(client, alice)

    response = client.post(f"{VERSIONS}/2.0.0/approve", headers=bob)

    assert response.status_code == 200


def test_the_registrant_cannot_approve_their_own_version_even_with_the_approver_role(
    client: TestClient, auth: Callable[..., Headers], alice: Headers
) -> None:
    validated_version(client, alice)
    alice_the_approver = auth("alice", Role.ML_ENGINEER, Role.APPROVER)

    response = client.post(f"{VERSIONS}/2.0.0/approve", headers=alice_the_approver)

    assert response.status_code == 403
    assert response.json()["code"] == "SEPARATION_OF_DUTIES"
    assert client.get(f"{VERSIONS}/2.0.0", headers=alice).json()["stage"] == "VALIDATED"


def test_even_an_admin_cannot_approve_their_own_version(
    client: TestClient, auth: Callable[..., Headers]
) -> None:
    admin = auth("alice", Role.ADMIN)
    client.post("/api/v1/models", json=PUMP, headers=admin)
    client.post(VERSIONS, json=version_payload("2.0.0"), headers=admin)
    client.post(f"{VERSIONS}/2.0.0/validate", headers=admin)

    response = client.post(f"{VERSIONS}/2.0.0/approve", headers=admin)

    assert response.status_code == 403
    assert response.json()["code"] == "SEPARATION_OF_DUTIES"


def test_a_draft_cannot_be_approved_yet(client: TestClient, alice: Headers, bob: Headers) -> None:
    client.post("/api/v1/models", json=PUMP, headers=alice)
    client.post(VERSIONS, json=version_payload("2.0.0"), headers=alice)

    response = client.post(f"{VERSIONS}/2.0.0/approve", headers=bob)

    assert response.status_code == 409
    assert response.json()["code"] == "INVALID_TRANSITION"
    assert response.json()["details"]["required_stage"] == "VALIDATED"


def test_approving_twice_is_refused(client: TestClient, alice: Headers, bob: Headers) -> None:
    validated_version(client, alice)
    client.post(f"{VERSIONS}/2.0.0/approve", headers=bob)

    again = client.post(f"{VERSIONS}/2.0.0/approve", headers=bob)

    assert again.status_code == 409
    assert again.json()["code"] == "INVALID_TRANSITION"


def test_approving_an_unknown_version_is_a_404(client: TestClient, bob: Headers) -> None:
    response = client.post(f"{VERSIONS}/9.9.9/approve", headers=bob)

    assert response.status_code == 404


def test_a_rejection_needs_a_reason(client: TestClient, alice: Headers, bob: Headers) -> None:
    validated_version(client, alice)

    response = client.post(f"{VERSIONS}/2.0.0/reject", headers=bob)

    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["field"] == "comment"


def test_a_rejected_version_stays_validated_and_can_be_approved_after_a_fix(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    validated_version(client, alice)

    rejected = client.post(
        f"{VERSIONS}/2.0.0/reject", json={"comment": "drift too high"}, headers=bob
    )
    approved = client.post(f"{VERSIONS}/2.0.0/approve", json={"comment": "fixed"}, headers=bob)

    assert rejected.status_code == 200
    assert rejected.json()["stage"] == "VALIDATED"
    assert rejected.json()["approval_status"] == "REJECTED"
    assert approved.json()["stage"] == "APPROVED"
    actions = [e["action"] for e in audit_trail(client, bob)]
    assert actions[-2:] == ["version.rejected", "version.approved"]


def test_every_decision_is_stored_as_an_approval_record(
    client: TestClient,
    alice: Headers,
    bob: Headers,
    session_factory: sessionmaker[Session],
) -> None:
    validated_version(client, alice)
    client.post(f"{VERSIONS}/2.0.0/reject", json={"comment": "drift too high"}, headers=bob)
    client.post(f"{VERSIONS}/2.0.0/approve", json={"comment": "fixed"}, headers=bob)

    with session_factory() as session:
        decisions = SqlApprovalRepository(session).list_for_version("pump-failure-predictor@2.0.0")

    assert [(d.decision, d.decided_by, d.comment) for d in decisions] == [
        (ApprovalStatus.REJECTED, "bob", "drift too high"),
        (ApprovalStatus.APPROVED, "bob", "fixed"),
    ]


def test_a_stale_if_match_blocks_the_approval_and_changes_nothing(
    client: TestClient, alice: Headers, bob: Headers
) -> None:
    validated_version(client, alice)

    response = client.post(f"{VERSIONS}/2.0.0/approve", headers={**bob, "If-Match": 'W/"1"'})

    assert response.status_code == 409
    assert response.json()["code"] == "STALE_VERSION"
    assert client.get(f"{VERSIONS}/2.0.0", headers=alice).json()["stage"] == "VALIDATED"
    assert [e["action"] for e in audit_trail(client, bob)] == [
        "version.registered",
        "version.validated",
    ]


def test_if_the_audit_write_fails_the_whole_approval_is_rolled_back(
    client: TestClient,
    alice: Headers,
    bob: Headers,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The change, its approval record and its audit entry commit together or not at all."""
    validated_version(client, alice)
    original = AuditService.record

    def failing(self: AuditService, **kwargs: Any) -> None:
        if kwargs["action"] == "version.approved":
            raise RuntimeError("audit store is down")
        original(self, **kwargs)

    monkeypatch.setattr(AuditService, "record", failing)

    response = client.post(f"{VERSIONS}/2.0.0/approve", headers=bob)

    monkeypatch.undo()
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL"
    assert client.get(f"{VERSIONS}/2.0.0", headers=alice).json()["stage"] == "VALIDATED"
    with session_factory() as session:
        assert SqlApprovalRepository(session).list_for_version("pump-failure-predictor@2.0.0") == []


# ---------------------------------------------------------------------------------- audit


def test_the_audit_endpoint_needs_both_query_parameters(client: TestClient, bob: Headers) -> None:
    missing = client.get("/api/v1/audit-events", params={"entity_type": "model"}, headers=bob)

    assert missing.status_code == 422
    assert missing.json()["details"]["errors"][0]["field"] == "query.entity_id"


def test_an_entity_without_history_has_an_empty_trail(client: TestClient, bob: Headers) -> None:
    response = client.get(
        "/api/v1/audit-events",
        params={"entity_type": "model", "entity_id": "nothing-here"},
        headers=bob,
    )

    assert response.json() == {"items": []}


def test_the_audit_trail_of_one_tenant_is_invisible_to_another(
    client: TestClient, alice: Headers, auth: Callable[..., Headers]
) -> None:
    validated_version(client, alice)
    other_auditor = auth("olga", Role.APPROVER, tenant="other-plant")

    assert audit_trail(client, other_auditor) == []


def test_an_admin_can_read_the_audit_trail(
    client: TestClient, alice: Headers, auth: Callable[..., Headers]
) -> None:
    client.post("/api/v1/models", json=PUMP, headers=alice)

    trail = client.get(
        "/api/v1/audit-events",
        params={"entity_type": "model", "entity_id": "pump-failure-predictor"},
        headers=auth("root", Role.ADMIN),
    ).json()["items"]

    assert [e["action"] for e in trail] == ["model.registered"]
    assert trail[0]["after"]["framework"] == "scikit-learn"
