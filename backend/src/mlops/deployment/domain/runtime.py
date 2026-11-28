"""What a runtime needs to know to place a model version in an environment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeTarget:
    model_id: str
    version: str
    environment: str
    artifact_uri: str
    artifact_digest: str
    runtime_type: str
