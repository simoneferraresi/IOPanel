"""One controlled-wait run of the real MatlabEngineManager.

Only this child imports MATLAB Engine, QtCore, and the manager module. Hardware
and GUI modules are intentionally not imported.
"""

from __future__ import annotations

import importlib.metadata
import logging
import sys
import threading
import time
import traceback
from pathlib import Path
from types import FrameType
from typing import Any, override

REPOSITORY = Path(r"C:\Users\bellanca\Desktop\IOPanel-dev")
sys.path.insert(0, str(REPOSITORY))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: interrupted_startup_child.py <diagnostic-directory>", file=sys.stderr)
        return 2

    output = Path(sys.argv[1])
    from tools.matlab_lifecycle.diagnostic_harness import (
        JsonlEventWriter,
        record_child_exception,
        record_startup_state,
        wait_for_disposer,
    )

    events = JsonlEventWriter(output / "child.jsonl")
    profile_started: dict[tuple[int, Any], int] = {}

    def profile(frame: FrameType, event: str, arg: Any) -> None:
        module = str(frame.f_globals.get("__name__", ""))
        method = frame.f_code.co_name
        if not module.startswith("matlab.engine") or method not in {"cancel", "result", "quit"}:
            return

        key = (threading.get_ident(), frame.f_code)
        thread_fields = {
            "thread_name": threading.current_thread().name,
            "thread_id": threading.get_ident(),
            "module": module,
        }
        try:
            if event == "call":
                profile_started[key] = time.monotonic_ns()
                events.emit("engine_api_call_enter", api=method, code_file=frame.f_code.co_filename, **thread_fields)
            elif event == "return":
                started = profile_started.pop(key, None)
                result: dict[str, Any]
                if isinstance(arg, (bool, int, float, str)) or arg is None:
                    result = {"value": arg}
                else:
                    result = {"is_none": arg is None, "type": type(arg).__qualname__, "module": type(arg).__module__}
                events.emit(
                    "engine_api_call_return",
                    api=method,
                    code_file=frame.f_code.co_filename,
                    duration_ns=time.monotonic_ns() - started if started is not None else None,
                    result=result,
                    **thread_fields,
                )
            elif event == "exception":
                exception_type, exception, _ = arg
                events.emit(
                    "engine_api_call_exception",
                    api=method,
                    exception_type=getattr(exception_type, "__name__", str(exception_type)),
                    exception=str(exception),
                    **thread_fields,
                )
        except BaseException:  # noqa: BLE001 - profiler instrumentation must never alter Engine behavior.
            # A profiler callback must not change the observed Engine method's behavior.
            return

    exit_code = 0
    manager: Any | None = None
    worker: Any | None = None
    shutdown_started_ns: int | None = None
    manager_logger = logging.getLogger("LabApp.matlab_engine")
    handler: logging.Handler | None = None
    try:
        import matlab.engine  # type: ignore[reportMissingImports]  # MATLAB Engine is an optional lab-only dependency.
        from PySide6.QtCore import QCoreApplication

        sys.path.insert(0, str(REPOSITORY))
        from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState

        events.emit(
            "child_environment",
            python=sys.version,
            executable=sys.executable,
            matlab_engine_version=importlib.metadata.version("matlabengine"),
            matlab_engine_module=matlab.engine.__file__,
            pyside6_version=importlib.metadata.version("PySide6"),
            manager_module=__import__("logic.matlab_engine_manager", fromlist=["x"]).__file__,
        )
        app = QCoreApplication([])
        manager_logger.setLevel(logging.INFO)

        class ManagerLogHandler(logging.Handler):
            @override
            def emit(self, record: logging.LogRecord) -> None:
                events.emit("manager_log", level=record.levelname, logger=record.name, message=record.getMessage())

        handler = ManagerLogHandler()
        manager_logger.addHandler(handler)
        manager = MatlabEngineManager(
            available=True,
            start_engine=matlab.engine.start_matlab,
            prewarm_delay_ms=0,
            poll_interval_ms=150,
        )
        manager.state_changed.connect(lambda state: events.emit("manager_state_changed", state=state.name))

        sys.setprofile(profile)
        threading.setprofile(profile)
        events.emit("prewarm_requested")
        manager.prewarm()

        startup_future = manager._future
        future_pending = record_startup_state(events, manager.state.name, startup_future)
        if manager.state is not MatlabEngineState.STARTING or not future_pending:
            events.emit("startup_precondition_failed", manager_state=manager.state.name, future_pending=future_pending)
            shutdown_started_ns = time.monotonic_ns()
            manager.shutdown()
            worker = manager._startup_disposal_thread
            if worker is not None:
                events.emit("disposal_thread_observed", worker_name=worker.name, daemon=worker.daemon)
                if wait_for_disposer(events, worker, shutdown_started_ns=shutdown_started_ns):
                    events.emit("operator_review_required", reason="disposal worker exceeded 150 seconds")
                    threading.Event().wait()
            exit_code = 3
        else:
            shutdown_started_ns = time.monotonic_ns()
            events.emit("manager_shutdown_requested", manager_state_before=manager.state.name, future_pending=True)
            manager.shutdown()
            events.emit(
                "manager_shutdown_returned",
                duration_ns=time.monotonic_ns() - shutdown_started_ns,
                manager_state_after=manager.state.name,
            )
            worker = manager._startup_disposal_thread
            events.emit(
                "disposal_thread_observed",
                worker_exists=worker is not None,
                worker_name=worker.name if worker is not None else None,
                daemon=worker.daemon if worker is not None else None,
                alive=worker.is_alive() if worker is not None else False,
                ident=worker.ident if worker is not None else None,
            )
            if worker is None:
                events.emit("disposal_thread_missing")
                exit_code = 4
            else:
                still_alive = wait_for_disposer(events, worker, shutdown_started_ns=shutdown_started_ns)
                if still_alive:
                    events.emit("operator_review_required", reason="disposal worker exceeded 150 seconds")
                    # Keep the child alive; the independent supervisor reports it and never kills it.
                    threading.Event().wait()

        # Keep a live Qt application object through the full manager/disposer lifetime.
        _ = app
    except BaseException as exc:  # noqa: BLE001 - record all unexpected child exits as nonzero.
        exit_code = record_child_exception(events, exc, traceback.format_exc())
        traceback.print_exc(file=sys.stderr)
        if worker is None and manager is not None:
            worker = getattr(manager, "_startup_disposal_thread", None)
        if (
            worker is not None
            and worker.is_alive()
            and shutdown_started_ns is not None
            and wait_for_disposer(events, worker, shutdown_started_ns=shutdown_started_ns)
        ):
            events.emit("operator_review_required", reason="child failure; disposer exceeded deadline")
            threading.Event().wait()
    finally:
        if handler is not None:
            manager_logger.removeHandler(handler)
        sys.setprofile(None)
        threading.setprofile(None)
        io_errors = events.close()
        if io_errors:
            print(f"diagnostic JSONL writer failed: {io_errors}", file=sys.stderr)
            exit_code = max(exit_code, 5)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
