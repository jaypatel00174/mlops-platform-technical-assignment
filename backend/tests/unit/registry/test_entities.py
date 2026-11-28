from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from mlops.kernel.errors import InvalidTransition, PolicyViolation, ValidationFailure
from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.registry.domain.entities import Model, ModelVersion, SemVer

DIGEST = "sha256:" + "a" * 64


def make_model(now: datetime, **overrides: object) -> Model:
    values: dict[str, object] = {
        "id": "pump-failure-predictor",
        "name": "Pump Failure Predictor",
        "owner": "Reliability AI Team",
        "framework": "scikit-learn",
        "now": now,
    }
    values.update(overrides)
    return Model.register(**values)  # type: ignore[arg-type]


def make_version(now: datetime, **overrides: object) -> ModelVersion:
    values: dict[str, object] = {
        "id": "ver-1",
        "model_id": "pump-failure-predictor",
        "version": "2.0.0",
        "algorithm": "gradient-boosting",
        "artifact_uri": "s3://models/pump/2.0.0",
        "artifact_digest": DIGEST,
        "training_data_ref": "s3://datasets/pump/2026-06",
        "registered_by": "alice",
        "now": now,
    }
    values.update(overrides)
    return ModelVersion.register(**values)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ model


def test_a_model_is_registered_with_audit_timestamps(now: datetime) -> None:
    model = make_model(now, tags=("plant-a",))

    assert model.id == "pump-failure-predictor"
    assert model.tenant_id == "default"
    assert model.tags == ("plant-a",)
    assert model.created_at == model.updated_at == now


@pytest.mark.parametrize(
    "bad_id", ["", "Pump", "-pump", "pump_failure", "a", "x" * 64, "pump failure"]
)
def test_a_model_id_must_be_a_lowercase_slug(now: datetime, bad_id: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_model(now, id=bad_id)

    assert error.value.details["field"] == "id"


@pytest.mark.parametrize("field", ["name", "owner", "framework"])
def test_required_model_text_fields_cannot_be_blank(now: datetime, field: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_model(now, **{field: "   "})

    assert error.value.details["field"] == field


# --------------------------------------------------------------------------- semantic version


@pytest.mark.parametrize("text", ["0.0.1", "1.0.0", "10.20.30", "1.0.0-rc.1", "1.0.0+build.5"])
def test_valid_semantic_versions_parse(text: str) -> None:
    assert str(SemVer.parse(text)) == text


@pytest.mark.parametrize("text", ["1.0", "v1.0.0", "01.0.0", "1.0.0.0", "", "latest", "1.0.-1"])
def test_invalid_semantic_versions_are_rejected(text: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        SemVer.parse(text)

    assert error.value.details["field"] == "version"


def test_semantic_versions_order_numerically_and_prereleases_sort_first() -> None:
    ordered = ["1.0.0-rc.1", "1.0.0", "1.2.0", "1.10.0", "2.0.0"]

    backwards = ordered[::-1]

    assert sorted(backwards, key=SemVer.parse) == ordered


# --------------------------------------------------------------------------- model version


def test_a_registered_version_starts_as_an_unapproved_draft(now: datetime) -> None:
    version = make_version(now)

    assert version.stage is Stage.DRAFT
    assert version.approval_status is ApprovalStatus.PENDING
    assert version.registered_by == "alice"
    assert version.row_version == 0
    assert version.metadata == {}


@pytest.mark.parametrize(
    "uri",
    [
        "s3://models/pump/2.0.0",
        "gs://bucket/m",
        "abfss://c@acct/m",
        "file:///models/m",
        "https://h/m",
    ],
)
def test_supported_artifact_uri_schemes_are_accepted(now: datetime, uri: str) -> None:
    assert make_version(now, artifact_uri=uri).artifact_uri == uri


@pytest.mark.parametrize("uri", ["", "pump/2.0.0", "ftp://host/m", "s3://", "javascript:alert(1)"])
def test_unsupported_artifact_uris_are_rejected(now: datetime, uri: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_version(now, artifact_uri=uri)

    assert error.value.details["field"] == "artifact_uri"


@pytest.mark.parametrize(
    "digest", ["", "abc", "sha256:xyz", "sha256:" + "A" * 64, "md5:" + "a" * 32]
)
def test_the_artifact_digest_must_be_a_lowercase_sha256(now: datetime, digest: str) -> None:
    with pytest.raises(ValidationFailure) as error:
        make_version(now, artifact_digest=digest)

    assert error.value.details["field"] == "artifact_digest"


def test_versions_are_immutable_values(now: datetime) -> None:
    version = make_version(now)

    with pytest.raises(dataclasses.FrozenInstanceError):
        version.stage = Stage.PRODUCTION  # type: ignore[misc]


def test_validation_moves_a_draft_to_validated(now: datetime) -> None:
    later = now + timedelta(minutes=5)

    validated = make_version(now).mark_validated(later)

    assert validated.stage is Stage.VALIDATED
    assert validated.updated_at == later
    assert validated.created_at == now


def test_a_draft_cannot_be_approved(now: datetime) -> None:
    with pytest.raises(InvalidTransition):
        make_version(now).mark_approved(now)


def test_approval_moves_a_validated_version_to_approved(now: datetime) -> None:
    approved = make_version(now).mark_validated(now).mark_approved(now)

    assert approved.stage is Stage.APPROVED
    assert approved.approval_status is ApprovalStatus.APPROVED


def test_rejection_keeps_the_version_validated_and_allows_resubmission(now: datetime) -> None:
    rejected = make_version(now).mark_validated(now).mark_rejected(now)

    assert rejected.stage is Stage.VALIDATED
    assert rejected.approval_status is ApprovalStatus.REJECTED

    resubmitted = rejected.mark_approved(now)
    assert resubmitted.stage is Stage.APPROVED
    assert resubmitted.approval_status is ApprovalStatus.APPROVED


def test_only_a_validated_version_can_be_rejected(now: datetime) -> None:
    with pytest.raises(InvalidTransition):
        make_version(now).mark_rejected(now)


def test_deployment_successes_advance_the_stage_through_staging_to_production(
    now: datetime,
) -> None:
    approved = make_version(now).mark_validated(now).mark_approved(now)

    staged = approved.after_successful_deployment("staging", now)
    produced = staged.after_successful_deployment("production", now)

    assert staged.stage is Stage.STAGING
    assert produced.stage is Stage.PRODUCTION


def test_an_approved_version_cannot_skip_staging(now: datetime) -> None:
    approved = make_version(now).mark_validated(now).mark_approved(now)

    with pytest.raises(InvalidTransition):
        approved.after_successful_deployment("production", now)


def test_archiving_is_blocked_while_the_version_is_live(now: datetime) -> None:
    approved = make_version(now).mark_validated(now).mark_approved(now)

    with pytest.raises(PolicyViolation):
        approved.archive(["production"], now)

    assert approved.archive([], now).stage is Stage.ARCHIVED
