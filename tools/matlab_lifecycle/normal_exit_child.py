"""Exercise pending MATLAB startup followed by ordinary Python interpreter exit.

This child deliberately does not join the daemon startup-disposal worker. It
closes only its own diagnostic writer during interpreter atexit processing.
MATLAB and application imports stay isolated from the parent supervisor.
"""

from __future__ import annotations

import atexit
import importlib.metadata
import sys
import threading
import time
import traceback
from pathlib import Path
from types import FrameType
from typing import Any


def record_normal_interpreter_exit(
    events: Any,
    *,
    cancel_started: threading.Event,
    cancel_completed: threading.Event,
    worker: Any | None,
    cancel_result: bool | None = None,
    cancel_duration_ns: int | None = None,
) -> tuple[str, ...]:
    """Record the atexit boundary and close logging without waiting on the disposer."""
    try:
        disposer_alive = bool(worker is not None and worker.is_alive())
    except BaseException as exc:  # noqa: BLE001 - record failed final state inspection.
        disposer_alive = True
        events.emit(
            "disposal_state_exception",
            exception_type=type(exc).__name__,
            exception=str(exc),
        )
    events.emit(
        "python_interpreter_atexit",
        cancel_started=cancel_started.is_set(),
        cancel_completed=cancel_completed.is_set(),
        cancel_result=cancel_result if cancel_completed.is_set() else None,
        cancel_duration_ns=cancel_duration_ns if cancel_completed.is_set() else None,
        disposer_alive=disposer_alive,
        disposer_joined=False,
    )
    return events.close()


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: normal_exit_child.py <diagnostic-directory> <repository>", file=sys.stderr)
        return 2

    output = Path(sys.argv[1])
    repository = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(repository))

    from tools.matlab_lifecycle.diagnostic_harness import JsonlEventWriter, record_startup_state

    # A daemon writer permits the Python atexit callback to run before it
    # flushes and joins that writer; explicit close still makes evidence durable.
    events = JsonlEventWriter(output / "child.jsonl", daemon=True)
    cancel_started = threading.Event()
    cancel_completed = threading.Event()
    cancel_started_ns: int | None = None
    cancel_observation: dict[str, Any] = {"result": None, "duration_ns": None}
    cancel_observation.update(
        {
            "entry_fields": None,
            "entry_logged": False,
            "return_fields": None,
            "return_logged": False,
        }
    )
    state: dict[str, Any] = {"manager": None, "worker": None, "shutdown_called": False}
    profile_lock = threading.Lock()

    def on_interpreter_exit() -> None:
        with profile_lock:
            worker = state["worker"]
            if cancel_started.is_set() and not cancel_observation["entry_logged"]:
                events.emit(
                    "future_cancel_entered",
                    **cancel_observation["entry_fields"],
                    recorded_at_interpreter_exit=True,
                )
            if cancel_completed.is_set() and not cancel_observation["return_logged"]:
                events.emit(
                    "future_cancel_return",
                    **cancel_observation["return_fields"],
                    recorded_at_interpreter_exit=True,
                )
            close_errors = record_normal_interpreter_exit(
                events,
                cancel_started=cancel_started,
                cancel_completed=cancel_completed,
                worker=worker,
                cancel_result=cancel_observation["result"],
                cancel_duration_ns=cancel_observation["duration_ns"],
            )
        if close_errors:
            print(f"normal-exit diagnostic JSONL writer failed: {close_errors!r}", file=sys.stderr)

    atexit.register(on_interpreter_exit)

    def profile(frame: FrameType, event: str, arg: Any) -> None:
        nonlocal cancel_started_ns
        if frame.f_globals.get("__name__") != "matlab.engine.futureresult" or frame.f_code.co_name != "cancel":
            return
        try:
            if event == "call":
                cancel_started_ns = time.monotonic_ns()
                cancel_observation["entry_fields"] = {
                    "thread_name": threading.current_thread().name,
                    "thread_id": threading.get_ident(),
                    "observed_monotonic_ns": cancel_started_ns,
                }
                cancel_started.set()
                with profile_lock:
                    cancel_observation["entry_logged"] = events.emit(
                        "future_cancel_entered", **cancel_observation["entry_fields"]
                    )
            elif event == "return":
                # Set this before queuing the JSONL row so atexit can classify
                # cancellation even if it runs before the writer thread flushes.
                cancel_observation["result"] = arg if isinstance(arg, bool) else None
                cancel_observation["duration_ns"] = (
                    time.monotonic_ns() - cancel_started_ns if cancel_started_ns is not None else None
                )
                cancel_observation["return_fields"] = {
                    "result": cancel_observation["result"],
                    "duration_ns": cancel_observation["duration_ns"],
                    "thread_name": threading.current_thread().name,
                    "thread_id": threading.get_ident(),
                    "observed_monotonic_ns": time.monotonic_ns(),
                }
                cancel_completed.set()
                with profile_lock:
                    cancel_observation["return_logged"] = events.emit(
                        "future_cancel_return", **cancel_observation["return_fields"]
                    )
            elif event == "exception":
                exception_type, exception, _ = arg
                events.emit(
                    "future_cancel_exception_observed",
                    exception_type=getattr(exception_type, "__name__", str(exception_type)),
                    exception=str(exception),
                )
        except BaseException:  # noqa: BLE001 - tracing must never alter Engine behavior.
            return

    exit_code = 0
    shutdown_started_ns: int | None = None
    try:
        import matlab.engine  # type: ignore[reportMissingImports]  # optional lab-only dependency
        from PySide6.QtCore import QCoreApplication

        from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState

        events.emit(
            "child_environment",
            python=sys.version,
            executable=sys.executable,
            child_pid=__import__("os").getpid(),
            parent_pid=__import__("os").getppid(),
            matlab_engine_version=importlib.metadata.version("matlabengine"),
            matlab_engine_module=matlab.engine.__file__,
            pyside6_version=importlib.metadata.version("PySide6"),
            manager_module=__import__("logic.matlab_engine_manager", fromlist=["x"]).__file__,
        )
        app = QCoreApplication([])
        threading.setprofile(profile)
        sys.setprofile(profile)

        manager = MatlabEngineManager(
            available=True,
            start_engine=matlab.engine.start_matlab,
            prewarm_delay_ms=0,
            poll_interval_ms=150,
        )
        state["manager"] = manager
        manager.state_changed.connect(lambda value: events.emit("manager_state_changed", state=value.name))

        events.emit("prewarm_requested")
        manager.prewarm()
        startup_future = manager._future
        pending = record_startup_state(events, manager.state.name, startup_future)
        precondition_ok = manager.state is MatlabEngineState.STARTING and pending
        if not precondition_ok:
            events.emit(
                "startup_precondition_failed",
                manager_state=manager.state.name,
                future_pending=pending,
            )

        shutdown_started_ns = time.monotonic_ns()
        state["shutdown_called"] = True
        events.emit(
            "manager_shutdown_requested",
            manager_state_before=manager.state.name,
            future_pending=pending,
        )
        manager.shutdown()
        events.emit(
            "manager_shutdown_returned",
            duration_ns=time.monotonic_ns() - shutdown_started_ns,
            manager_state_after=manager.state.name,
        )
        worker = manager._startup_disposal_thread
        state["worker"] = worker
        events.emit(
            "disposal_thread_observed",
            worker_exists=worker is not None,
            worker_name=worker.name if worker is not None else None,
            daemon=worker.daemon if worker is not None else None,
            alive=worker.is_alive() if worker is not None else False,
            ident=worker.ident if worker is not None else None,
        )
        if not precondition_ok or worker is None or not worker.daemon:
            exit_code = 3

        # Do not call join(), wait_for_disposer(), or wait on cancellation.
        # Returning from main triggers the atexit snapshot and cleanly closes
        # the JSONL writer; Python then tears down daemon threads naturally.
        _ = app
    except BaseException as exc:  # noqa: BLE001 - failures must survive in child evidence.
        events.emit(
            "child_exception",
            exception_type=type(exc).__name__,
            exception=str(exc),
            traceback=traceback.format_exc(),
        )
        traceback.print_exc(file=sys.stderr)
        exit_code = 1
        manager = state["manager"]
        if manager is not None and not state["shutdown_called"]:
            try:
                shutdown_started_ns = time.monotonic_ns()
                state["shutdown_called"] = True
                events.emit("manager_shutdown_requested_during_error")
                manager.shutdown()
                events.emit("manager_shutdown_returned_during_error")
                state["worker"] = manager._startup_disposal_thread
            except BaseException as shutdown_error:  # noqa: BLE001 - preserve both failures.
                events.emit(
                    "manager_shutdown_exception_during_error",
                    exception_type=type(shutdown_error).__name__,
                    exception=str(shutdown_error),
                )
        if shutdown_started_ns is not None and state["worker"] is None and manager is not None:
            state["worker"] = getattr(manager, "_startup_disposal_thread", None)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
