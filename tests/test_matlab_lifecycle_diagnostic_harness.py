from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tools.matlab_lifecycle.diagnostic_harness import (
    JsonlEventWriter,
    diagnostic_exit_code,
    launch_child,
    monitor_child,
    record_call,
    record_child_exception,
    record_startup_state,
    wait_for_disposer,
)


class FakeFuture:
    def __init__(self, *, pending: bool = True, cancel_error: BaseException | None = None) -> None:
        self.pending = pending
        self.cancel_error = cancel_error
        self.cancel_calls = 0

    def done(self) -> bool:
        return not self.pending

    def cancel(self) -> bool:
        self.cancel_calls += 1
        if self.cancel_error is not None:
            raise self.cancel_error
        return False

    def result(self) -> FakeEngine:
        return FakeEngine()


class FakeEngine:
    def __init__(self, quit_error: BaseException | None = None) -> None:
        self.quit_error = quit_error
        self.quit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1
        if self.quit_error is not None:
            raise self.quit_error


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_pending_startup_and_disposal_event_keyword_are_logged(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    future = FakeFuture()
    assert record_startup_state(writer, "STARTING", future) is True

    class FakeWorker:
        name = "MatlabEngineStartupDisposal"
        daemon = True
        alive = True

        def is_alive(self) -> bool:
            return self.alive

    worker = FakeWorker()
    assert writer.emit("disposal_thread_observed", worker_name=worker.name, daemon=worker.daemon)
    assert writer.emit("disposal_thread_state", alive=worker.is_alive())
    assert writer.close() == ()

    rows = read_jsonl(path)
    assert rows[0]["event"] == "startup_state_observed"
    assert rows[0]["manager_state"] == "STARTING"
    assert rows[0]["future_pending"] is True
    assert rows[1]["worker_name"] == "MatlabEngineStartupDisposal"


def test_event_writer_serializes_concurrent_events_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    workers = 8
    per_worker = 20
    barrier = threading.Barrier(workers)

    def write_events(worker_id: int) -> None:
        barrier.wait()
        for event_id in range(per_worker):
            assert writer.emit("concurrent_event", worker=worker_id, event_id=event_id)

    threads = [threading.Thread(target=write_events, args=(worker_id,)) for worker_id in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert writer.close() == ()

    rows = read_jsonl(path)
    assert len(rows) == workers * per_worker
    assert len({(row["worker"], row["event_id"]) for row in rows}) == workers * per_worker


def test_json_serialization_failure_is_recorded_and_does_not_hide_later_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    assert writer.emit("bad_payload", future=FakeFuture()) is False
    assert writer.emit("after_serialization_failure", preserved=True)
    assert writer.close() == ()

    rows = read_jsonl(path)
    assert rows[0]["event"] == "event_serialization_failure"
    assert rows[0]["attempted_event"] == "bad_payload"
    assert "future" in rows[0]["field_names"]
    assert rows[1]["event"] == "after_serialization_failure"


def test_writer_io_failure_is_returned_and_rejects_unwritten_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.mkdir()
    writer = JsonlEventWriter(path)

    errors = writer.close()

    assert errors
    assert "IsADirectoryError" in errors[0] or "PermissionError" in errors[0]
    assert writer.emit("cannot_be_written", evidence="must not be reported as accepted") is False


def test_unserializable_disposal_observation_is_recorded_instead_of_raising(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    assert writer.emit("disposal_thread_observed", worker_name=object()) is False
    assert writer.close() == ()

    row = read_jsonl(path)[0]
    assert row["event"] == "event_serialization_failure"
    assert row["attempted_event"] == "disposal_thread_observed"
    assert "worker_name" in row["field_names"]


def test_cancel_exception_is_logged_and_reraised(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    future = FakeFuture(cancel_error=RuntimeError("cancel exploded"))
    with pytest.raises(RuntimeError, match="cancel exploded"):
        record_call(writer, "future_cancel", future.cancel)
    assert writer.close() == ()

    rows = read_jsonl(path)
    assert [row["event"] for row in rows] == ["future_cancel_call", "future_cancel_exception"]
    assert rows[-1]["exception"] == "cancel exploded"
    assert future.cancel_calls == 1


def test_disposal_result_and_fake_engine_quit_are_observable(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    future = FakeFuture()
    engine = record_call(writer, "future_result", future.result)
    assert isinstance(engine, FakeEngine)
    with pytest.raises(ValueError, match="quit exploded"):
        record_call(writer, "engine_quit", FakeEngine(ValueError("quit exploded")).quit)
    assert writer.close() == ()

    rows = read_jsonl(path)
    assert "future_result_return" in [row["event"] for row in rows]
    assert "engine_quit_exception" in [row["event"] for row in rows]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def wait(self, duration: float) -> None:
        self.now += duration


class FakeProcess:
    pid = 731

    def __init__(self, return_codes: list[int | None]) -> None:
        self.return_codes = iter(return_codes)
        self.last: int | None = None

    def poll(self) -> int | None:
        try:
            self.last = next(self.return_codes)
        except StopIteration:
            pass
        return self.last


def test_parent_continues_monitoring_after_unexpected_child_failure(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.jsonl"
    snapshots_path = tmp_path / "process_snapshots.jsonl"
    writer = JsonlEventWriter(path)
    clock = FakeClock()
    process = FakeProcess([None, 7])
    snapshot_calls = 0

    def snapshot() -> list[dict[str, Any]]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return [{"PID": 999, "Name": "MATLAB.exe"}]

    result = monitor_child(
        process,
        writer=writer,
        snapshot=snapshot,
        snapshot_path=snapshots_path,
        shutdown_requested=lambda: None,
        post_exit_observation_s=0.3,
        interval_s=0.1,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )
    assert writer.close() == ()

    rows = read_jsonl(path)
    snapshots = read_jsonl(snapshots_path)
    assert result.return_code == 7
    assert any(row["event"] == "child_failed" and row["return_code"] == 7 for row in rows)
    assert result.snapshots_after_exit == 3
    assert all(row["phase"] == "post_exit" for row in snapshots if row["phase"] == "post_exit")
    assert snapshot_calls >= 4


def test_parent_preserves_raw_child_stdout_and_stderr_after_nonzero_exit(tmp_path: Path) -> None:
    stdout_path = tmp_path / "child.stdout.txt"
    stderr_path = tmp_path / "child.stderr.txt"
    code = "import sys; print('raw-out'); print('raw-error', file=sys.stderr); raise SystemExit(9)"
    process = launch_child(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    assert process.wait(timeout=10) == 9
    assert stdout_path.read_text(encoding="utf-8").strip() == "raw-out"
    assert stderr_path.read_text(encoding="utf-8").strip() == "raw-error"


@pytest.mark.parametrize(
    ("child_return_code", "snapshot_errors", "writer_errors", "expected"),
    [
        (0, (), (), 0),
        (7, (), (), 1),
        (0, ("snapshot denied",), (), 1),
        (0, (), ("writer failed",), 1),
        (None, (), (), 1),
    ],
)
def test_supervisor_exit_status_reports_child_and_evidence_failures(
    child_return_code: int | None,
    snapshot_errors: tuple[str, ...],
    writer_errors: tuple[str, ...],
    expected: int,
) -> None:
    assert (
        diagnostic_exit_code(
            child_return_code,
            snapshot_errors=snapshot_errors,
            writer_errors=writer_errors,
        )
        == expected
    )


def test_unexpected_child_exception_is_logged_and_returns_nonzero(tmp_path: Path) -> None:
    writer = JsonlEventWriter(tmp_path / "child.jsonl")
    exception = RuntimeError("unexpected child failure")
    exit_code = record_child_exception(writer, exception, "traceback evidence")
    assert exit_code != 0
    assert writer.close() == ()

    row = read_jsonl(tmp_path / "child.jsonl")[0]
    assert row["event"] == "child_exception"
    assert row["exception"] == "unexpected child failure"
    assert row["traceback"] == "traceback evidence"


def test_child_failure_waits_for_existing_disposer_before_exit(tmp_path: Path) -> None:
    path = tmp_path / "child.jsonl"
    writer = JsonlEventWriter(path)
    exit_code = record_child_exception(writer, RuntimeError("instrumentation failed"), "traceback")

    class FinishedWorker:
        name = "MatlabEngineStartupDisposal"

        def __init__(self) -> None:
            self.alive = True
            self.join_timeout: float | None = None

        def join(self, timeout: float) -> None:
            self.join_timeout = timeout
            self.alive = False

        def is_alive(self) -> bool:
            return self.alive

    worker = FinishedWorker()
    still_alive = wait_for_disposer(
        writer,
        worker,
        shutdown_started_ns=0,
        deadline_s=150,
        monotonic_ns=lambda: 1_000_000_000,
    )
    assert still_alive is False
    assert exit_code == 1
    assert worker.join_timeout == pytest.approx(149)
    assert writer.close() == ()
    rows = read_jsonl(path)
    assert [row["event"] for row in rows] == [
        "child_exception",
        "disposal_wait_started",
        "disposal_wait_complete",
    ]
    assert rows[-1]["alive"] is False


def test_shutdown_deadline_reports_child_without_terminating_it(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.jsonl"
    writer = JsonlEventWriter(path)
    clock = FakeClock()
    process = FakeProcess([None, None, None])
    result = monitor_child(
        process,
        writer=writer,
        snapshot=list,
        snapshot_path=tmp_path / "snapshots.jsonl",
        shutdown_requested=lambda: 0.0,
        max_after_shutdown_s=0.2,
        post_exit_observation_s=0.1,
        interval_s=0.1,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )
    assert result.deadline_reached is True
    assert result.return_code is None
    assert process.poll() is None
    assert writer.close() == ()
    assert any(row["event"] == "child_deadline_reached_left_running" for row in read_jsonl(path))
