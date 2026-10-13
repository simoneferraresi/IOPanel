"""Deterministic, driver-free PM5020 simulator."""

from __future__ import annotations

import logging
import math
import threading
from datetime import UTC, datetime
from threading import Event
from typing import override

from hardware.interfaces import AbstractPM5020
from hardware.pm5020 import PM5020AcquisitionError, PM5020Cancelled, PM5020CommunicationError, PM5020StateError
from hardware.pm5020_types import BurstAcquisitionConfig, BurstPowerData, ChannelConfig, PowerChannel, PowerReading

logger = logging.getLogger("LabApp.DummyPM5020")


class DummyPM5020(AbstractPM5020):
    """Repeatable PM5020 behavior for offline use and automated tests."""

    def __init__(
        self,
        *,
        power_levels_w: dict[PowerChannel, float] | None = None,
        burst_sample_count: int = 257,
        available_channels: set[PowerChannel] | None = None,
        fail_operations: set[str] | None = None,
    ):
        if burst_sample_count < 0:
            raise ValueError("burst_sample_count must be non-negative")
        levels = power_levels_w or {PowerChannel.CH1: 0.01, PowerChannel.CH2: 8e-7}
        self._power_levels = {PowerChannel(channel): float(value) for channel, value in levels.items()}
        if any(not math.isfinite(value) for value in self._power_levels.values()):
            raise ValueError("simulated power levels must be finite watts values")
        self._available_channels = set(available_channels or (PowerChannel.CH1, PowerChannel.CH2))
        self._burst_sample_count = burst_sample_count
        self._fail_operations = set(fail_operations or ())
        self._connected = False
        self._state = "disconnected"
        self._channel_configs: dict[PowerChannel, ChannelConfig] = {}
        self._burst_config: BurstAcquisitionConfig | None = None
        self._lock = threading.Lock()

    @override
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    @override
    def connect(self, resource_name: str | None = None) -> None:
        if resource_name is not None and (not resource_name.strip() or "\0" in resource_name):
            raise ValueError("resource_name must be non-empty and cannot contain NUL")
        with self._lock:
            if self._connected:
                raise PM5020StateError("PM5020 simulator is already connected")
            self._maybe_fail("connect")
            self._connected = True
            self._state = "idle"
            self._channel_configs.clear()
            self._burst_config = None
            logger.info("Connected to deterministic PM5020 simulator")

    @override
    def configure_channel(self, channel: PowerChannel, config: ChannelConfig) -> None:
        channel = PowerChannel(channel)
        with self._lock:
            self._require_idle()
            self._require_sensor(channel)
            self._maybe_fail("configure_channel")
            self._channel_configs[channel] = config

    @override
    def read_power(self, channel: PowerChannel) -> PowerReading:
        channel = PowerChannel(channel)
        with self._lock:
            self._require_idle()
            self._require_sensor(channel)
            self._maybe_fail("read_power")
            if channel not in self._channel_configs:
                self._channel_configs[channel] = ChannelConfig()
            return PowerReading(channel, self._power_levels[channel], datetime.now(UTC))

    def set_power_level(self, channel: PowerChannel, power_w: float) -> None:
        """Change a simulated optical input level, in watts."""
        channel = PowerChannel(channel)
        value = float(power_w)
        if not math.isfinite(value):
            raise ValueError("power_w must be finite")
        with self._lock:
            self._power_levels[channel] = value

    @override
    def configure_burst(self, config: BurstAcquisitionConfig) -> None:
        if set(config.channels) != {PowerChannel.CH1, PowerChannel.CH2}:
            raise ValueError("PM5020 burst acquisition must configure both CH1 and CH2")
        with self._lock:
            self._require_idle()
            for channel in config.channels:
                self._require_sensor(channel)
            self._maybe_fail("configure_burst")
            self._burst_config = config

    @override
    def start_burst(self) -> None:
        with self._lock:
            self._require_idle()
            if self._burst_config is None:
                raise PM5020StateError("Configure burst acquisition before arming it")
            self._maybe_fail("start_burst")
            self._state = "armed"

    @override
    def retrieve_burst(
        self,
        *,
        max_samples: int = 15_000,
        cancel_event: Event | None = None,
    ) -> BurstPowerData:
        if not 1 <= max_samples <= 15_000:
            raise ValueError("max_samples must be between 1 and 15000")
        with self._lock:
            if not self._connected:
                raise PM5020StateError("PM5020 simulator is not connected")
            if self._state != "armed" or self._burst_config is None:
                raise PM5020StateError("PM5020 simulator burst acquisition is not armed")
            config = self._burst_config
            if max_samples > config.max_samples:
                raise ValueError("max_samples cannot exceed the configured burst capacity")
            self._state = "retrieving"
            try:
                self._maybe_fail("retrieve_burst")
                if cancel_event is not None and cancel_event.is_set():
                    raise PM5020Cancelled("PM5020 simulator burst retrieval cancelled")
                if self._burst_sample_count == 0:
                    raise PM5020AcquisitionError("PM5020 simulator has zero burst samples")
                if self._burst_sample_count > max_samples:
                    raise PM5020AcquisitionError(
                        f"PM5020 simulator has {self._burst_sample_count} samples, exceeding capacity {max_samples}"
                    )

                timestamps: list[int] = []
                ch1: list[float] = []
                ch2: list[float] = []
                for start in range(0, self._burst_sample_count, config.chunk_size):
                    if cancel_event is not None and cancel_event.is_set():
                        raise PM5020Cancelled(f"PM5020 simulator retrieval cancelled after {len(timestamps)} samples")
                    end = min(start + config.chunk_size, self._burst_sample_count)
                    for index in range(start, end):
                        timestamps.append(index * 1000)
                        modulation = 1.0 + 0.02 * math.sin(index / 13.0)
                        ch1.append(self._power_levels[PowerChannel.CH1] * modulation)
                        ch2.append(self._power_levels[PowerChannel.CH2] * (2.0 - modulation))
                result = BurstPowerData(tuple(timestamps), tuple(ch1), tuple(ch2))
            except (PM5020AcquisitionError, PM5020Cancelled):
                self._state = "error"
                raise
            except PM5020CommunicationError:
                self._state = "error"
                raise
            self._state = "idle"
            return result

    @override
    def close(self) -> None:
        with self._lock:
            self._connected = False
            self._state = "disconnected"
            self._burst_config = None
            self._channel_configs.clear()

    def _require_idle(self) -> None:
        if not self._connected:
            raise PM5020StateError("PM5020 simulator is not connected")
        if self._state != "idle":
            raise PM5020StateError(f"PM5020 simulator is busy or requires recovery (state: {self._state})")

    def _require_sensor(self, channel: PowerChannel) -> None:
        if channel not in self._available_channels:
            raise PM5020CommunicationError(f"No simulated sensor is connected to channel {channel.value}")
        if channel not in self._power_levels:
            raise PM5020CommunicationError(f"No simulated power level is configured for channel {channel.value}")

    def _maybe_fail(self, operation: str) -> None:
        if operation in self._fail_operations:
            self._fail_operations.remove(operation)
            raise PM5020CommunicationError(f"Simulated PM5020 {operation} failure")
