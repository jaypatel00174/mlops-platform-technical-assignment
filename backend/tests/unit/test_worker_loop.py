"""The polling loop: it keeps going after an error, backs off when idle, and stops on request."""

from __future__ import annotations

import threading
from typing import Any

from mlops.worker import run_forever


class FakeWorker:
    def __init__(self, results: list[Any], stop: threading.Event) -> None:
        self._results = results
        self._stop = stop
        self.calls = 0

    def run_once(self) -> int:
        self.calls += 1
        result = self._results.pop(0) if self._results else 0
        if not self._results:
            self._stop.set()
        if isinstance(result, Exception):
            raise result
        return int(result)


class FakeReconciler:
    def __init__(self) -> None:
        self.tenants: list[str] = []

    def reconcile(self, tenant_id: str) -> None:
        self.tenants.append(tenant_id)


class RecordingStop(threading.Event):
    """An Event that records how long the loop asked to wait instead of really waiting."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout or 0.0)
        return self.is_set()


def run(results: list[Any], *, ticks: list[float] | None = None) -> tuple[Any, Any, RecordingStop]:
    stop = RecordingStop()
    worker = FakeWorker(results, stop)
    reconciler = FakeReconciler()
    clock = iter(ticks or [0.0] * 50)
    run_forever(
        worker,  # type: ignore[arg-type]
        reconciler,  # type: ignore[arg-type]
        tenants=["default", "other"],
        poll_seconds=2.0,
        reconcile_interval_seconds=30.0,
        stop=stop,
        monotonic=lambda: next(clock),
    )
    return worker, reconciler, stop


def test_the_loop_sleeps_only_when_there_was_nothing_to_do() -> None:
    _, _, stop = run([3, 0])

    assert stop.waits == [2.0]  # busy pass: no sleep; idle pass: one poll interval


def test_an_error_in_one_pass_does_not_stop_the_loop() -> None:
    worker, _, _ = run([RuntimeError("database blip"), 1])

    assert worker.calls == 2


def test_every_tenant_is_reconciled_on_the_first_pass() -> None:
    _, reconciler, _ = run([0])

    assert reconciler.tenants == ["default", "other"]


def test_reconciling_waits_for_its_own_interval() -> None:
    # Pass 1 reconciles (t=0, next due at 30). Pass 2 at t=10 does not. Pass 3 at t=31 does.
    _, reconciler, _ = run([0, 0, 0], ticks=[0.0, 0.0, 0.0, 10.0, 31.0, 31.0, 31.0, 31.0])

    assert reconciler.tenants == ["default", "other", "default", "other"]
