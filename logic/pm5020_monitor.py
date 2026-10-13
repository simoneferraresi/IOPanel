"""Serialized PM5020 monitoring worker and display-unit helpers."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence

from PySide6.QtCore import QTimer, Signal, Slot

from hardware.dummy_pm5020 import DummyPM5020
from hardware.interfaces import AbstractPM5020
from hardware.pm5020 import TLPMXPM5020
from hardware.pm5020_types import ChannelConfig, PM5020State, PowerChannel
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.pm5020_monitor")

DISPLAY_UNITS = ("W", "mW", "µW", "nW", "dBm")
_UNIT_SCALE = {"W": 1.0, "mW": 1e3, "µW": 1e6, "nW": 1e9}


def convert_power(power_w: float, unit: str) -> float | None:
    """Convert raw watts for presentation; nonpositive values have no dBm value."""
    try:
        value = float(power_w)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or unit not in DISPLAY_UNITS:
        return None
    if unit == "dBm":
        return 10.0 * math.log10(value / 1e-3) if value > 0 else None
    return value * _UNIT_SCALE[unit]


def format_power(power_w: float, unit: str) -> str:
    value = convert_power(power_w, unit)
    if value is None:
        return "—"
    if unit == "dBm":
        return f"{value:.3f} dBm"
    magnitude = abs(value)
    precision = 3 if magnitude == 0 or magnitude >= 0.01 else 4
    return f"{value:.{precision}g} {unit}"


class PM5020MonitorWorker(BaseWorker):
    """Long-lived service worker; the backend is created and used only in its thread."""

    instrument_state = Signal(object)
    activity_state = Signal(str)
    reading_ready = Signal(object, float)
    error_occurred = Signal(str)
    shutdown_complete = Signal()

    def __init__(
        self,
        *,
        physical_factory: Callable[[], AbstractPM5020] = TLPMXPM5020,
        simulator_factory: Callable[[], AbstractPM5020] = DummyPM5020,
    ) -> None:
        super().__init__()
        self._physical_factory = physical_factory
        self._simulator_factory = simulator_factory
        self._backend: AbstractPM5020 | None = None
        self._timer: QTimer | None = None
        self._monitoring = False
        self._paused = False
        self._poll_in_progress = False
        self._monitor_started_at = 0.0
        self._channels = (PowerChannel.CH1, PowerChannel.CH2)

    @Slot()
    def initialize(self) -> None:
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._poll)
        self.instrument_state.emit(PM5020State.DISCONNECTED)
        self.activity_state.emit("Disconnected" if not self._connected() else "Recovery required")

    @Slot(str, str)
    def connect_instrument(self, mode: str, resource_name: str) -> None:
        if self._backend is not None and self._backend.is_connected():
            self.error_occurred.emit("Disconnect the current PM5020 before connecting again.")
            return
        if mode not in {"Simulator", "Physical PM5020"}:
            self.error_occurred.emit("Select Simulator or Physical PM5020.")
            return
        if mode == "Physical PM5020" and not resource_name.strip():
            self.error_occurred.emit("Enter the PM5020 USB/VISA resource identifier.")
            return
        self.activity_state.emit("Connecting")
        try:
            factory = self._simulator_factory if mode == "Simulator" else self._physical_factory
            self._backend = factory()
            self._backend.connect(None if mode == "Simulator" else resource_name.strip())
            self._publish_state()
            self.activity_state.emit("Connected / idle")
        except Exception as exc:  # Vendor wrappers may raise types outside the backend hierarchy.
            logger.exception("PM5020 connection failed")
            self._publish_state()
            state = PM5020State.DISCONNECTED if self._backend is None else self._backend.state
            self.activity_state.emit("Recovery required" if state is PM5020State.ERROR else "Disconnected")
            self.error_occurred.emit(f"PM5020 connection failed: {exc}")

    @Slot(object)
    def configure_channels(self, channel_configs: Sequence[tuple[PowerChannel, ChannelConfig]]) -> None:
        backend = self._ready_backend()
        if backend is None:
            return
        try:
            for channel, config in channel_configs:
                backend.configure_channel(PowerChannel(channel), config)
                self._publish_state()
            self.activity_state.emit("Connected / idle")
        except Exception as exc:
            logger.exception("PM5020 channel configuration failed")
            self._publish_state()
            self.error_occurred.emit(f"Channel configuration failed: {exc}")

    @Slot(object)
    def set_channels(self, channels: Sequence[PowerChannel]) -> None:
        self._channels = tuple(PowerChannel(channel) for channel in channels)

    @Slot(int)
    def start_monitoring(self, interval_ms: int) -> None:
        if self._ready_backend() is None or self._timer is None:
            return
        if not self._channels:
            self.error_occurred.emit("Enable at least one channel for monitoring.")
            return
        self._monitoring = True
        self._paused = False
        self._monitor_started_at = time.monotonic()
        self._timer.start(max(250, min(5000, int(interval_ms))))
        self.activity_state.emit("Monitoring")
        self._poll()

    @Slot()
    def pause_monitoring(self) -> None:
        if not self._monitoring or self._timer is None:
            return
        self._timer.stop()
        self._paused = True
        self.activity_state.emit("Paused")

    @Slot(int)
    def resume_monitoring(self, interval_ms: int) -> None:
        if not self._monitoring or not self._paused or self._timer is None:
            return
        if self._ready_backend() is None:
            return
        self._paused = False
        self._timer.start(max(250, min(5000, int(interval_ms))))
        self.activity_state.emit("Monitoring")
        self._poll()

    @Slot()
    def stop_monitoring(self) -> None:
        self._stop_timer()
        self.activity_state.emit("Connected / idle" if self._connected() else "Disconnected")

    @Slot()
    def disconnect_instrument(self) -> None:
        self._stop_timer()
        if self._backend is not None:
            self.activity_state.emit("Disconnecting")
            try:
                self._backend.close()
            except Exception as exc:
                logger.exception("PM5020 disconnect failed")
                self.error_occurred.emit(f"PM5020 disconnect failed: {exc}")
            self._publish_state()
        self.activity_state.emit("Disconnected" if not self._connected() else "Recovery required")

    @Slot()
    def shutdown(self) -> None:
        self._stop_timer()
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception:
                logger.exception("PM5020 close failed during application shutdown")
        self._publish_state()
        self.shutdown_complete.emit()
        self.finished.emit()

    @Slot()
    def _poll(self) -> None:
        if self._poll_in_progress or not self._monitoring or self._paused:
            return
        backend = self._ready_backend()
        if backend is None:
            return
        self._poll_in_progress = True
        self.activity_state.emit("Busy with acquisition")
        try:
            for channel in self._channels:
                if not self._monitoring or self._paused or not backend.is_ready:
                    break
                reading = backend.read_power(channel)
                elapsed = time.monotonic() - self._monitor_started_at
                self.reading_ready.emit(reading, elapsed)
                self._publish_state()
            if self._monitoring and not self._paused:
                self.activity_state.emit("Monitoring")
        except Exception as exc:
            logger.exception("PM5020 monitoring read failed")
            self._stop_timer()
            self._publish_state()
            if backend.state is not PM5020State.ERROR:
                self.activity_state.emit("Connected / idle")
            self.error_occurred.emit(f"PM5020 measurement failed: {exc}")
        finally:
            self._poll_in_progress = False

    def _ready_backend(self) -> AbstractPM5020 | None:
        backend = self._backend
        if backend is None or not backend.is_connected():
            self.error_occurred.emit("Connect to a PM5020 before requesting this operation.")
            return None
        self._publish_state()
        if not backend.is_ready:
            self._stop_timer()
            self.activity_state.emit("Recovery required" if backend.state is PM5020State.ERROR else "Busy")
            if backend.state is PM5020State.ERROR:
                self.error_occurred.emit("The PM5020 requires recovery. Disconnect it, then reconnect deliberately.")
            return None
        return backend

    def _connected(self) -> bool:
        return self._backend is not None and self._backend.is_connected()

    def _stop_timer(self) -> None:
        self._monitoring = False
        self._paused = False
        if self._timer is not None:
            self._timer.stop()

    def _publish_state(self) -> None:
        state = PM5020State.DISCONNECTED if self._backend is None else self._backend.state
        self.instrument_state.emit(state)
        if state is PM5020State.ERROR:
            self._stop_timer()
            self.activity_state.emit("Recovery required")
