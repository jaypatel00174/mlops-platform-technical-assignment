"""What the registry application layer needs from storage. SQL repositories satisfy these
structurally; tests and other adapters can too. (application may not import infrastructure.)"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Protocol

from mlops.registry.domain.entities import Model, ModelVersion

# (model_id, version) -> environments where that version is currently live
LiveEnvironments = Callable[[str, str], list[str]]


class ModelRepository(Protocol):
    def add(self, model: Model) -> None: ...

    def get(self, model_id: str) -> Model | None: ...

    def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
        framework: str | None = None,
        after_id: str | None = None,
    ) -> list[Model]: ...


class ModelVersionRepository(Protocol):
    def add(self, version: ModelVersion) -> None: ...

    def get(self, model_id: str, version: str) -> ModelVersion | None: ...

    def list_for_model(self, model_id: str) -> list[ModelVersion]: ...

    def save(self, version: ModelVersion) -> ModelVersion: ...

    def stats_for(self, model_ids: Collection[str]) -> dict[str, tuple[int, str | None]]:
        """(version count, latest semantic version) per model id."""
        ...
