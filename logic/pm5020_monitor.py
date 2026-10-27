"""Serialized PM5020 monitoring worker and display-unit helpers."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QTimer, Signal, Slot

from hardware.dummy_pm5020 import DummyPM5020
from hardware.interfaces import AbstractPM5020
from hardware.pm5020 import TLPMXPM5020
from hardware.pm5020_types import ChannelConfig, PM5020State, PowerChannel
from logic.device_lease_registry import (
    DeviceLease,
    DeviceLeaseRegistry,
    DeviceRecoveryRequired,
    DeviceResource,
    LeaseState,
)
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.pm5020_monitor")


@dataclass(frozen=True)
class ConfigurationRequest:
    generation: int
    request_id: int
    settings_revision: int
    channel_configs: tuple[tuple[PowerChannel, ChannelConfig], ...]


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
    configuration_result = Signal(int, int, int, bool, str)
    monitoring_started = Signal(int, int)
    monitor_interval_result = Signal(int, int, int, bool, str)

    def __init__(
        self,
        *,
        physical_factory: Callable[[], AbstractPM5020] | None = None,
        simulator_factory: Callable[[], AbstractPM5020] = DummyPM5020,
        wrapper_path: str | None = None,
        dll_path: str | None = None,
        lease_registry: DeviceLeaseRegistry | None = None,
    ) -> None:
        super().__init__()
        self._physical_factory = physical_factory
        self._simulator_factory = simulator_factory
        self._wrapper_path = wrapper_path
        self._dll_path = dll_path
        self._lease_registry = lease_registry if lease_registry is not None else DeviceLeaseRegistry()
        self._connection_lease: DeviceLease | None = None
        self._backend: AbstractPM5020 | None = None
        self._timer: QTimer | None = None
        self._monitoring = False
        self._paused = False
        self._poll_in_progress = False
        self._monitor_started_at = 0.0
        self._channels = (PowerChannel.CH1, PowerChannel.CH2)
        self._connection_generation = 0
        self._monitor_session_id = 0

    def bind_lease_registry(self, registry: DeviceLeaseRegistry) -> None:
        """Bind MainWindow registry before starting this owner thread."""
        if self._backend is not None or self._timer is not None or self._connection_lease is not None:
            raise RuntimeError("Cannot change registry after PM5020 worker initialization")
        self._lease_registry = registry

    def _release_connection_lease(self) -> None:
        lease = self._connection_lease
        if lease is not None:
            self._lease_registry.release(lease, operations_complete=True)
            self._connection_lease = None

    def _quarantine_connection_lease(self, reason: str) -> None:
        if self._connection_lease is not None:
            self._lease_registry.mark_uncertain(self._connection_lease, reason)

    @Slot()
    def initialize(self) -> None:
        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._poll)
        self.instrument_state.emit(PM5020State.DISCONNECTED)
        self.activity_state.emit("Disconnected" if not self._connected() else "Recovery required")

    @Slot(str, str, int)
    def connect_instrument(self, mode: str, resource_name: str, generation: int) -> None:
        self._connection_generation = generation
        self.activity_state.emit("Connecting")
        if self._backend is not None and self._backend.is_connected():
            self._publish_state()
            self.activity_state.emit(self._activity_for_state())
            self.error_occurred.emit("Disconnect the current PM5020 before connecting again.")
            return
        if mode not in {"Simulator", "Physical PM5020"}:
            self._publish_state()
            self.activity_state.emit(self._activity_for_state())
            self.error_occurred.emit("Select Simulator or Physical PM5020.")
            return
        if mode == "Physical PM5020" and not resource_name.strip():
            self._publish_state()
            self.activity_state.emit(self._activity_for_state())
            self.error_occurred.emit("Enter the PM5020 USB/VISA resource identifier.")
            return
        try:
            if self._connection_lease is None:
                self._connection_lease = self._lease_registry.acquire(
                    "pm5020-owner-worker", (DeviceResource.PM5020,)
                )
            elif any(item.resource is DeviceResource.PM5020 and item.state is LeaseState.UNCERTAIN
                     for item in self._lease_registry.snapshot()):
                raise DeviceRecoveryRequired("PM5020 is quarantined until explicit recovery")
            if mode == "Simulator":
                self._backend = self._simulator_factory()
                self._backend.connect(None)
            elif self._physical_factory is not None:
                self._backend = self._physical_factory()
                self._backend.connect(resource_name.strip())
            else:
                self._backend = TLPMXPM5020(wrapper_path=self._wrapper_path, dll_path=self._dll_path)
                self._backend.connect(resource_name.strip())
            self._publish_state()
            self.activity_state.emit("Connected / idle")
        except Exception as exc:  # Vendor wrappers may raise types outside the backend hierarchy.
            logger.exception("PM5020 connection failed")
            try:
                if self._backend is None or not self._backend.is_connected():
                    self._release_connection_lease()
                else:
                    self._quarantine_connection_lease(f"PM5020 connection failed: {exc}")
            except DeviceRecoveryRequired:
                pass
            except Exception:  # noqa: BLE001 - cannot trust vendor/native close state
                self._quarantine_connection_lease("PM5020 connection state could not be verified")
            self._publish_state()
            state = PM5020State.DISCONNECTED if self._backend is None else self._backend.state
            self.activity_state.emit("Recovery required" if state is PM5020State.ERROR else "Disconnected")
            self.error_occurred.emit(f"PM5020 connection failed: {exc}")

    @Slot(object)
    def configure_channels(self, request: ConfigurationRequest) -> None:
        if request.generation != self._connection_generation:
            return
        backend = self._ready_backend()
        if backend is None:
            self.configuration_result.emit(
                request.generation, request.request_id, request.settings_revision, False, "PM5020 is not ready."
            )
            return
        try:
            for channel, config in request.channel_configs:
                backend.configure_channel(PowerChannel(channel), config)
                self._publish_state()
            self.activity_state.emit("Connected / idle")
            self.configuration_result.emit(request.generation, request.request_id, request.settings_revision, True, "")
        except Exception as exc:
            logger.exception("PM5020 channel configuration failed")
            self._publish_state()
            self.configuration_result.emit(
                request.generation, request.request_id, request.settings_revision, False, str(exc)
            )
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
        self._monitor_session_id += 1
        self._timer.start(max(250, min(5000, int(interval_ms))))
        self.monitoring_started.emit(self._connection_generation, self._monitor_session_id)
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

    @Slot(int, int, int, int)
    def update_monitor_interval(self, generation: int, session_id: int, request_id: int, interval_ms: int) -> None:
        """Update the active worker timer without restarting its monitoring session."""
        if not 250 <= interval_ms <= 5000:
            self.monitor_interval_result.emit(
                generation, session_id, request_id, False, "Update interval must be between 250 and 5000 ms."
            )
            return
        if (
            generation != self._connection_generation
            or session_id != self._monitor_session_id
            or not self._monitoring
            or self._paused
            or self._timer is None
            or self._ready_backend() is None
        ):
            self.monitor_interval_result.emit(
                generation, session_id, request_id, False, "The monitoring session is no longer active."
            )
            return
        self._timer.setInterval(interval_ms)
        self.monitor_interval_result.emit(generation, session_id, request_id, True, "")

    @Slot()
    def stop_monitoring(self) -> None:
        self._stop_timer()
        self.activity_state.emit(self._activity_for_state())

    @Slot(int)
    def disconnect_instrument(self, generation: int) -> None:
        self._connection_generation = generation
        self._stop_timer()
        if self._backend is not None:
            self.activity_state.emit("Disconnecting")
            try:
                self._backend.close()
            except Exception as exc:
                logger.exception("PM5020 disconnect failed")
                self._quarantine_connection_lease(f"PM5020 disconnect failed: {exc}")
                self.error_occurred.emit(f"PM5020 disconnect failed: {exc}")
            self._publish_state()
        try:
            if not self._connected():
                self._release_connection_lease()
            else:
                self._quarantine_connection_lease("PM5020 disconnect is not confirmed")
        except DeviceRecoveryRequired:
            pass
        except Exception:  # noqa: BLE001 - cannot trust vendor/native close state
            self._quarantine_connection_lease("PM5020 disconnect state could not be verified")
        self.activity_state.emit("Disconnected" if not self._connected() else "Recovery required")

    @Slot()
    def shutdown(self) -> None:
        self._stop_timer()
        if self._backend is not None:
            try:
                self._backend.close()
            except Exception as exc:
                logger.exception("PM5020 close failed during application shutdown")
                self._quarantine_connection_lease(f"PM5020 shutdown close failed: {exc}")
        try:
            if not self._connected():
                self._release_connection_lease()
            else:
                self._quarantine_connection_lease("PM5020 remained connected during shutdown")
        except DeviceRecoveryRequired:
            pass
        except Exception:
            logger.exception("PM5020 shutdown state could not be verified")
            self._quarantine_connection_lease("PM5020 shutdown state could not be verified")
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
        if self._connection_lease is None:
            self.error_occurred.emit("PM5020 has no owner lease; refusing device access.")
            return None
        if any(item.resource is DeviceResource.PM5020 and item.state is LeaseState.UNCERTAIN
               for item in self._lease_registry.snapshot()):
            self.error_occurred.emit("PM5020 ownership is uncertain; refusing device access.")
            return None
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

    def _activity_for_state(self) -> str:
        state = PM5020State.DISCONNECTED if self._backend is None else self._backend.state
        return {
            PM5020State.DISCONNECTED: "Disconnected",
            PM5020State.IDLE: "Connected / idle",
            PM5020State.ERROR: "Recovery required",
            PM5020State.ARMED: "Busy with acquisition",
            PM5020State.RETRIEVING: "Busy with acquisition",
        }[state]

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
