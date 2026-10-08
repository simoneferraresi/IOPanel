from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import Enum, auto
from threading import Thread
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("LabApp.matlab_engine")


class MatlabEngineState(Enum):
    UNAVAILABLE = auto()
    IDLE = auto()
    STARTING = auto()
    READY = auto()
    FAILED = auto()
    SHUTTING_DOWN = auto()


class MatlabEngineManager(QObject):
    """Own one optional MATLAB Engine and poll asynchronous startup on the Qt loop."""

    state_changed = Signal(object)
    ready = Signal()
    startup_failed = Signal(str)

    PREWARM_DELAY_MS = 350
    POLL_INTERVAL_MS = 150

    def __init__(
        self,
        *,
        available: bool,
        start_engine: Callable[..., Any] | None,
        parent: QObject | None = None,
        prewarm_delay_ms: int = PREWARM_DELAY_MS,
        poll_interval_ms: int = POLL_INTERVAL_MS,
    ) -> None:
        super().__init__(parent)
        self._available = available and start_engine is not None
        self._start_engine = start_engine
        self._state = MatlabEngineState.IDLE if self._available else MatlabEngineState.UNAVAILABLE
        self._engine: Any | None = None
        self._future: Any | None = None
        self._started_at: float | None = None
        self._shutdown = False
        self._startup_disposal_thread: Thread | None = None
        self._prewarm_timer: QTimer | None = None
        self._poll_timer: QTimer | None = None

        if self._available:
            self._prewarm_timer = QTimer(self)
            self._prewarm_timer.setSingleShot(True)
            self._prewarm_timer.setInterval(prewarm_delay_ms)
            self._prewarm_timer.timeout.connect(self.prewarm)
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(poll_interval_ms)
            self._poll_timer.timeout.connect(self._poll_startup)

    @property
    def state(self) -> MatlabEngineState:
        return self._state

    @property
    def engine(self) -> Any | None:
        return self._engine

    @property
    def available(self) -> bool:
        return self._available

    @property
    def prewarm_timer_active(self) -> bool:
        return self._prewarm_timer is not None and self._prewarm_timer.isActive()

    @property
    def polling(self) -> bool:
        return self._poll_timer is not None and self._poll_timer.isActive()

    def _set_state(self, state: MatlabEngineState) -> None:
        if self._state is state:
            return
        self._state = state
        self.state_changed.emit(state)

    def schedule_prewarm(self) -> None:
        if self._state is MatlabEngineState.IDLE and self._prewarm_timer is not None:
            logger.info("Scheduling MATLAB Engine prewarm after %d ms.", self._prewarm_timer.interval())
            self._prewarm_timer.start()

    def prewarm(self) -> None:
        """Start once after the GUI has been shown; background failure is non-modal."""
        if self._state is not MatlabEngineState.IDLE:
            return
        self._start_async()

    def request_engine(self) -> MatlabEngineState:
        """Ensure startup is underway, retrying FAILED only after an explicit request."""
        if self._state in (MatlabEngineState.IDLE, MatlabEngineState.FAILED):
            if self._prewarm_timer is not None:
                self._prewarm_timer.stop()
            self._start_async()
        return self._state

    def _start_async(self) -> None:
        if (
            self._shutdown
            or not self._available
            or self._state
            in (
                MatlabEngineState.STARTING,
                MatlabEngineState.READY,
                MatlabEngineState.SHUTTING_DOWN,
                MatlabEngineState.UNAVAILABLE,
            )
        ):
            return

        assert self._start_engine is not None
        self._set_state(MatlabEngineState.STARTING)
        self._started_at = time.perf_counter()
        logger.info("MATLAB prewarm requested.")
        try:
            self._future = self._start_engine(background=True)
        except Exception as error:  # noqa: BLE001 - MATLAB startup can raise package-specific exceptions.
            self._fail_startup(error)
            return
        if self._poll_timer is not None:
            self._poll_timer.start()

    def _poll_startup(self) -> None:
        future = self._future
        if future is None or self._state is not MatlabEngineState.STARTING:
            if self._poll_timer is not None:
                self._poll_timer.stop()
            return

        try:
            if not future.done():
                return
            engine = future.result()
        except Exception as error:  # noqa: BLE001 - FutureResult exposes MATLAB-specific exceptions.
            if self._poll_timer is not None:
                self._poll_timer.stop()
            self._future = None
            if self._shutdown:
                return
            self._fail_startup(error)
            return

        if self._poll_timer is not None:
            self._poll_timer.stop()
        self._future = None
        if self._shutdown or self._state is MatlabEngineState.SHUTTING_DOWN:
            self._quit_engine(engine)
            return

        self._engine = engine
        elapsed = time.perf_counter() - (self._started_at or time.perf_counter())
        logger.info("MATLAB READY after %.2f s.", elapsed)
        self._set_state(MatlabEngineState.READY)
        self.ready.emit()

    def _fail_startup(self, error: Exception) -> None:
        self._engine = None
        self._future = None
        logger.error("MATLAB Engine startup failed: %s", error)
        self._set_state(MatlabEngineState.FAILED)
        self.startup_failed.emit(str(error))

    def engine_unresponsive(self, error: Exception) -> None:
        """Drop an unhealthy shared Engine and allow the requesting FIG save to retry it."""
        if self._state is not MatlabEngineState.READY or self._shutdown:
            return
        failed_engine = self._engine
        self._engine = None
        self._set_state(MatlabEngineState.FAILED)
        logger.error("Shared MATLAB Engine health check failed: %s", error)
        if failed_engine is not None:
            Thread(target=self._quit_engine, args=(failed_engine,), daemon=True).start()

    @staticmethod
    def _quit_engine(engine: Any) -> None:
        try:
            engine.quit()
        except Exception:
            logger.warning("Could not quit MATLAB Engine cleanly.", exc_info=True)

    def _dispose_startup_future(self, future: Any, shutdown_requested_at: float) -> None:
        """Dispose of an in-progress startup without blocking Qt or Python exit."""
        cancel_started_at = time.perf_counter()
        cancel = getattr(future, "cancel", None)
        try:
            cancelled = bool(cancel()) if callable(cancel) else False
        except Exception:
            logger.info("MATLAB startup cancellation raised; waiting for startup disposal.", exc_info=True)
            cancelled = False
        logger.info(
            "MATLAB startup cancellation returned %s after %.2f s.",
            cancelled,
            time.perf_counter() - cancel_started_at,
        )
        if cancelled:
            return

        logger.info("Waiting for in-progress MATLAB startup to finish before disposal.")
        try:
            engine = future.result()
        except Exception as error:
            logger.info(
                "MATLAB startup disposal completed with exception after %.2f s: %s",
                time.perf_counter() - shutdown_requested_at,
                error,
                exc_info=True,
            )
            return
        if engine is not None:
            self._quit_engine(engine)
        logger.info("Late MATLAB startup disposed after %.2f s.", time.perf_counter() - shutdown_requested_at)

    def shutdown(self) -> None:
        if self._shutdown:
            return
        shutdown_requested_at = time.perf_counter()
        self._shutdown = True
        if self._prewarm_timer is not None:
            self._prewarm_timer.stop()
        if self._poll_timer is not None:
            self._poll_timer.stop()

        if self._state is MatlabEngineState.UNAVAILABLE:
            return

        was_starting = self._state is MatlabEngineState.STARTING
        engine, self._engine = self._engine, None
        future, self._future = self._future, None
        if was_starting:
            logger.info("MATLAB startup shutdown requested.")
        self._set_state(MatlabEngineState.SHUTTING_DOWN)
        if engine is not None:
            self._quit_engine(engine)
        if future is not None:
            disposal_thread = Thread(
                target=self._dispose_startup_future,
                args=(future, shutdown_requested_at),
                name="MatlabEngineStartupDisposal",
                # Do not let an unresolvable FutureResult hold Python exit forever; if it resolves while
                # the process remains alive, this worker acquires and quits the late Engine.
                daemon=True,
            )
            self._startup_disposal_thread = disposal_thread
            try:
                disposal_thread.start()
            except RuntimeError:
                logger.exception("Could not start MATLAB startup disposal worker.")
