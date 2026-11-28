"""Registry use cases. Authorization happens at the API boundary; the business rules live in the
domain. Every state change writes its audit entry in the same transaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mlops.kernel.concurrency import ensure_unchanged
from mlops.kernel.errors import NotFound
from mlops.kernel.ports import AuditRecorder, Clock
from mlops.registry.application.ports import (
    LiveEnvironments,
    ModelRepository,
    ModelVersionRepository,
)
from mlops.registry.domain.entities import Model, ModelVersion

# Other modules reach these types through the application layer, never through registry.domain.
__all__ = ["Model", "ModelPage", "ModelSummary", "ModelVersion", "RegistryService"]


@dataclass(frozen=True, slots=True)
class ModelSummary:
    model: Model
    version_count: int
    latest_version: str | None


@dataclass(frozen=True, slots=True)
class ModelPage:
    items: list[ModelSummary]
    next_after_id: str | None


def _version_state(version: ModelVersion) -> dict[str, Any]:
    return {"stage": version.stage.value, "approval_status": version.approval_status.value}


class RegistryService:
    def __init__(
        self,
        *,
        models: ModelRepository,
        versions: ModelVersionRepository,
        live_environments: LiveEnvironments,
        audit: AuditRecorder,
        clock: Clock,
        tenant_id: str,
    ) -> None:
        self._models = models
        self._versions = versions
        self._live_environments = live_environments
        self._audit = audit
        self._clock = clock
        self._tenant_id = tenant_id

    # -- models ----------------------------------------------------------------------------

    def register_model(
        self,
        *,
        actor: str,
        id: str,
        name: str,
        owner: str,
        framework: str,
        tags: Sequence[str] = (),
    ) -> Model:
        model = Model.register(
            id=id,
            name=name,
            owner=owner,
            framework=framework,
            now=self._clock.now(),
            tags=tuple(tags),
            tenant_id=self._tenant_id,
        )
        self._models.add(model)
        self._audit.record(
            actor=actor,
            action="model.registered",
            entity_type="model",
            entity_id=model.id,
            after={"name": model.name, "owner": model.owner, "framework": model.framework},
        )
        return model

    def list_models(
        self,
        *,
        search: str | None = None,
        framework: str | None = None,
        limit: int = 50,
        after_id: str | None = None,
    ) -> ModelPage:
        # Fetch one extra row to know whether another page exists without a second query.
        rows = self._models.list_all(
            limit=limit + 1, search=search, framework=framework, after_id=after_id
        )
        page = rows[:limit]
        stats = self._versions.stats_for([model.id for model in page])
        items = [ModelSummary(model, *stats.get(model.id, (0, None))) for model in page]
        return ModelPage(items, page[-1].id if len(rows) > limit else None)

    def get_model(self, model_id: str) -> ModelSummary:
        model = self._require_model(model_id)
        count, latest = self._versions.stats_for([model.id]).get(model.id, (0, None))
        return ModelSummary(model, count, latest)

    # -- versions --------------------------------------------------------------------------

    def register_version(
        self,
        *,
        actor: str,
        model_id: str,
        version: str,
        algorithm: str,
        artifact_uri: str,
        artifact_digest: str,
        training_data_ref: str,
        runtime_type: str = "generic",
        metadata: Mapping[str, Any] | None = None,
        tags: Sequence[str] = (),
    ) -> ModelVersion:
        self._require_model(model_id)
        created = ModelVersion.register(
            id=f"{model_id}@{version}",
            model_id=model_id,
            version=version,
            algorithm=algorithm,
            artifact_uri=artifact_uri,
            artifact_digest=artifact_digest,
            training_data_ref=training_data_ref,
            registered_by=actor,
            now=self._clock.now(),
            runtime_type=runtime_type,
            metadata=dict(metadata or {}),
            tags=tuple(tags),
            tenant_id=self._tenant_id,
        )
        self._versions.add(created)
        self._audit.record(
            actor=actor,
            action="version.registered",
            entity_type="model_version",
            entity_id=created.id,
            after={**_version_state(created), "version": created.version},
        )
        # Re-read so the caller sees the stored row_version (the ETag source).
        stored = self._versions.get(model_id, version)
        return stored if stored is not None else created

    def list_versions(self, model_id: str) -> list[ModelVersion]:
        self._require_model(model_id)
        return self._versions.list_for_model(model_id)

    def get_version(self, model_id: str, version: str) -> ModelVersion:
        found = self._versions.get(model_id, version)
        if found is None:
            raise NotFound(
                f"version {version} of model {model_id!r} does not exist",
                model_id=model_id,
                version=version,
            )
        return found

    def validate_version(
        self, *, actor: str, model_id: str, version: str, if_match: str | None = None
    ) -> ModelVersion:
        current = self.get_version(model_id, version)
        ensure_unchanged(if_match, current.row_version)
        saved = self._versions.save(current.mark_validated(self._clock.now()))
        self._record_change("version.validated", actor, current, saved)
        return saved

    def archive_version(
        self, *, actor: str, model_id: str, version: str, if_match: str | None = None
    ) -> ModelVersion:
        current = self.get_version(model_id, version)
        ensure_unchanged(if_match, current.row_version)
        live = self._live_environments(model_id, version)
        saved = self._versions.save(current.archive(live, self._clock.now()))
        self._record_change("version.archived", actor, current, saved)
        return saved

    def apply_approval(
        self,
        *,
        model_id: str,
        version: str,
        approved: bool,
        if_match: str | None = None,
    ) -> tuple[ModelVersion, ModelVersion]:
        """Apply an approval decision that governance already validated. Returns (before, after)."""
        current = self.get_version(model_id, version)
        ensure_unchanged(if_match, current.row_version)
        now = self._clock.now()
        changed = current.mark_approved(now) if approved else current.mark_rejected(now)
        return current, self._versions.save(changed)

    def promote_after_deployment(
        self, *, actor: str, model_id: str, version: str, environment: str
    ) -> ModelVersion:
        """Advance the stage once a deployment succeeded (staging, then production).

        Idempotent: a redelivered event, or a rollback to a version that already reached
        production, leaves the stage as it is and writes no audit entry.
        """
        current = self.get_version(model_id, version)
        promoted = current.after_successful_deployment(environment, self._clock.now())
        if promoted.stage is current.stage:
            return current
        saved = self._versions.save(promoted)
        self._record_change("version.promoted", actor, current, saved)
        return saved

    # -- helpers ---------------------------------------------------------------------------

    def _require_model(self, model_id: str) -> Model:
        model = self._models.get(model_id)
        if model is None:
            raise NotFound(f"model {model_id!r} does not exist", model_id=model_id)
        return model

    def _record_change(
        self, action: str, actor: str, before: ModelVersion, after: ModelVersion
    ) -> None:
        self._audit.record(
            actor=actor,
            action=action,
            entity_type="model_version",
            entity_id=after.id,
            before=_version_state(before),
            after=_version_state(after),
        )
