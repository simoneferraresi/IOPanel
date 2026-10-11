from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tools.matlab_lifecycle.diagnostic_harness import (
    JsonlEventWriter,
    read_jsonl,
    summarize_matlab_candidates,
    validate_normal_exit_child_evidence,
)
from tools.matlab_lifecycle.normal_exit_child import record_normal_interpreter_exit


def _normal_exit_rows(*, completed: bool, alive: bool, result: bool | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"event": "child_environment"},
        {"event": "prewarm_requested"},
        {"event": "startup_state_observed", "manager_state": "STARTING", "future_pending": True},
        {"event": "manager_shutdown_requested", "manager_state_before": "STARTING", "future_pending": True},
        {"event": "manager_shutdown_returned", "manager_state_after": "SHUTTING_DOWN"},
        {"event": "disposal_thread_observed", "worker_exists": True, "daemon": True},
        {"event": "future_cancel_entered", "monotonic_ns": 10},
    ]
    if completed:
        rows.append({"event": "future_cancel_return", "result": result, "monotonic_ns": 20})
    rows.append(
        {
            "event": "python_interpreter_atexit",
            "monotonic_ns": 30,
            "cancel_started": True,
            "cancel_completed": completed,
            "cancel_result": result if completed else None,
            "disposer_alive": alive,
            "disposer_joined": False,
        }
    )
    return rows


def test_scenario_validation_accepts_cancellation_completed_before_exit() -> None:
    assert validate_normal_exit_child_evidence(_normal_exit_rows(completed=True, alive=False, result=True)) == ()


def test_scenario_validation_accepts_cancellation_blocked_at_natural_exit() -> None:
    rows = _normal_exit_rows(completed=False, alive=True)
    assert validate_normal_exit_child_evidence(rows) == ()
    assert "disposal_wait_complete" not in [row["event"] for row in rows]


def test_scenario_validation_records_disposer_not_started_before_interpreter_exit() -> None:
    rows = _normal_exit_rows(completed=False, alive=True)
    rows.remove(next(row for row in rows if row["event"] == "future_cancel_entered"))
    rows[-1]["cancel_started"] = False
    assert validate_normal_exit_child_evidence(rows) == ()


@pytest.mark.parametrize(
    ("post_exit_candidate", "final_candidate", "expected"),
    [
        (True, True, "survived_child_exit"),
        (True, False, "disappeared_during_post_exit_observation"),
        (False, False, "absent_by_first_post_exit_inventory"),
    ],
)
def test_candidate_disposition_uses_pid_creation_time_and_observation_phase(
    post_exit_candidate: bool,
    final_candidate: bool,
    expected: str,
) -> None:
    baseline = [{"Name": "MATLAB.exe", "PID": 22312, "CreationDate": "2026-10-09T12:50:57.476Z"}]
    candidate = {
        "Name": "MATLAB.exe",
        "PID": 5001,
        "ParentPID": 4001,
        "CreationDate": "2026-10-11T17:00:00.000Z",
    }
    snapshots = [
        {"phase": "running", "utc": "t1", "processes": [*baseline, candidate]},
        {
            "phase": "post_exit",
            "utc": "t2",
            "processes": [*baseline, candidate] if post_exit_candidate else baseline,
        },
    ]
    final = [*baseline, candidate] if final_candidate else baseline

    result = summarize_matlab_candidates(baseline, snapshots, final, child_pid=4001)

    assert len(result) == 1
    assert result[0]["PID"] == 5001
    assert result[0]["CreationDate"] == candidate["CreationDate"]
    assert result[0]["attribution"] == "candidate; direct parent match"
    assert result[0]["disposition"] == expected


def test_missing_and_corrupt_evidence_fail_scenario_validation(tmp_path: Path) -> None:
    path = tmp_path / "child.jsonl"
    path.write_bytes(b'{"event":"child_environment"}\nnot-json\n')
    parsed = read_jsonl(path)
    errors = validate_normal_exit_child_evidence(parsed.rows, parsed.errors)
    assert any("invalid JSONL" in error for error in errors)
    assert any("missing critical normal-exit event" in error for error in errors)


def test_exit_validator_rejects_disposer_join_and_false_completion_claim() -> None:
    rows = _normal_exit_rows(completed=False, alive=True)
    rows[-1]["disposer_joined"] = True
    rows[-1]["cancel_completed"] = True
    errors = validate_normal_exit_child_evidence(rows)
    assert any("joined" in error for error in errors)
    assert any("boolean return" in error or "without a recorded return" in error for error in errors)


def test_fake_future_cancellation_can_finish_before_exit_snapshot(tmp_path: Path) -> None:
    events = JsonlEventWriter(tmp_path / "child.jsonl")
    started = threading.Event()
    completed = threading.Event()

    class FakeFuture:
        def cancel(self) -> bool:
            started.set()
            completed.set()
            return True

    class FinishedDisposer:
        def is_alive(self) -> bool:
            return False

    future = FakeFuture()
    assert future.cancel() is True
    errors = record_normal_interpreter_exit(
        events,
        cancel_started=started,
        cancel_completed=completed,
        worker=FinishedDisposer(),
        cancel_result=True,
        cancel_duration_ns=10,
    )
    assert errors == ()
    parsed = read_jsonl(tmp_path / "child.jsonl")
    assert (
        validate_normal_exit_child_evidence(
            [*_normal_exit_rows(completed=True, alive=False, result=True)[:-1], *parsed.rows]
        )
        == ()
    )
    assert parsed.rows[-1]["cancel_completed"] is True
    assert parsed.rows[-1]["disposer_joined"] is False


def test_fresh_python_exit_does_not_wait_for_blocked_daemon_disposer(tmp_path: Path) -> None:
    child_events = tmp_path / "child.jsonl"
    code = """
import atexit
import threading
from pathlib import Path
from tools.matlab_lifecycle.diagnostic_harness import JsonlEventWriter
from tools.matlab_lifecycle.normal_exit_child import record_normal_interpreter_exit

writer = JsonlEventWriter(Path(__import__('sys').argv[1]), daemon=True)
started = threading.Event()
completed = threading.Event()
release = threading.Event()
class FakeFuture:
    def cancel(self):
        started.set()
        release.wait()
        completed.set()
        return True
class FakeWorker:
    def __init__(self): self.thread = None
    def is_alive(self): return bool(self.thread and self.thread.is_alive())
future = FakeFuture()
worker = FakeWorker()
worker.thread = threading.Thread(target=future.cancel, daemon=True)
worker.thread.start()
if not started.wait(3): raise RuntimeError('fake cancellation did not start')
atexit.register(record_normal_interpreter_exit, writer, cancel_started=started,
                cancel_completed=completed, worker=worker)
print('RETURNING_NATURALLY', flush=True)
"""
    stdout_path = tmp_path / "child.stdout.txt"
    stderr_path = tmp_path / "child.stderr.txt"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(child_events)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
        )
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail(f"fake child PID {process.pid} did not exit naturally; it was left running")

    assert return_code == 0, stderr_path.read_text(encoding="utf-8")
    assert stdout_path.read_text(encoding="utf-8").strip() == "RETURNING_NATURALLY"
    parsed = read_jsonl(child_events)
    assert parsed.errors == ()
    assert parsed.rows[-1]["event"] == "python_interpreter_atexit"
    assert parsed.rows[-1]["cancel_started"] is True
    assert parsed.rows[-1]["cancel_completed"] is False
    assert parsed.rows[-1]["disposer_alive"] is True
    assert parsed.rows[-1]["disposer_joined"] is False


def test_parent_supervisor_observes_natural_exit_without_waiting_on_disposer(tmp_path: Path) -> None:
    from tools.matlab_lifecycle.diagnostic_harness import launch_child
    from tools.matlab_lifecycle.normal_exit_supervisor import REPOSITORY, run_normal_exit_diagnostic

    output = tmp_path / "supervised-normal-exit"
    code = """
import atexit
import os
import sys
import threading
from pathlib import Path
from tools.matlab_lifecycle.diagnostic_harness import JsonlEventWriter
from tools.matlab_lifecycle.normal_exit_child import record_normal_interpreter_exit

output = Path(sys.argv[1])
events = JsonlEventWriter(output / 'child.jsonl', daemon=True)
started = threading.Event()
completed = threading.Event()
release = threading.Event()
class FakeFuture:
    def cancel(self):
        started.set()
        release.wait()
        completed.set()
        return True
class FakeWorker:
    def __init__(self): self.thread = None
    def is_alive(self): return bool(self.thread and self.thread.is_alive())
worker = FakeWorker()
worker.thread = threading.Thread(target=FakeFuture().cancel, daemon=True)
events.emit('child_environment', child_pid=os.getpid(), parent_pid=os.getppid())
events.emit('prewarm_requested')
events.emit('startup_state_observed', manager_state='STARTING', future_pending=True)
events.emit('manager_shutdown_requested', manager_state_before='STARTING', future_pending=True)
events.emit('manager_shutdown_returned', manager_state_after='SHUTTING_DOWN')
worker.thread.start()
if not started.wait(3): raise RuntimeError('fake cancellation did not start')
events.emit('disposal_thread_observed', worker_exists=True, daemon=True)
events.emit('future_cancel_entered')
atexit.register(record_normal_interpreter_exit, events, cancel_started=started,
                cancel_completed=completed, worker=worker)
print('CHILD_RETURNING', flush=True)
"""

    def launch(command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> subprocess.Popen[bytes]:
        return launch_child(
            [sys.executable, "-c", code, command[-2]],
            cwd=cwd,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )

    baseline = [{"Name": "MATLAB.exe", "PID": 22312, "CreationDate": "2026-10-09T12:50:57.476Z"}]
    result = run_normal_exit_diagnostic(
        output,
        snapshot_fn=lambda: baseline,
        launch_fn=launch,
        environment_fn=lambda _repo: {"python": "fake", "repository": {"branch": "test", "head": "test"}},
        repository=REPOSITORY,
        max_before_shutdown_s=5,
        max_after_shutdown_s=5,
        post_exit_observation_s=0.03,
        interval_s=0.01,
    )

    assert result == 0
    child_rows = read_jsonl(output / "child.jsonl").rows
    at_exit = next(row for row in child_rows if row["event"] == "python_interpreter_atexit")
    assert at_exit["cancel_completed"] is False
    assert at_exit["disposer_alive"] is True
    assert at_exit["disposer_joined"] is False
    supervisor_rows = read_jsonl(output / "supervisor.jsonl").rows
    assert any(row["event"] == "child_exited" and row["return_code"] == 0 for row in supervisor_rows)
    assert "Diagnostic result: `PASS`" in (output / "REPORT.md").read_text(encoding="utf-8")


def test_scenario_supervisor_deadline_reports_without_terminating_fake_child(tmp_path: Path) -> None:
    from tools.matlab_lifecycle.normal_exit_supervisor import REPOSITORY, run_normal_exit_diagnostic

    class FakeClock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

        def wait(self, duration: float) -> None:
            self.now += duration

    class NeverExited:
        pid = 801
        terminated = False

        def poll(self) -> None:
            return None

    process = NeverExited()
    output = tmp_path / "deadline"

    def launch(_command: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> NeverExited:
        del cwd
        stdout_path.touch()
        stderr_path.touch()
        (output / "child.jsonl").write_text(
            json.dumps({"event": "manager_shutdown_requested"}) + "\n",
            encoding="utf-8",
        )
        return process

    clock = FakeClock()
    code = run_normal_exit_diagnostic(
        output,
        snapshot_fn=lambda: [{"Name": "MATLAB.exe", "PID": 22312, "CreationDate": "2026-10-09T12:50:57Z"}],
        launch_fn=launch,
        environment_fn=lambda _repo: {"python": "fake", "repository": {"branch": "test", "head": "test"}},
        repository=REPOSITORY,
        max_before_shutdown_s=0.2,
        post_exit_observation_s=0,
        interval_s=0.1,
        monotonic=clock.monotonic,
        wait=clock.wait,
    )

    assert code != 0
    assert process.terminated is False
    assert "deadline reached" in (output / "REPORT.md").read_text(encoding="utf-8")


def test_normal_exit_supervisor_reports_preflight_snapshot_failure_and_closes_writer(tmp_path: Path) -> None:
    from tools.matlab_lifecycle.normal_exit_supervisor import REPOSITORY, run_normal_exit_diagnostic

    output = tmp_path / "snapshot-failure"
    before = {thread.ident for thread in threading.enumerate() if thread.name == "JsonlEventWriter"}

    def fail_snapshot() -> list[dict[str, Any]]:
        raise PermissionError("injected process inventory failure")

    result = run_normal_exit_diagnostic(
        output,
        snapshot_fn=fail_snapshot,
        environment_fn=lambda _repo: {"python": "fake", "repository": {"branch": "test", "head": "test"}},
        repository=REPOSITORY,
    )

    after = {thread.ident for thread in threading.enumerate() if thread.name == "JsonlEventWriter"}
    assert result != 0
    assert (output / "REPORT.md").exists()
    assert (output / "supervisor_failure.txt").exists()
    assert after == before
