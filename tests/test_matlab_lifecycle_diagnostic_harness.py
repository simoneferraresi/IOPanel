from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from typing import Any, Self

import pytest

from tools.matlab_lifecycle.diagnostic_harness import (
    REQUIRED_SUCCESS_EVENTS,
    JsonlEventWriter,
    compare_process_inventories,
    diagnostic_exit_code,
    launch_child,
    monitor_child,
    normalize_creation_time,
    record_call,
    record_child_exception,
    record_startup_state,
    validate_child_evidence,
    wait_for_disposer,
)
from tools.matlab_lifecycle.diagnostic_harness import (
    read_jsonl as read_event_jsonl,
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


@pytest.mark.parametrize("blocked_phase", ["before_startup", "before_shutdown_event"])
def test_parent_owned_deadline_covers_child_that_never_requests_shutdown(tmp_path: Path, blocked_phase: str) -> None:
    path = tmp_path / f"{blocked_phase}.jsonl"
    snapshots_path = tmp_path / f"{blocked_phase}_snapshots.jsonl"
    writer = JsonlEventWriter(path)
    clock = FakeClock()
    process = FakeProcess([None, None, None, None])
    snapshots: list[str] = []

    def snapshot() -> list[dict[str, Any]]:
        snapshots.append("observed")
        return [{"PID": 500, "Name": "python.exe", "CreationDate": "start"}]

    result = monitor_child(
        process,
        writer=writer,
        snapshot=snapshot,
        snapshot_path=snapshots_path,
        shutdown_requested=lambda: None,
        max_before_shutdown_s=0.2,
        interval_s=0.1,
        monotonic=clock.monotonic,
        monitor_started_at=0.0,
        wait=clock.wait,
    )

    assert result.deadline_reached is True
    assert result.deadline_reason == "before_shutdown_requested"
    assert result.return_code is None
    assert len(snapshots) >= 3
    assert process.poll() is None
    assert writer.close() == ()
    assert any(row["event"] == "child_startup_deadline_reached_left_running" for row in read_jsonl(path))
    assert diagnostic_exit_code(result.return_code) == 1


def test_unexpected_child_exit_is_reported_and_observed_without_killing(tmp_path: Path) -> None:
    writer = JsonlEventWriter(tmp_path / "events.jsonl")
    clock = FakeClock()
    process = FakeProcess([None, 17])
    snapshot_calls = 0

    def snapshot() -> list[dict[str, Any]]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return []

    result = monitor_child(
        process,
        writer=writer,
        snapshot=snapshot,
        snapshot_path=tmp_path / "snapshots.jsonl",
        shutdown_requested=lambda: None,
        post_exit_observation_s=0.2,
        interval_s=0.1,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )
    assert result.return_code == 17
    assert result.snapshots_after_exit == 2
    assert snapshot_calls >= 3
    assert writer.close() == ()
    assert diagnostic_exit_code(result.return_code) == 1


def test_wmi_snapshot_failure_is_collected_until_parent_deadline(tmp_path: Path) -> None:
    writer = JsonlEventWriter(tmp_path / "events.jsonl")
    clock = FakeClock()

    def fail_snapshot() -> list[dict[str, Any]]:
        raise RuntimeError("fake WMI failure")

    result = monitor_child(
        FakeProcess([None, None, None]),
        writer=writer,
        snapshot=fail_snapshot,
        snapshot_path=tmp_path / "snapshots.jsonl",
        shutdown_requested=lambda: None,
        max_before_shutdown_s=0.1,
        interval_s=0.1,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )
    assert result.deadline_reached is True
    assert result.snapshot_errors
    assert "fake WMI failure" in result.snapshot_errors[0]
    assert writer.close() == ()
    assert diagnostic_exit_code(None, snapshot_errors=result.snapshot_errors) == 1


def test_disposer_success_block_and_failure_are_distinguished(tmp_path: Path) -> None:
    writer = JsonlEventWriter(tmp_path / "events.jsonl")

    class FakeWorker:
        name = "disposer"

        def __init__(self, *, finishes: bool = False, failure: BaseException | None = None) -> None:
            self.finishes = finishes
            self.failure = failure
            self.timeout: float | None = None

        def join(self, timeout: float) -> None:
            self.timeout = timeout
            if self.failure:
                raise self.failure

        def is_alive(self) -> bool:
            return not self.finishes

    now = 1_000_000_000
    done = FakeWorker(finishes=True)
    blocked = FakeWorker()
    failed = FakeWorker(failure=RuntimeError("join failed"))
    assert wait_for_disposer(writer, done, shutdown_started_ns=0, deadline_s=5, monotonic_ns=lambda: now) is False
    assert wait_for_disposer(writer, blocked, shutdown_started_ns=0, deadline_s=5, monotonic_ns=lambda: now) is True
    with pytest.raises(RuntimeError, match="join failed"):
        wait_for_disposer(writer, failed, shutdown_started_ns=0, deadline_s=5, monotonic_ns=lambda: now)
    assert done.timeout == blocked.timeout == failed.timeout == pytest.approx(4)
    assert writer.close() == ()
    assert "disposal_wait_exception" in [row["event"] for row in read_jsonl(tmp_path / "events.jsonl")]


def test_malformed_and_incomplete_jsonl_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "malformed.jsonl"
    path.write_bytes(b'{"event":"good"}\nnot-json\n{"event":"partial"}')
    strict = read_event_jsonl(path)
    live = read_event_jsonl(path, allow_incomplete_final_line=True)
    assert len(strict.rows) == 2
    assert len(strict.errors) == 2
    assert len(live.errors) == 2
    assert len(live.rows) == 1
    assert any("invalid JSONL record" in error for error in strict.errors)
    assert any("incomplete JSONL record" in error for error in strict.errors)
    assert validate_child_evidence(strict.rows, strict.errors)


def test_event_fallback_and_missing_critical_events_fail_validation(tmp_path: Path) -> None:
    path = tmp_path / "child.jsonl"
    writer = JsonlEventWriter(path)
    writer.emit("startup_state_observed", future=FakeFuture())
    assert writer.close() == ()
    rows = read_event_jsonl(path).rows
    assert rows[0]["event"] == "event_serialization_failure"
    assert validate_child_evidence(rows)
    assert diagnostic_exit_code(0, evidence_errors=validate_child_evidence(rows)) == 1


def test_duplicate_and_out_of_order_critical_events_fail_validation() -> None:
    rows: list[dict[str, Any]] = []
    for event in REQUIRED_SUCCESS_EVENTS:
        row: dict[str, Any] = {"event": event}
        if event == "startup_state_observed":
            row.update(manager_state="STARTING", future_pending=True)
        if event == "disposal_wait_complete":
            row["alive"] = False
        rows.append(row)
    assert not validate_child_evidence(rows)
    rows.insert(0, dict(rows[-1]))
    assert any("occurred 2 times" in error for error in validate_child_evidence(rows))
    rows = rows[1:]
    rows[0], rows[1] = rows[1], rows[0]
    assert any("out of lifecycle order" in error for error in validate_child_evidence(rows))


def test_all_baseline_processes_are_matched_across_timestamp_encodings_and_pid_reuse() -> None:
    baseline = [
        {"Name": "MATLAB.exe", "PID": 22312, "CreationDate": "/Date(1791550257476)/"},
        {"Name": "MATLAB.exe", "PID": 55555, "CreationDate": "20261011104620.906000+000"},
        {"Name": "MATLAB.exe", "PID": 60000, "CreationDate": "2026-10-11T10:47:00.000Z"},
        {"Name": "MATLABWindow.exe", "PID": 90000, "CreationDate": "20261011105000.000000+000"},
    ]
    final = [
        {
            "Name": "MATLAB.exe",
            "PID": 22312,
            "CreationDate": dt.datetime.fromtimestamp(1791550257.47697, tz=dt.UTC),
        },
        {"Name": "MATLAB.exe", "PID": 55555, "CreationDate": "2026-10-11T10:46:20.906Z"},
        {"Name": "MATLAB.exe", "PID": 60000, "CreationDate": "/Date(1791715680000)/"},
        {"Name": "MATLABWindow.exe", "PID": 90000, "CreationDate": 1791715800000},
    ]
    comparison = compare_process_inventories(baseline, final)
    assert len(comparison["preserved"]) == 3
    assert len(comparison["pid_reused"]) == 1
    assert not comparison["missing"]
    assert normalize_creation_time("/Date(1791550257476)/") == normalize_creation_time("2026-10-09T12:50:57.476Z")
    assert normalize_creation_time("20261011104620.906000+000") == normalize_creation_time("2026-10-11T10:46:20.906Z")


def _run_supervisor_failure_in_fresh_python(tmp_path: Path, *, fail_phase: str) -> subprocess.CompletedProcess[str]:
    from tools.matlab_lifecycle.interrupted_startup_supervisor import REPOSITORY

    output = tmp_path / fail_phase
    code = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from tools.matlab_lifecycle import interrupted_startup_supervisor as supervisor

        def environment(_repository):
            return {{"python": "test", "repository": {{"branch": "test", "head": "0"}}}}

        if {fail_phase!r} == "snapshot":
            def snapshot():
                raise RuntimeError("injected WMI inventory failure")
            launch = None
        else:
            def snapshot():
                return []
            def launch(*_args, **_kwargs):
                raise OSError("injected child launch failure")

        raise SystemExit(supervisor.run_diagnostic(
            Path(sys.argv[1]), snapshot_fn=snapshot, launch_fn=launch,
            environment_fn=environment, repository=Path({str(REPOSITORY)!r}),
        ))
        """
    )
    environment = os.environ.copy()
    current_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(REPOSITORY) + (os.pathsep + current_pythonpath if current_pythonpath else "")
    return subprocess.run(
        [sys.executable, "-c", code, str(output)],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize("fail_phase", ["snapshot", "launch", "writer_open"])
def test_fresh_supervisor_process_closes_writer_on_preflight_and_launch_failure(
    tmp_path: Path, fail_phase: str
) -> None:
    output = tmp_path / fail_phase
    if fail_phase == "writer_open":
        (output / "supervisor.jsonl").mkdir(parents=True)
    result = _run_supervisor_failure_in_fresh_python(tmp_path, fail_phase=fail_phase)
    assert result.returncode != 0
    assert "Traceback" in result.stderr
    assert (output / "REPORT.md").exists()
    if fail_phase == "writer_open":
        assert "Supervisor JSONL writer errors" in (output / "REPORT.md").read_text(encoding="utf-8")
    else:
        assert (output / "supervisor_failure.txt").exists()
        event_rows = read_jsonl(output / "supervisor.jsonl")
        assert any(row["event"] == "supervisor_exception" for row in event_rows)


def test_jsonl_writer_open_failure_is_reported_and_closes_thread(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.mkdir()
    writer = JsonlEventWriter(path)
    writer.emit("evidence")
    errors = writer.close()
    assert errors
    assert writer.serialization_failures == 0


def test_jsonl_writer_write_failure_is_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    original_open = Path.open

    class BrokenStream:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, _value: str) -> None:
            raise OSError("injected write failure")

        def flush(self) -> None:
            return None

        def fileno(self) -> int:
            return 1

    def open_path(target: Path, *args: Any, **kwargs: Any) -> Any:
        if target == path:
            return BrokenStream()
        return original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    writer = JsonlEventWriter(path)
    assert writer.emit("write_failure")
    errors = writer.close()
    assert errors and "injected write failure" in errors[0]
    assert writer.emit("after_failure") is False


def test_fake_complete_child_can_pass_only_with_all_evidence_and_baselines_preserved(tmp_path: Path) -> None:
    from tools.matlab_lifecycle.interrupted_startup_supervisor import REPOSITORY, run_diagnostic

    baseline = [
        {"Name": "MATLAB.exe", "PID": 22312, "CreationDate": "/Date(1791550257476)/"},
        {"Name": "MATLAB.exe", "PID": 55555, "CreationDate": "20261011104620.906000+000"},
        {"Name": "MATLABWindow.exe", "PID": 90000, "CreationDate": "20261011105000.000000+000"},
    ]
    child_code = textwrap.dedent(
        """
        import json
        import time
        events = [
            {"event": "child_environment"},
            {"event": "prewarm_requested"},
            {"event": "startup_state_observed", "manager_state": "STARTING", "future_pending": True},
            {"event": "manager_shutdown_requested", "monotonic_ns": time.monotonic_ns()},
            {"event": "manager_shutdown_returned"},
            {"event": "disposal_thread_observed"},
            {"event": "disposal_wait_started"},
            {"event": "disposal_wait_complete", "alive": False},
        ]
        with open(sys.argv[-1] + "\\\\child.jsonl", "w", encoding="utf-8", newline="\\n") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\\n")
        """
    ).replace("import json", "import json\nimport sys")

    def launch(command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> subprocess.Popen[bytes]:
        return launch_child(
            [sys.executable, "-c", child_code, command[-1]],
            cwd=cwd,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    comparison_snapshots = iter([baseline, baseline, baseline])

    def snapshot() -> list[dict[str, Any]]:
        return next(comparison_snapshots, baseline)

    exit_code = run_diagnostic(
        tmp_path / "successful-run",
        snapshot_fn=snapshot,
        launch_fn=launch,
        environment_fn=lambda _repo: {
            "python": "test",
            "matlab_engine_version": "fake",
            "repository": {"branch": "test", "head": "0"},
        },
        repository=REPOSITORY,
        max_before_shutdown_s=5,
        max_after_shutdown_s=5,
        post_exit_observation_s=0,
    )

    assert exit_code == 0
    report = (tmp_path / "successful-run" / "REPORT.md").read_text(encoding="utf-8")
    assert "Evidence errors: `[]`" in report
    assert "Existing MATLAB processes preserved: `3/3`" in report
