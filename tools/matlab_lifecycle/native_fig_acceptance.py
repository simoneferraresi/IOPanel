"""Operator-gated native MATLAB FIG lifecycle acceptance.

The default invocation prints the test plan and never imports MATLAB Engine.
Every native scenario runs in a child process with a bounded parent observer.
If an operation exceeds its deadline, the observer records the process tree and
leaves the child and any MATLAB session running for operator-directed recovery.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

SCENARIOS = (
    "close-before-startup",
    "normal-fig",
    "close-during-startup",
    "rejected-cancel",
    "close-during-fig",
)
OPERATOR_CONFIRMATION = "I approve native MATLAB Engine acceptance on this workstation"
EXPECTED_MATLAB_ROOT = r"C:\Program Files\MATLAB\R2024b"
POST_CHILD_EXIT_OBSERVATION_SECONDS = 30.0
POST_CHILD_EXIT_POLL_INTERVAL_SECONDS = 0.25
EXCEPTION_CLEANUP_TIMEOUT_SECONDS = 30.0
MATLAB_FIG_REOPEN_VERIFICATION = """
iopanel_native_fig = openfig(iopanel_native_fig_path, 'new', 'invisible');
iopanel_native_lines = findall(iopanel_native_fig, 'Type', 'line');
assert(numel(iopanel_native_lines) == 2, 'Expected two detector traces');
iopanel_native_det1 = findobj(iopanel_native_lines, 'DisplayName', 'Det 1');
iopanel_native_det2 = findobj(iopanel_native_lines, 'DisplayName', 'Det 2');
assert(isequal(get(iopanel_native_det1, 'XData'), iopanel_expected_x));
assert(isequal(get(iopanel_native_det2, 'XData'), iopanel_expected_x));
assert(isequal(get(iopanel_native_det1, 'YData'), iopanel_expected_det1));
assert(isequal(get(iopanel_native_det2, 'YData'), iopanel_expected_det2));
assert(max(abs(get(iopanel_native_det1, 'Color') - [27 158 119] / 255)) < 1e-12);
assert(max(abs(get(iopanel_native_det2, 'Color') - [217 95 2] / 255)) < 1e-12);
iopanel_native_axes = get(iopanel_native_det1(1), 'Parent');
assert(strcmp(get(get(iopanel_native_axes, 'XLabel'), 'String'), 'Wavelength (nm)'));
assert(strcmp(get(get(iopanel_native_axes, 'YLabel'), 'String'), 'Power (mW)'));
assert(strcmp(get(get(iopanel_native_axes, 'Title'), 'String'), 'Native acceptance'));
close(iopanel_native_fig);
clear iopanel_native_fig iopanel_native_lines iopanel_native_det1 iopanel_native_det2;
"""
PROCESS_QUERY = r"""
$rows = Get-CimInstance Win32_Process | ForEach-Object {
    $owner = $null
    try { $owner = $_.GetOwner() } catch {}
    [PSCustomObject]@{
        pid = [int]$_.ProcessId
        ppid = [int]$_.ParentProcessId
        name = [string]$_.Name
        path = [string]$_.ExecutablePath
        created = [string]$_.CreationDate
        owner = if ($owner) { [string]$owner.Domain + '\\' + [string]$owner.User } else { $null }
    }
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 3
"""


def process_snapshot() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PROCESS_QUERY],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"Could not inventory Windows process ownership: {completed.stderr.strip()}")
    value = json.loads(completed.stdout or "[]")
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise TypeError("Windows process inventory did not return a list")
    return [row for row in value if isinstance(row, dict)]


def descendants(rows: list[dict[str, Any]], root_pid: int) -> set[int]:
    parents = {int(row["pid"]): int(row.get("ppid") or 0) for row in rows if row.get("pid") is not None}
    found = {root_pid}
    while True:
        children = {pid for pid, parent in parents.items() if parent in found}
        next_found = found | children
        if next_found == found:
            return found - {root_pid}
        found = next_found


def expected_matlab_path(path: object) -> bool:
    if not isinstance(path, str) or not path:
        return False
    try:
        root = os.path.normcase(os.path.abspath(EXPECTED_MATLAB_ROOT))
        executable = os.path.normcase(os.path.abspath(path))
        return os.path.commonpath((root, executable)) == root
    except (OSError, ValueError):
        return False


def _owned_matlab_processes_still_running(
    seen_owned_matlab: dict[int, dict[str, Any]], rows: list[dict[str, Any]]
) -> list[int]:
    after_by_pid = {int(row["pid"]): row for row in rows if row.get("pid") is not None}
    active = []
    for pid, owned_row in seen_owned_matlab.items():
        after_row = after_by_pid.get(pid)
        if after_row is None:
            continue
        owned_created = owned_row.get("created")
        after_created = after_row.get("created")
        if owned_created is None or after_created is None or owned_created == after_created:
            active.append(pid)
    return active


def observe_owned_matlab_exit(
    seen_owned_matlab: dict[int, dict[str, Any]],
    initial_rows: list[dict[str, Any]],
    *,
    timeout_seconds: float = POST_CHILD_EXIT_OBSERVATION_SECONDS,
    snapshot_fn: Callable[[], list[dict[str, Any]]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    monotonic_fn: Callable[[], float] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    """Observe orderly MATLAB exit after Python finishes; never terminate it."""
    snapshot = snapshot_fn or process_snapshot
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    started = monotonic()
    deadline = started + max(0.0, timeout_seconds)
    rows = initial_rows
    while _owned_matlab_processes_still_running(seen_owned_matlab, rows):
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        sleep(min(POST_CHILD_EXIT_POLL_INTERVAL_SECONDS, remaining))
        rows = snapshot()
    return rows, monotonic() - started


def append_event(path: Path, event: str, **fields: object) -> None:
    row = {"utc": datetime.now(UTC).isoformat(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run_child(scenario: str, evidence: Path, *, deadline_seconds: float) -> int:
    child_token = secrets.token_urlsafe(32)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_child",
        "--_child-token",
        child_token,
        "--scenario",
        scenario,
    ]
    command.extend(("--evidence-dir", str(evidence), "--deadline-seconds", str(deadline_seconds)))
    before = process_snapshot()
    environment = os.environ.copy()
    environment["IOPANEL_NATIVE_ACCEPTANCE_CHILD_TOKEN"] = child_token
    child = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2], env=environment)
    started = time.monotonic()
    seen_owned_matlab: dict[int, dict[str, Any]] = {}
    unexpected_matlab_paths: dict[int, dict[str, Any]] = {}
    timed_out = False
    while child.poll() is None:
        rows = process_snapshot()
        child_descendants = descendants(rows, child.pid)
        for row in rows:
            try:
                pid = int(row.get("pid", 0))
            except (TypeError, ValueError):
                continue
            if pid in child_descendants and str(row.get("name", "")).lower().startswith("matlab"):
                seen_owned_matlab[pid] = row
                if not expected_matlab_path(row.get("path")):
                    unexpected_matlab_paths[pid] = row
        if time.monotonic() - started >= deadline_seconds:
            timed_out = True
            break
        time.sleep(0.5)

    after_immediately = process_snapshot()
    after = after_immediately
    post_child_observation_seconds = 0.0
    if (
        not timed_out
        and child.poll() is not None
        and _owned_matlab_processes_still_running(seen_owned_matlab, after_immediately)
    ):
        after, post_child_observation_seconds = observe_owned_matlab_exit(seen_owned_matlab, after_immediately)
    after_by_pid = {int(row["pid"]): row for row in after if row.get("pid") is not None}
    immediate_by_pid = {int(row["pid"]): row for row in after_immediately if row.get("pid") is not None}
    before_by_pid = {int(row["pid"]): row for row in before if row.get("pid") is not None}
    owned = []
    for pid, row in sorted(seen_owned_matlab.items()):
        after_row = after_by_pid.get(pid)
        immediate_row = immediate_by_pid.get(pid)
        running_immediately = immediate_row is not None and (
            row.get("created") is None
            or immediate_row.get("created") is None
            or immediate_row.get("created") == row.get("created")
        )
        same_process = after_row is not None and (
            row.get("created") is None
            or after_row.get("created") is None
            or after_row.get("created") == row.get("created")
        )
        owned.append(
            {
                **row,
                "running_immediately_after_child": running_immediately,
                "still_running_after_child": same_process,
                "exited_during_post_child_observation": running_immediately and not same_process,
            }
        )
    observed_rows = {
        (int(row["pid"]), row.get("created")): row for row in (*after_immediately, *after) if row.get("pid") is not None
    }

    def has_confirmed_child_ancestry(row: dict[str, Any]) -> bool:
        owned_row = seen_owned_matlab.get(int(row["pid"]))
        if owned_row is None:
            return False
        owned_created = owned_row.get("created")
        observed_created = row.get("created")
        return owned_created is None or observed_created is None or owned_created == observed_created

    report = {
        "scenario": scenario,
        "child_pid": child.pid,
        "child_returncode": child.poll(),
        "timed_out": timed_out,
        "child_left_running": child.poll() is None,
        "elapsed_seconds": time.monotonic() - started,
        "post_child_exit_observation_seconds": post_child_observation_seconds,
        "preexisting_matlab_processes": [
            row for row in before if str(row.get("name", "")).lower().startswith("matlab")
        ],
        "test_owned_matlab_processes_observed": owned,
        "test_owned_matlab_processes_with_unexpected_executable_path": list(unexpected_matlab_paths.values()),
        "matlab_processes_remaining_immediately_after_child": [
            row for row in after_immediately if str(row.get("name", "")).lower().startswith("matlab")
        ],
        "matlab_processes_remaining": [row for row in after if str(row.get("name", "")).lower().startswith("matlab")],
        "new_matlab_pids_without_confirmed_child_ancestry": [
            row
            for row in observed_rows.values()
            if row.get("pid") is not None
            and (
                int(row["pid"]) not in before_by_pid
                or row.get("created") != before_by_pid[int(row["pid"])].get("created")
            )
            and str(row.get("name", "")).lower().startswith("matlab")
            and not has_confirmed_child_ancestry(row)
        ],
    }
    (evidence / "supervisor.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if timed_out:
        print(
            f"NATIVE_ACCEPTANCE_PENDING: child {child.pid} is still running. "
            "No process was terminated; inspect supervisor.json and recover only the attributed test session.",
            file=sys.stderr,
        )
        return 2
    if any(row["still_running_after_child"] for row in owned):
        print(
            "NATIVE_ACCEPTANCE_STOP: a test-owned MATLAB process remained after its Python child exited; "
            "the next case will not start.",
            file=sys.stderr,
        )
        return 3
    if unexpected_matlab_paths:
        print(
            "NATIVE_ACCEPTANCE_STOP: test-owned MATLAB process path could not be verified under R2024b; "
            "the next case will not start.",
            file=sys.stderr,
        )
        return 4
    if report["new_matlab_pids_without_confirmed_child_ancestry"]:
        print(
            "NATIVE_ACCEPTANCE_STOP: a new MATLAB process lacked confirmed child ancestry; "
            "the next case will not start.",
            file=sys.stderr,
        )
        return 5
    return child.returncode or 0


def child_main(scenario: str, evidence: Path, deadline_seconds: float) -> int:
    if sys.platform != "win32":
        raise RuntimeError("Native MATLAB acceptance runner supports Windows only")

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    import matlab  # type: ignore[reportMissingImports]
    import matlab.engine  # type: ignore[reportMissingImports]
    import numpy as np
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app_settings import AppSettings
    from logic.matlab_engine_manager import MatlabEngineState
    from ui.control_panel import ScanSettings
    from ui.plot_widgets import PlotWidget, PreparedMatlabFigPayload, PreparedMatlabFigTrace

    if matlab.engine is None:
        raise RuntimeError("MATLAB Engine import is unavailable")

    app = cast(QApplication | None, QApplication.instance())
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    settings = AppSettings(QSettings(str(evidence / "isolated-settings.ini"), QSettings.Format.IniFormat))
    widget = PlotWidget(ScanSettings(), settings=settings)
    widget.hide()
    event_path = evidence / "events.jsonl"
    fig_path = evidence / "synthetic-native-acceptance.fig"
    wavelengths = np.array([1549.8, 1550.0, 1550.2, 1550.4], dtype=float)
    detector_1 = np.array([1.1, 1.2, 1.3, 1.4], dtype=float)
    detector_2 = np.array([2.1, 2.0, 1.9, 1.8], dtype=float)
    for values in (wavelengths, detector_1, detector_2):
        values.setflags(write=False)
    payload = PreparedMatlabFigPayload(
        wavelengths,
        (
            PreparedMatlabFigTrace("Det 1", detector_1, (27 / 255, 158 / 255, 119 / 255)),
            PreparedMatlabFigTrace("Det 2", detector_2, (217 / 255, 95 / 255, 2 / 255)),
        ),
    )
    request = (payload, str(fig_path), "Native acceptance", "Wavelength (nm)", "Power (mW)")
    manager = widget.matlab_engine_manager

    class QuitObservedEngine:
        def __init__(self, engine: Any) -> None:
            self._engine = engine

        def __getattr__(self, name: str) -> Any:
            return getattr(self._engine, name)

        def quit(self) -> Any:
            append_event(event_path, "native_engine_quit_entered")
            try:
                result = self._engine.quit()
            except Exception as error:
                append_event(event_path, "native_engine_quit_failed", error=str(error))
                raise
            append_event(event_path, "native_engine_quit_returned")
            return result

    class ObservedFuture:
        def __init__(self, future: Any) -> None:
            self._future = future

        def done(self) -> bool:
            return bool(self._future.done())

        def result(self) -> Any:
            engine = self._future.result()
            append_event(event_path, "native_engine_startup_returned")
            return QuitObservedEngine(engine)

        def cancel(self) -> bool:
            accepted = bool(self._future.cancel())
            append_event(event_path, "native_startup_cancel_returned", accepted=accepted)
            return accepted

    native_start = manager._start_engine
    if native_start is not None:
        manager._start_engine = lambda **kwargs: ObservedFuture(native_start(**kwargs))

    def record_state(state: MatlabEngineState) -> None:
        append_event(event_path, "manager_state", state=state.name)

    manager.state_changed.connect(record_state)
    manager.shutdown_complete.connect(lambda: append_event(event_path, "engine_shutdown_complete"))
    manager.shutdown_failed.connect(lambda error: append_event(event_path, "engine_shutdown_failed", error=error))
    native_acquire = manager.acquire_engine
    native_release = manager.release_engine

    def record_acquire() -> Any:
        engine = native_acquire()
        append_event(
            event_path, "engine_lease_acquired", accepted=engine is not None, lease_count=manager._engine_users
        )
        return engine

    def record_release() -> None:
        native_release()
        append_event(event_path, "engine_lease_released", lease_count=manager._engine_users)

    manager.acquire_engine = record_acquire
    manager.release_engine = record_release
    widget._matlab_save_thread_finished.connect(
        lambda thread, _worker: append_event(
            event_path,
            "fig_qthread_retired",
            is_running=thread.isRunning(),
            lease_count=manager._engine_users,
        )
    )
    append_event(
        event_path,
        "runner_started",
        scenario=scenario,
        python_pid=os.getpid(),
        matlab_engine_version=__import__("importlib.metadata", fromlist=["version"]).version("matlabengine"),
    )

    def pump_until(predicate, timeout_seconds: float, label: str) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        append_event(event_path, "deadline_reached", label=label, timeout_seconds=timeout_seconds)
        return bool(predicate())

    scenario_exception: Exception | None = None
    try:
        if scenario == "close-before-startup":
            startup_attempts = []
            original_start = manager._start_engine

            def count_start(**kwargs: Any) -> Any:
                startup_attempts.append(True)
                if original_start is None:
                    raise RuntimeError("MATLAB Engine startup function is unavailable")
                return original_start(**kwargs)

            manager._start_engine = count_start
            accepted = widget.close()
            append_event(event_path, "close_requested_before_startup", close_accepted=accepted)
            if not pump_until(lambda: widget.shutdown_finished, deadline_seconds, "close_before_startup"):
                return 9
            if startup_attempts or manager.state is not MatlabEngineState.SHUT_DOWN:
                raise AssertionError("Close before startup launched MATLAB or failed to retire cleanly")

        elif scenario == "close-during-startup":
            manager.request_engine()
            append_event(event_path, "startup_requested", manager_state=manager.state.name)
            if manager.state is not MatlabEngineState.STARTING:
                append_event(event_path, "startup_case_not_exercised", manager_state=manager.state.name)
                return 3
            accepted = widget.close()
            append_event(event_path, "close_requested_during_startup", close_accepted=accepted)

        elif scenario == "rejected-cancel":
            manager.request_engine()
            if manager.state is not MatlabEngineState.STARTING or manager._future is None:
                append_event(event_path, "rejected_cancel_case_not_exercised", manager_state=manager.state.name)
                return 3

            class RejectCancel:
                def __init__(self, future: Any) -> None:
                    self._future = future

                def done(self) -> bool:
                    return bool(self._future.done())

                def result(self) -> object:
                    return self._future.result()

                def cancel(self) -> bool:
                    append_event(event_path, "startup_cancel_forced_rejection")
                    return False

            native_future = manager._future
            manager._future = RejectCancel(native_future)
            native_future = None
            accepted = widget.close()
            append_event(event_path, "close_requested_after_forced_cancel_rejection", close_accepted=accepted)

        elif scenario in ("normal-fig", "close-during-fig"):
            if scenario == "close-during-fig":
                manager.request_engine()
                if not pump_until(lambda: manager.state is MatlabEngineState.READY, deadline_seconds, "engine_ready"):
                    return 4
                if manager._engine is None:
                    raise AssertionError("MATLAB Engine was not retained after READY")
                engine: Any = manager._engine
                entered_save = __import__("threading").Event()
                save_returned = __import__("threading").Event()
                quit_after_save = []

                class DelayedEngine:
                    def __getattr__(self, name: str) -> object:
                        return getattr(engine, name)

                    def savefig(self, *args: object, **kwargs: object) -> object:
                        entered_save.set()
                        append_event(event_path, "native_fig_save_entered")
                        engine.eval("pause(5)", nargout=0)
                        result = engine.savefig(*args, **kwargs)
                        save_returned.set()
                        append_event(event_path, "native_fig_save_returned")
                        return result

                    def quit(self) -> object:
                        quit_after_save.append(save_returned.is_set())
                        append_event(event_path, "native_engine_quit_entered", after_fig_save=save_returned.is_set())
                        return engine.quit()

                manager._engine = DelayedEngine()
            widget._queue_matlab_fig(request)
            if scenario == "close-during-fig":
                if not pump_until(entered_save.is_set, deadline_seconds, "native_fig_save_entry"):
                    return 5
                heartbeats = [0]
                from PySide6.QtCore import QTimer

                heartbeat = QTimer()
                heartbeat.setInterval(100)
                heartbeat.timeout.connect(lambda: heartbeats.__setitem__(0, heartbeats[0] + 1))
                heartbeat.start()
                accepted = widget.close()
                append_event(event_path, "close_requested_during_fig", close_accepted=accepted)
                if not pump_until(lambda: widget.shutdown_finished, deadline_seconds, "fig_retirement_and_quit"):
                    return 6
                heartbeat.stop()
                append_event(
                    event_path,
                    "fig_close_checks",
                    gui_heartbeat_count=heartbeats[0],
                    save_returned=save_returned.is_set(),
                    engine_quit_after_save=quit_after_save,
                )
                if heartbeats[0] < 3 or not save_returned.is_set() or quit_after_save != [True]:
                    raise AssertionError("FIG close ordering or GUI heartbeat did not meet acceptance criteria")
            else:
                if not pump_until(lambda: widget.pending_saves == 0, deadline_seconds, "fig_export"):
                    return 7
                if widget.error_list or not fig_path.is_file():
                    raise AssertionError(f"Native FIG export failed: {widget.error_list}")
                engine = manager.engine
                if engine is None:
                    raise AssertionError("Engine was not ready to reopen the FIG")
                engine.workspace["iopanel_native_fig_path"] = str(fig_path)
                engine.workspace["iopanel_expected_x"] = matlab.double(wavelengths.tolist())
                engine.workspace["iopanel_expected_det1"] = matlab.double(detector_1.tolist())
                engine.workspace["iopanel_expected_det2"] = matlab.double(detector_2.tolist())
                engine.eval(MATLAB_FIG_REOPEN_VERIFICATION, nargout=0)
                append_event(event_path, "native_fig_reopened_and_verified", fig_path=str(fig_path))
                widget.close()

        if not pump_until(lambda: widget.shutdown_finished, deadline_seconds, "widget_shutdown"):
            return 8
        if not widget.close():
            raise AssertionError("PlotWidget did not accept close after native retirement")
        append_event(event_path, "widget_close_accepted", engine_state=manager.state.name)
        if manager.state not in (MatlabEngineState.SHUT_DOWN, MatlabEngineState.UNAVAILABLE):
            raise AssertionError(f"Unexpected final manager state: {manager.state.name}")
        append_event(
            event_path,
            "scenario_passed",
            scenario=scenario,
            fig_path=str(fig_path) if fig_path.exists() else None,
        )
        return 0
    except Exception as error:
        scenario_exception = error
        try:
            append_event(
                event_path,
                "scenario_exception",
                exception_type=type(error).__name__,
                exception=str(error),
                traceback=traceback.format_exc(),
            )
        except (OSError, TypeError, ValueError) as logging_error:
            print(f"Could not record native acceptance exception: {logging_error}", file=sys.stderr)
        raise
    finally:
        cleanup_error: Exception | None = None

        def record_cleanup_event(event: str, **fields: object) -> None:
            try:
                append_event(event_path, event, **fields)
            except (OSError, TypeError, ValueError) as logging_error:
                print(f"Could not record native cleanup event {event}: {logging_error}", file=sys.stderr)

        if not widget.shutdown_finished:
            try:
                record_cleanup_event(
                    "exception_cleanup_started" if scenario_exception else "final_cleanup_started",
                    manager_state=manager.state.name,
                    shutdown_finished=widget.shutdown_finished,
                )
                close_accepted = widget.close()
                record_cleanup_event(
                    "exception_cleanup_close_requested" if scenario_exception else "final_cleanup_close_requested",
                    close_accepted=close_accepted,
                )
                cleanup_completed = pump_until(
                    lambda: widget.shutdown_finished,
                    min(deadline_seconds, EXCEPTION_CLEANUP_TIMEOUT_SECONDS),
                    "exception_path_cleanup" if scenario_exception else "final_cleanup",
                )
                record_cleanup_event(
                    "exception_cleanup_finished" if scenario_exception else "final_cleanup_finished",
                    completed=cleanup_completed,
                    manager_state=manager.state.name,
                    shutdown_finished=widget.shutdown_finished,
                )
            except Exception as error:  # noqa: BLE001 - cleanup errors must not replace the original failure.
                cleanup_error = error
                try:
                    append_event(
                        event_path,
                        "exception_cleanup_failed",
                        exception_type=type(error).__name__,
                        exception=str(error),
                        traceback=traceback.format_exc(),
                    )
                except (OSError, TypeError, ValueError) as logging_error:
                    print(f"Could not record native cleanup failure: {logging_error}", file=sys.stderr)
        elif scenario_exception is not None:
            record_cleanup_event(
                "exception_cleanup_not_needed",
                manager_state=manager.state.name,
                shutdown_finished=True,
            )
        try:
            widget.deleteLater()
            app.processEvents()
        except Exception as error:  # noqa: BLE001 - report cleanup failure without hiding the original error.
            cleanup_error = cleanup_error or error
            try:
                record_cleanup_event(
                    "exception_cleanup_disposal_failed",
                    exception_type=type(error).__name__,
                    exception=str(error),
                    traceback=traceback.format_exc(),
                )
            except (OSError, TypeError, ValueError) as logging_error:
                print(f"Could not record native cleanup disposal failure: {logging_error}", file=sys.stderr)
        if cleanup_error is not None and scenario_exception is None:
            raise cleanup_error


def print_plan() -> None:
    print("Native MATLAB Engine acceptance plan (no MATLAB launch):")
    for scenario in SCENARIOS:
        print(f"  - {scenario}")
    print("Expected launches: 0 for close-before-startup, 1 for normal-fig,")
    print("                  0-1 for close-during-startup, 1 for rejected-cancel,")
    print("                  1 for close-during-fig (3-4 total).")
    print("The parent observes only process metadata; it never terminates processes.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all")
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--deadline-minutes", type=float, default=20.0)
    parser.add_argument("--execute", action="store_true", help="Start real MATLAB Engine sessions")
    parser.add_argument("--confirm-native-matlab", action="store_true")
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_child-token", help=argparse.SUPPRESS)
    parser.add_argument("--deadline-seconds", type=float, default=1200.0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._child:
        inherited_token = os.environ.pop("IOPANEL_NATIVE_ACCEPTANCE_CHILD_TOKEN", None)
        if not inherited_token or not secrets.compare_digest(args._child_token or "", inherited_token):
            raise SystemExit("Native acceptance child must be launched by an approved supervisor.")
        if args.scenario == "all" or args.evidence_dir is None:
            parser.error("The supervised child requires one scenario and an evidence directory")
        return child_main(args.scenario, args.evidence_dir, args.deadline_seconds)
    if not args.execute:
        print_plan()
        return 0
    if not args.confirm_native_matlab or input(f"Type exactly: {OPERATOR_CONFIRMATION}\n> ") != OPERATOR_CONFIRMATION:
        raise SystemExit("Native MATLAB execution requires the explicit operator confirmation phrase.")
    if args.evidence_dir is None:
        parser.error("--evidence-dir is required with --execute")
    if args.deadline_minutes <= 0 or args.deadline_minutes > 45:
        parser.error("--deadline-minutes must be in (0, 45]")
    if args.evidence_dir.exists():
        parser.error("Evidence directory already exists; choose a new path (the runner never overwrites evidence).")
    args.evidence_dir.mkdir(parents=True)
    scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
    manifest: dict[str, Any] = {
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "started_utc": datetime.now(UTC).isoformat(),
        "scenario_order": list(scenarios),
        "deadline_minutes_per_scenario": args.deadline_minutes,
        "process_policy": "observe only; never terminate MATLAB or Python processes",
        "results": [],
    }
    for scenario in scenarios:
        case_dir = args.evidence_dir / scenario
        case_dir.mkdir()
        if args._child:
            code = child_main(scenario, case_dir, args.deadline_seconds)
        else:
            code = run_child(scenario, case_dir, deadline_seconds=args.deadline_minutes * 60)
        manifest["results"].append({"scenario": scenario, "returncode": code})
        (args.evidence_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if code:
            return code
    manifest["finished_utc"] = datetime.now(UTC).isoformat()
    (args.evidence_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
