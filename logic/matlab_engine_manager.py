from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
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
    SHUT_DOWN = auto()
    SHUTDOWN_FAILED = auto()


@dataclass
class _EngineDisposal:
    """Transferred native ownership; the worker never accesses a QObject."""

    future: Any = None
    engine: Any = None
    error: str | None = None
    thread: Thread | None = None


def _dispose_engine(job: _EngineDisposal) -> None:
    if job.future is not None:
        future = job.future
        try:
            cancel = getattr(future, "cancel", None)
            try:
                cancelled = bool(cancel()) if callable(cancel) else False
            except Exception:
                logger.info("MATLAB startup cancellation raised; waiting for startup disposal.", exc_info=True)
                cancelled = False
            logger.info("MATLAB startup cancellation returned %s after cancellation request.", cancelled)
            if not cancelled:
                logger.info("Waiting for in-progress MATLAB startup to finish before disposal.")
                try:
                    job.engine = future.result()
                except Exception:
                    logger.info("MATLAB startup disposal completed with exception.", exc_info=True)
        finally:
            # Future destruction also enters the vendor API. Release its final
            # reference here, never in a GUI completion callback.
            job.future = None
            del future
    if job.engine is not None:
        try:
            job.engine.quit()
        except Exception as error:
            job.error = str(error)
            logger.exception("Could not quit MATLAB Engine cleanly; ownership retained.")
        else:
            job.engine = None
            logger.info("Late MATLAB startup disposed after native cleanup.")


class MatlabEngineManager(QObject):
    """Own one optional MATLAB Engine and poll asynchronous startup on the Qt loop."""

    state_changed = Signal(object)
    ready = Signal()
    startup_failed = Signal(str)
    shutdown_complete = Signal()
    shutdown_failed = Signal(str)

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
        self._disposals: list[_EngineDisposal] = []
        self._failed_disposals: list[_EngineDisposal] = []
        self._engine_users = 0
        self._retire_engine_requested = False
        self._restart_requested = False
        self._shutdown_complete = False
        self._disposal_timer = QTimer(self)
        self._disposal_timer.setInterval(poll_interval_ms)
        self._disposal_timer.timeout.connect(self._poll_disposals)
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
        return self._engine if not self._shutdown and self._state is MatlabEngineState.READY else None

    @property
    def shutdown_finished(self) -> bool:
        return self._shutdown_complete

    def acquire_engine(self) -> Any | None:
        """GUI-thread lease, held until the export's native thread retires."""
        engine = self.engine
        if engine is not None:
            self._engine_users += 1
        return engine

    def release_engine(self) -> None:
        if self._engine_users <= 0:
            raise RuntimeError("MATLAB Engine lease released without an owner")
        self._engine_users -= 1
        if not self._engine_users and (self._shutdown or self._retire_engine_requested):
            self._dispose_owned_resources()

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
        if self._shutdown or self._failed_disposals:
            return self._state
        if self._retire_engine_requested or self._disposals:
            self._restart_requested = True
            self._set_state(MatlabEngineState.STARTING)
            return self._state
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
            self._start_disposal(_EngineDisposal(engine=engine))
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
        """Retire the unhealthy Engine after its last native user has returned."""
        if self._state is not MatlabEngineState.READY or self._shutdown:
            return
        logger.error("Shared MATLAB Engine health check failed: %s", error)
        self._retire_engine_requested = True
        self._set_state(MatlabEngineState.FAILED)
        if not self._engine_users:
            self._dispose_owned_resources()

    def _start_disposal(self, job: _EngineDisposal) -> None:
        # Required cleanup is non-daemon. MainWindow keeps Qt alive until the
        # job ends; an intrinsically blocked native call keeps close pending.
        thread = Thread(target=_dispose_engine, args=(job,), name="MatlabEngineDisposal", daemon=False)
        job.thread = thread
        self._disposals.append(job)
        self._startup_disposal_thread = thread
        try:
            thread.start()
        except RuntimeError as error:
            job.error = str(error)
            self._disposals.remove(job)
            self._failed_disposals.append(job)
            self._report_disposal_failure(job.error)
            return
        self._disposal_timer.start()

    def _dispose_owned_resources(self) -> None:
        if self._engine_users:
            return
        if self._engine is not None or self._future is not None:
            job = _EngineDisposal(future=self._future, engine=self._engine)
            self._engine = None
            self._future = None
            self._start_disposal(job)
        self._retire_engine_requested = False
        self._finish_shutdown_if_idle()

    def _poll_disposals(self) -> None:
        for job in tuple(self._disposals):
            thread = job.thread
            if thread is None or thread.is_alive():
                continue
            # No potentially blocked operation remains in this Python thread.
            thread.join(timeout=0)
            self._disposals.remove(job)
            if job.error is not None:
                self._failed_disposals.append(job)
                self._report_disposal_failure(job.error)
        if self._disposals:
            return
        self._disposal_timer.stop()
        if self._shutdown:
            self._finish_shutdown_if_idle()
        elif self._restart_requested and not self._failed_disposals:
            self._restart_requested = False
            self._set_state(MatlabEngineState.FAILED)
            self._start_async()

    def _report_disposal_failure(self, error: str) -> None:
        self._restart_requested = False
        if self._shutdown:
            self._set_state(MatlabEngineState.SHUTDOWN_FAILED)
            self.shutdown_failed.emit(error)
        else:
            self._set_state(MatlabEngineState.FAILED)
            self.startup_failed.emit(f"Could not retire the previous MATLAB Engine: {error}")

    def _finish_shutdown_if_idle(self) -> None:
        if not self._shutdown or self._shutdown_complete:
            return
        if self._failed_disposals:
            self._set_state(MatlabEngineState.SHUTDOWN_FAILED)
            return
        if self._engine_users or self._disposals or self._engine is not None or self._future is not None:
            return
        self._shutdown_complete = True
        if self._available or self._state is not MatlabEngineState.UNAVAILABLE:
            self._set_state(MatlabEngineState.SHUT_DOWN)
        self.shutdown_complete.emit()

    def shutdown(self) -> None:
        """Request cleanup; completion confirms API cleanup, not MATLAB PID absence."""
        if self._shutdown:
            return
        self._shutdown = True
        self._restart_requested = False
        if self._prewarm_timer is not None:
            self._prewarm_timer.stop()
        if self._poll_timer is not None:
            self._poll_timer.stop()
        if self._state is MatlabEngineState.STARTING:
            logger.info("MATLAB startup shutdown requested.")
        if self._state is not MatlabEngineState.UNAVAILABLE:
            self._set_state(MatlabEngineState.SHUTTING_DOWN)
        self._dispose_owned_resources()
