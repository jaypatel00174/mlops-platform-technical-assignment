"""A ModelRuntime that pretends to deploy, with failures you can ask for.

There is no real serving platform behind it. It exists so the whole asynchronous flow (retry,
backoff, failure classes, rollback) can be exercised end to end and in tests. Behaviour is chosen
by a marker in the artifact URI, so a demo or a test can pick the outcome per version:

    s3://models/pump/2.0.0                      deploys fine
    s3://models/pump/2.0.0?sim=flaky:2          first 2 attempts fail with a timeout, then succeed
    s3://models/pump/2.0.0?sim=timeout          always times out        (TRANSIENT)
    s3://models/pump/2.0.0?sim=unavailable      always unavailable      (TRANSIENT)
    s3://models/pump/2.0.0?sim=reject           runtime refuses it      (PERMANENT_RUNTIME)
    s3://models/pump/2.0.0?sim=missing          artifact check fails    (VALIDATION)

A real adapter replaces this class; nothing else changes (ADR-001).
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict

from mlops.deployment.domain.runtime import RuntimeTarget
from mlops.kernel.errors import RuntimeRejected, RuntimeTimeout, RuntimeUnavailable

DEFAULT_SUPPORTED_TYPES = frozenset({"generic", "sklearn", "pytorch", "onnx", "tensorflow"})
_MARKER = re.compile(r"[?&]sim=([a-z]+)(?::(\d+))?")


class SimulatedRuntime:
    def __init__(self, supported_types: frozenset[str] = DEFAULT_SUPPORTED_TYPES) -> None:
        self._supported = supported_types
        self._calls: defaultdict[tuple[str, str, str], int] = defaultdict(int)
        self._lock = threading.Lock()
        self.deployed: dict[tuple[str, str], str] = {}  # (model, environment) -> version running

    def supports(self, runtime_type: str) -> bool:
        return runtime_type in self._supported

    def verify_artifact(self, artifact_uri: str, artifact_digest: str) -> bool:
        match = _MARKER.search(artifact_uri)
        return not (match and match.group(1) == "missing")

    def deploy(self, target: RuntimeTarget) -> str:
        match = _MARKER.search(target.artifact_uri)
        behaviour = match.group(1) if match else "ok"
        count = int(match.group(2)) if match and match.group(2) else 0

        if behaviour == "timeout":
            raise RuntimeTimeout("the runtime did not answer in time (simulated)")
        if behaviour == "unavailable":
            raise RuntimeUnavailable("the runtime is unavailable (simulated)")
        if behaviour == "reject":
            raise RuntimeRejected("the runtime refuses this model (simulated)")
        if behaviour == "flaky":
            key = (target.model_id, target.version, target.environment)
            with self._lock:
                self._calls[key] += 1
                calls = self._calls[key]
            if calls <= count:
                raise RuntimeTimeout(f"simulated timeout {calls} of {count}")

        with self._lock:
            self.deployed[(target.model_id, target.environment)] = target.version
        return f"sim://{target.environment}/{target.model_id}/{target.version}"
