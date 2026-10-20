"""Read-only EDFA service worker. The backend is owned by the worker's Qt thread."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal, Slot

from hardware.dummy_edfa import DummyEDFA
from hardware.edfa_types import EDFAState
from hardware.interfaces import AbstractEDFA
from hardware.ipg_ead2kc import IPGEAD2KCGPIB
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.edfa_monitor")


class EDFAMonitorWorker(BaseWorker):
    state_changed = Signal(object, str)
    identity_ready = Signal(str)
    telemetry_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(
        self,
        *,
        simulator_factory: Callable[[], AbstractEDFA] = DummyEDFA,
        physical_factory: Callable[[str, int, str, str], AbstractEDFA] | None = None,
    ) -> None:
        super().__init__()
        self._simulator_factory = simulator_factory
        self._physical_factory = physical_factory
        self._backend: AbstractEDFA | None = None
        self._timer: QTimer | None = None
        self._monitoring = False

    @Slot()
    def initialize(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._read_once)
        self.state_changed.emit(EDFAState.DISCONNECTED, "Disconnected")

    @Slot(str, str, int, str, str)
    def connect_instrument(
        self, mode: str, resource: str, timeout_ms: int, write_termination: str, read_termination: str
    ) -> None:
        if self._backend is not None and self._backend.state is not EDFAState.DISCONNECTED:
            self.error_occurred.emit("Disconnect the current EDFA first.")
            self._publish()
            return
        self.state_changed.emit(EDFAState.DISCONNECTED, "Connecting")
        try:
            if mode == "simulation":
                backend = self._simulator_factory()
            elif mode == "physical":
                if not resource:
                    raise ValueError("Configure a specific EDFA GPIB address.")
                backend = (
                    self._physical_factory(resource, timeout_ms, write_termination, read_termination)
                    if self._physical_factory is not None
                    else IPGEAD2KCGPIB(
                        resource, timeout_ms=timeout_ms,
                        write_termination=write_termination, read_termination=read_termination
                    )
                )
            else:
                raise ValueError("Unsupported EDFA backend mode")
            self._backend = backend
            backend.connect()
            self.identity_ready.emit(backend.identify())
            self.state_changed.emit(EDFAState.IDLE, "Connected / read-only")
        except Exception as exc:
            logger.exception("EDFA connection failed")
            self.error_occurred.emit(f"EDFA connection failed: {exc}")
            if self._backend is not None:
                try:
                    self._backend.close()
                except Exception:
                    logger.exception("EDFA cleanup after failed connect")
            self._publish()

    @Slot()
    def read_once(self) -> None:
        if self._backend is None or self._backend.state is not EDFAState.IDLE:
            self.error_occurred.emit("Connect the EDFA before reading telemetry.")
            self._publish()
            return
        self._read_once()

    @Slot()
    def _read_once(self) -> None:
        backend = self._backend
        if backend is None or backend.state is not EDFAState.IDLE:
            self.stop_monitoring()
            return
        try:
            self.telemetry_ready.emit(backend.read_telemetry())
        except Exception as exc:
            logger.exception("EDFA telemetry failed")
            self.stop_monitoring()
            self.error_occurred.emit(f"EDFA read-only telemetry failed: {exc}")
            self._publish()

    @Slot(int)
    def start_monitoring(self, interval_ms: int) -> None:
        if self._timer is None or self._backend is None or self._backend.state is not EDFAState.IDLE:
            self.error_occurred.emit("Connect the EDFA before monitoring.")
            self._publish()
            return
        if not 2000 <= interval_ms <= 10000:
            self.error_occurred.emit("EDFA interval must be between 2000 and 10000 ms.")
            self._publish()
            return
        self._monitoring = True
        self._timer.start(interval_ms)
        self.state_changed.emit(EDFAState.IDLE, "Monitoring (read-only)")
        self._read_once()

    @Slot()
    def stop_monitoring(self) -> None:
        self._monitoring = False
        if self._timer is not None:
            self._timer.stop()
        self._publish()

    @Slot()
    def disconnect_instrument(self) -> None:
        self._monitoring = False
        if self._timer is not None:
            self._timer.stop()
        self.state_changed.emit(self._state(), "Disconnecting")
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception as exc:
                logger.exception("EDFA VISA close failed")
                self.error_occurred.emit(f"EDFA disconnect incomplete: {exc}")
                self._publish()
                return
        self._backend = None
        self.identity_ready.emit("")
        self._publish()

    @Slot()
    def shutdown(self) -> None:
        """Finish current queued operation, then release resources in their owner thread."""
        self._monitoring = False
        if self._timer is not None:
            self._timer.stop()
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception as exc:
                logger.exception("EDFA shutdown cleanup failed")
                self.error_occurred.emit(f"EDFA shutdown cleanup unconfirmed: {exc}")
        self.finished.emit()

    def _state(self) -> EDFAState:
        return self._backend.state if self._backend is not None else EDFAState.DISCONNECTED

    def _publish(self) -> None:
        state = self._state()
        activity = {
            EDFAState.DISCONNECTED: "Disconnected",
            EDFAState.IDLE: "Monitoring (read-only)" if self._monitoring else "Connected / read-only",
            EDFAState.ERROR: "Recovery required",
        }[state]
        self.state_changed.emit(state, activity)
