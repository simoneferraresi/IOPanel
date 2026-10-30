"""Launch MATLAB only after its Python bindings import off the Qt GUI thread.

This adapter returns a Future compatible with MatlabEngineManager. The manager
continues to own the engine, shutdown, retries and native release; this module
never creates a QWidget or manipulates a QObject from the worker thread.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future
from threading import Thread
from typing import Any

logger = logging.getLogger("LabApp.matlab_deferred_start")


def _import_engine_module() -> Any:
    # Retain a static import so PyInstaller sees this optional lab dependency.
    import matlab.engine  # pyright: ignore[reportMissingImports] - optional vendor SDK absent from CI.

    return matlab.engine


def start_matlab_with_deferred_import(*, background: bool = True) -> Future[Any]:
    """Perform import + native asynchronous start without blocking Qt callbacks.

    A Python Future bridges MATLAB's native future into the existing manager.
    Shutdown cannot cancel a running native start; the manager's non-daemon
    disposal worker waits for completion and quits any late engine.
    """
    if not background:
        raise ValueError("Deferred MATLAB startup requires background=True")

    result: Future[Any] = Future()

    def load_and_start() -> None:
        if not result.set_running_or_notify_cancel():
            return
        try:
            engine_module = _import_engine_module()
            logger.info("MATLAB Engine Python bindings imported on background thread.")
            native_future = engine_module.start_matlab(background=True)
            # A native Future must be waited on and dropped on this same
            # background thread; never transfer its ownership to Qt.
            engine = native_future.result()
        except Exception as error:  # noqa: BLE001 - MATLAB raises vendor-specific errors.
            logger.exception("Deferred MATLAB Engine import/start failed.")
            # Do not attach a native exception traceback to the long-lived
            # Future: it may keep SDK wrappers alive on the GUI thread.
            result.set_exception(RuntimeError(f"MATLAB Engine import/start failed: {error}"))
        else:
            result.set_result(engine)

    Thread(target=load_and_start, name="IOPanel-MATLAB-Startup", daemon=False).start()
    return result
