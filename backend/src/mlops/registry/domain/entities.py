"""Registry entities. Immutable values: every change returns a new instance.

They are plain dataclasses on purpose. The domain imports no framework; the API layer has its own
Pydantic schemas and the infrastructure layer maps these to and from database rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Self

from mlops.kernel.errors import InvalidTransition, ValidationFailure
from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.registry.domain import lifecycle

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_URI = re.compile(r"^(s3|gs|abfss|file|https)://\S+$")


def _require_text(field_name: str, value: str) -> str:
    if not value or not value.strip():
        raise ValidationFailure(f"{field_name} must not be blank", field=field_name)
    return value.strip()


@dataclass(frozen=True, slots=True, order=False)
class SemVer:
    """Semantic version. Ordering follows semver precedence; build metadata is ignored."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Self:
        match = _SEMVER.match(text)
        if match is None:
            raise ValidationFailure(
                f"{text!r} is not a semantic version such as 1.4.2", field="version", value=text
            )
        major, minor, patch, pre, build = match.groups()
        return cls(
            int(major),
            int(minor),
            int(patch),
            tuple(pre.split(".")) if pre else (),
            tuple(build.split(".")) if build else (),
        )

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    def _key(self) -> tuple[Any, ...]:
        # A release sorts after its own prereleases; numeric identifiers sort before text ones.
        pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.prerelease)
        return (self.major, self.minor, self.patch, 1 if not self.prerelease else 0, pre)

    def __lt__(self, other: SemVer) -> bool:
        return self._key() < other._key()


@dataclass(frozen=True, slots=True)
class Model:
    id: str
    name: str
    owner: str
    framework: str
    created_at: datetime
    updated_at: datetime
    tags: tuple[str, ...] = ()
    tenant_id: str = "default"

    @classmethod
    def register(
        cls,
        *,
        id: str,
        name: str,
        owner: str,
        framework: str,
        now: datetime,
        tags: tuple[str, ...] = (),
        tenant_id: str = "default",
    ) -> Self:
        if not _SLUG.match(id):
            raise ValidationFailure(
                "id must be a lowercase slug of 2 to 63 characters (a-z, 0-9, hyphen)",
                field="id",
                value=id,
            )
        return cls(
            id=id,
            name=_require_text("name", name),
            owner=_require_text("owner", owner),
            framework=_require_text("framework", framework),
            created_at=now,
            updated_at=now,
            tags=tags,
            tenant_id=tenant_id,
        )


@dataclass(frozen=True, slots=True)
class ModelVersion:
    id: str
    model_id: str
    version: str
    algorithm: str
    artifact_uri: str
    artifact_digest: str
    training_data_ref: str
    registered_by: str
    created_at: datetime
    updated_at: datetime
    stage: Stage = Stage.DRAFT
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    runtime_type: str = "generic"
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    tenant_id: str = "default"
    # Managed by the persistence layer as an optimistic-lock counter (compare-and-swap).
    row_version: int = 0

    @classmethod
    def register(
        cls,
        *,
        id: str,
        model_id: str,
        version: str,
        algorithm: str,
        artifact_uri: str,
        artifact_digest: str,
        training_data_ref: str,
        registered_by: str,
        now: datetime,
        runtime_type: str = "generic",
        metadata: dict[str, Any] | None = None,
        tags: tuple[str, ...] = (),
        tenant_id: str = "default",
    ) -> Self:
        SemVer.parse(version)
        if not _URI.match(artifact_uri):
            raise ValidationFailure(
                "artifact_uri must use one of the schemes s3, gs, abfss, file or https",
                field="artifact_uri",
                value=artifact_uri,
            )
        if not _DIGEST.match(artifact_digest):
            raise ValidationFailure(
                "artifact_digest must be 'sha256:' followed by 64 lowercase hex characters",
                field="artifact_digest",
                value=artifact_digest,
            )
        return cls(
            id=id,
            model_id=model_id,
            version=version,
            algorithm=_require_text("algorithm", algorithm),
            artifact_uri=artifact_uri,
            artifact_digest=artifact_digest,
            training_data_ref=_require_text("training_data_ref", training_data_ref),
            registered_by=_require_text("registered_by", registered_by),
            created_at=now,
            updated_at=now,
            runtime_type=runtime_type,
            metadata=dict(metadata or {}),
            tags=tags,
            tenant_id=tenant_id,
        )

    # -- lifecycle -------------------------------------------------------------------------

    def mark_validated(self, now: datetime) -> Self:
        return self._moved_to(Stage.VALIDATED, now)

    def mark_approved(self, now: datetime) -> Self:
        return self._moved_to(Stage.APPROVED, now, approval_status=ApprovalStatus.APPROVED)

    def mark_rejected(self, now: datetime) -> Self:
        """A rejection keeps the version VALIDATED so it can be fixed and resubmitted."""
        if self.stage is not Stage.VALIDATED:
            raise InvalidTransition(
                "only a VALIDATED version can be rejected",
                current=self.stage.value,
                required_stage=Stage.VALIDATED.value,
            )
        return replace(self, approval_status=ApprovalStatus.REJECTED, updated_at=now)

    def after_successful_deployment(self, environment: str, now: datetime) -> Self:
        new_stage = lifecycle.stage_after_successful_deployment(self.stage, environment)
        return replace(self, stage=new_stage, updated_at=now)

    def archive(self, live_environments: list[str], now: datetime) -> Self:
        new_stage = lifecycle.archive(self.stage, live_environments)
        return replace(self, stage=new_stage, updated_at=now)

    def _moved_to(self, target: Stage, now: datetime, **changes: Any) -> Self:
        return replace(
            self, stage=lifecycle.transition(self.stage, target), updated_at=now, **changes
        )
