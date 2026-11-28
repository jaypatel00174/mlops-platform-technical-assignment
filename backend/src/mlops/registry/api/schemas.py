"""Request and response bodies of the registry API.

Pydantic checks types and sizes here. The business rules (slug format, semantic version, digest
format, lifecycle) live in the domain, which raises errors that come back in the same envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.registry.application.services import ModelSummary
from mlops.registry.domain.entities import ModelVersion

Tag = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCreate(_Request):
    id: str = Field(max_length=63, examples=["pump-failure-predictor"])
    name: str = Field(max_length=255, examples=["Pump Failure Predictor"])
    owner: str = Field(max_length=255, examples=["Reliability AI Team"])
    framework: str = Field(max_length=255, examples=["scikit-learn"])
    tags: list[Tag] = Field(default_factory=list, max_length=32)


class ModelOut(BaseModel):
    id: str
    name: str
    owner: str
    framework: str
    tags: list[str]
    version_count: int
    latest_version: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_summary(cls, summary: ModelSummary) -> Self:
        model = summary.model
        return cls(
            id=model.id,
            name=model.name,
            owner=model.owner,
            framework=model.framework,
            tags=list(model.tags),
            version_count=summary.version_count,
            latest_version=summary.latest_version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class ModelListOut(BaseModel):
    items: list[ModelOut]
    next_cursor: str | None = Field(
        description="Pass as ?cursor= to get the next page. Null on the last page."
    )


class VersionCreate(_Request):
    version: str = Field(max_length=64, examples=["2.0.0"])
    algorithm: str = Field(max_length=255, examples=["gradient-boosting"])
    artifact_uri: str = Field(max_length=1024, examples=["s3://models/pump/2.0.0"])
    artifact_digest: str = Field(
        max_length=71, examples=["sha256:" + "a" * 64], description="sha256 of the artifact"
    )
    training_data_ref: str = Field(max_length=1024, examples=["s3://datasets/pump/2026-06"])
    runtime_type: str = Field(default="generic", max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[Tag] = Field(default_factory=list, max_length=32)


class VersionOut(BaseModel):
    id: str
    model_id: str
    version: str
    algorithm: str
    artifact_uri: str
    artifact_digest: str
    training_data_ref: str
    registered_by: str
    stage: Stage
    approval_status: ApprovalStatus
    runtime_type: str
    metadata: dict[str, Any]
    tags: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, version: ModelVersion) -> Self:
        return cls(
            id=version.id,
            model_id=version.model_id,
            version=version.version,
            algorithm=version.algorithm,
            artifact_uri=version.artifact_uri,
            artifact_digest=version.artifact_digest,
            training_data_ref=version.training_data_ref,
            registered_by=version.registered_by,
            stage=version.stage,
            approval_status=version.approval_status,
            runtime_type=version.runtime_type,
            metadata=dict(version.metadata),
            tags=list(version.tags),
            created_at=version.created_at,
            updated_at=version.updated_at,
        )


class VersionListOut(BaseModel):
    items: list[VersionOut]


class DecisionRequest(_Request):
    comment: str | None = Field(
        default=None, max_length=2000, description="Required when rejecting a version"
    )
