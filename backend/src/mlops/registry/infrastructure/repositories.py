"""SQLAlchemy repositories for the registry module.

Every read is scoped to the repository's tenant. Writes translate database integrity failures into
typed domain errors, and updates use the row's optimistic-lock counter (compare-and-swap).
"""

from __future__ import annotations

from collections.abc import Collection

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from mlops.kernel.errors import AlreadyExists, NotFound, StaleVersion
from mlops.kernel.vocabulary import ApprovalStatus, Stage
from mlops.platform.db import IntegrityKind, flush_translating
from mlops.registry.domain.entities import Model, ModelVersion, SemVer
from mlops.registry.infrastructure.orm import ModelRow, ModelVersionRow


def _model_from_row(row: ModelRow) -> Model:
    return Model(
        id=row.id,
        name=row.name,
        owner=row.owner,
        framework=row.framework,
        created_at=row.created_at,
        updated_at=row.updated_at,
        tags=tuple(row.tags),
        tenant_id=row.tenant_id,
    )


def _version_from_row(row: ModelVersionRow) -> ModelVersion:
    return ModelVersion(
        id=row.id,
        model_id=row.model_id,
        version=row.version,
        algorithm=row.algorithm,
        artifact_uri=row.artifact_uri,
        artifact_digest=row.artifact_digest,
        training_data_ref=row.training_data_ref,
        registered_by=row.registered_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        stage=Stage(row.stage),
        approval_status=ApprovalStatus(row.approval_status),
        runtime_type=row.runtime_type,
        metadata=dict(row.extra_metadata),
        tags=tuple(row.tags),
        tenant_id=row.tenant_id,
        row_version=row.row_version,
    )


class SqlModelRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add(self, model: Model) -> None:
        self._session.add(
            ModelRow(
                id=model.id,
                tenant_id=model.tenant_id,
                name=model.name,
                owner=model.owner,
                framework=model.framework,
                tags=list(model.tags),
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists(f"model {model.id!r} already exists", model_id=model.id)
            return None

        flush_translating(self._session, translate)

    def get(self, model_id: str) -> Model | None:
        row = self._session.scalar(
            select(ModelRow).where(ModelRow.id == model_id, ModelRow.tenant_id == self._tenant_id)
        )
        return _model_from_row(row) if row else None

    def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
        framework: str | None = None,
        after_id: str | None = None,
    ) -> list[Model]:
        """Models in id order. `after_id` is the keyset cursor; `search` matches id or name."""
        statement = select(ModelRow).where(ModelRow.tenant_id == self._tenant_id)
        if search:
            needle = search.lower()
            statement = statement.where(
                or_(
                    func.lower(ModelRow.id).contains(needle, autoescape=True),
                    func.lower(ModelRow.name).contains(needle, autoescape=True),
                )
            )
        if framework:
            statement = statement.where(func.lower(ModelRow.framework) == framework.lower())
        if after_id is not None:
            statement = statement.where(ModelRow.id > after_id)
        rows = self._session.scalars(statement.order_by(ModelRow.id).limit(limit).offset(offset))
        return [_model_from_row(row) for row in rows]


class SqlModelVersionRepository:
    def __init__(self, session: Session, tenant_id: str = "default") -> None:
        self._session = session
        self._tenant_id = tenant_id

    def add(self, version: ModelVersion) -> None:
        self._session.add(
            ModelVersionRow(
                id=version.id,
                tenant_id=version.tenant_id,
                model_id=version.model_id,
                version=version.version,
                algorithm=version.algorithm,
                artifact_uri=version.artifact_uri,
                artifact_digest=version.artifact_digest,
                training_data_ref=version.training_data_ref,
                registered_by=version.registered_by,
                stage=version.stage.value,
                approval_status=version.approval_status.value,
                runtime_type=version.runtime_type,
                extra_metadata=dict(version.metadata),
                tags=list(version.tags),
                created_at=version.created_at,
                updated_at=version.updated_at,
            )
        )

        def translate(kind: IntegrityKind, _error: IntegrityError) -> Exception | None:
            if kind == "unique":
                return AlreadyExists(
                    f"version {version.version} of {version.model_id!r} already exists",
                    model_id=version.model_id,
                    version=version.version,
                )
            if kind == "foreign_key":
                return NotFound(
                    f"model {version.model_id!r} does not exist", model_id=version.model_id
                )
            return None

        flush_translating(self._session, translate)

    def get(self, model_id: str, version: str) -> ModelVersion | None:
        row = self._session.scalar(
            select(ModelVersionRow).where(
                ModelVersionRow.tenant_id == self._tenant_id,
                ModelVersionRow.model_id == model_id,
                ModelVersionRow.version == version,
            )
        )
        return _version_from_row(row) if row else None

    def stats_for(self, model_ids: Collection[str]) -> dict[str, tuple[int, str | None]]:
        """Version count and latest semantic version per model, in one query."""
        if not model_ids:
            return {}
        rows = self._session.execute(
            select(ModelVersionRow.model_id, ModelVersionRow.version).where(
                ModelVersionRow.tenant_id == self._tenant_id,
                ModelVersionRow.model_id.in_(list(model_ids)),
            )
        )
        found: dict[str, list[str]] = {}
        for model_id, version in rows:
            found.setdefault(model_id, []).append(version)
        return {
            model_id: (len(versions), max(versions, key=SemVer.parse))
            for model_id, versions in found.items()
        }

    def list_for_model(self, model_id: str) -> list[ModelVersion]:
        rows = self._session.scalars(
            select(ModelVersionRow).where(
                ModelVersionRow.tenant_id == self._tenant_id, ModelVersionRow.model_id == model_id
            )
        )
        versions = [_version_from_row(row) for row in rows]
        return sorted(versions, key=lambda v: SemVer.parse(v.version))

    def save(self, version: ModelVersion) -> ModelVersion:
        """Persist changes. Fails with StaleVersion if someone else changed the row first."""
        row = self._session.scalar(
            select(ModelVersionRow).where(
                ModelVersionRow.id == version.id, ModelVersionRow.tenant_id == self._tenant_id
            )
        )
        if row is None:
            raise NotFound(f"version {version.id!r} does not exist", version_id=version.id)
        if row.row_version != version.row_version:
            raise StaleVersion(
                "the version was changed by someone else; reload it and try again",
                version_id=version.id,
                expected=version.row_version,
                actual=row.row_version,
            )
        row.stage = version.stage.value
        row.approval_status = version.approval_status.value
        row.runtime_type = version.runtime_type
        row.extra_metadata = dict(version.metadata)
        row.tags = list(version.tags)
        row.updated_at = version.updated_at
        try:
            self._session.flush()
        except StaleDataError as error:
            self._session.rollback()
            raise StaleVersion(
                "the version was changed by someone else; reload it and try again",
                version_id=version.id,
            ) from error
        return _version_from_row(row)
