from __future__ import annotations

import pytest

from mlops.deployment.domain.runtime import RuntimeTarget
from mlops.deployment.infrastructure.simulated_runtime import SimulatedRuntime
from mlops.kernel.errors import RuntimeRejected, RuntimeTimeout, RuntimeUnavailable


def target(uri: str = "s3://models/pump/2.0.0", version: str = "2.0.0") -> RuntimeTarget:
    return RuntimeTarget(
        model_id="pump",
        version=version,
        environment="staging",
        artifact_uri=uri,
        artifact_digest="sha256:" + "a" * 64,
        runtime_type="generic",
    )


def test_a_plain_artifact_deploys_and_is_remembered() -> None:
    runtime = SimulatedRuntime()

    ref = runtime.deploy(target())

    assert ref == "sim://staging/pump/2.0.0"
    assert runtime.deployed == {("pump", "staging"): "2.0.0"}


def test_deploying_the_same_target_twice_leaves_one_running_copy() -> None:
    runtime = SimulatedRuntime()

    first = runtime.deploy(target())
    second = runtime.deploy(target())

    assert first == second
    assert runtime.deployed == {("pump", "staging"): "2.0.0"}


@pytest.mark.parametrize(
    ("marker", "error"),
    [
        ("timeout", RuntimeTimeout),
        ("unavailable", RuntimeUnavailable),
        ("reject", RuntimeRejected),
    ],
)
def test_markers_in_the_artifact_uri_choose_the_failure(
    marker: str, error: type[Exception]
) -> None:
    runtime = SimulatedRuntime()

    with pytest.raises(error):
        runtime.deploy(target(f"s3://models/pump/2.0.0?sim={marker}"))

    assert runtime.deployed == {}


def test_a_flaky_artifact_fails_the_requested_number_of_times_then_works() -> None:
    runtime = SimulatedRuntime()
    flaky = target("s3://models/pump/2.0.0?sim=flaky:2")

    with pytest.raises(RuntimeTimeout):
        runtime.deploy(flaky)
    with pytest.raises(RuntimeTimeout):
        runtime.deploy(flaky)

    assert runtime.deploy(flaky) == "sim://staging/pump/2.0.0"


def test_flakiness_is_counted_per_version() -> None:
    runtime = SimulatedRuntime()

    with pytest.raises(RuntimeTimeout):
        runtime.deploy(target("s3://m/a?sim=flaky:1", version="1.0.0"))

    with pytest.raises(RuntimeTimeout):  # a different version has its own budget
        runtime.deploy(target("s3://m/b?sim=flaky:1", version="2.0.0"))


def test_artifact_verification_and_runtime_support_can_be_refused() -> None:
    runtime = SimulatedRuntime()

    assert runtime.verify_artifact("s3://models/pump/2.0.0", "sha256:x")
    assert not runtime.verify_artifact("s3://models/pump/2.0.0?sim=missing", "sha256:x")
    assert runtime.supports("generic")
    assert not runtime.supports("legacy-engine")
