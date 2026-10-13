"""Physical Thorlabs PM5020 adapter with lazy vendor loading and serialized access."""

from __future__ import annotations

import importlib
import logging
import threading
from collections.abc import Callable
from ctypes import byref, c_bool, c_double, c_float, c_uint16, c_uint32, create_string_buffer
from enum import Enum, auto
from threading import Event
from typing import Any, Self, override

from hardware.interfaces import AbstractPM5020
from hardware.pm5020_types import BurstAcquisitionConfig, BurstPowerData, ChannelConfig, PowerChannel, PowerReading

logger = logging.getLogger("LabApp.PM5020")


class PM5020Error(RuntimeError):
    """Base error raised by the PM5020 backends."""


class PM5020DependencyError(PM5020Error):
    """The Thorlabs Python wrapper or native driver is unavailable."""


class PM5020StateError(PM5020Error):
    """The requested operation is not valid in the instrument's current state."""


class PM5020CommunicationError(PM5020Error):
    """A call to the Thorlabs wrapper failed."""


class PM5020AcquisitionError(PM5020Error):
    """Burst data were empty, incomplete, or exceeded the configured capacity."""


class PM5020Cancelled(PM5020Error):
    """Cancellation was observed between blocking vendor calls."""


class _State(Enum):
    DISCONNECTED = auto()
    IDLE = auto()
    ARMED = auto()
    RETRIEVING = auto()
    ERROR = auto()


class TLPMXPM5020(AbstractPM5020):
    """PM5020 backend using Thorlabs' locally installed ``TLPMX`` wrapper.

    The wrapper is imported only by :meth:`connect`. Every vendor call is made
    while holding one lock. An armed or failed burst reserves the device until
    retrieval succeeds or the caller closes the connection.
    """

    def __init__(self, driver_factory: Callable[[], Any] | None = None):
        self._driver_factory = driver_factory
        self._driver: Any | None = None
        self._state = _State.DISCONNECTED
        self._lock = threading.Lock()
        self._burst_config: BurstAcquisitionConfig | None = None
        self.resource_name: str | None = None

    @override
    def is_connected(self) -> bool:
        with self._lock:
            return self._driver is not None and self._state is not _State.DISCONNECTED

    @override
    def connect(self, resource_name: str | None = None) -> None:
        if not isinstance(resource_name, str) or not resource_name.strip():
            raise ValueError("A non-empty USB/VISA resource name is required")
        resource_name = resource_name.strip()
        if "\0" in resource_name:
            raise ValueError("resource_name cannot contain a NUL character")
        try:
            encoded_resource = resource_name.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("VISA resource names must contain only ASCII characters") from exc

        with self._lock:
            if self._driver is not None:
                raise PM5020StateError("PM5020 is already connected; close it before reconnecting")
            if self._driver_factory is None:
                try:
                    module = importlib.import_module("TLPMX")
                    factory = module.TLPMX
                except (ImportError, AttributeError) as exc:
                    raise PM5020DependencyError(
                        "Physical PM5020 support requires the Thorlabs Optical Power Meter SDK, "
                        "its TLPMX.py wrapper, and a matching native TLPMX DLL. Install these "
                        "through Thorlabs' Windows software installer."
                    ) from exc
            else:
                factory = self._driver_factory

            driver = None
            try:
                driver = factory()
                driver.open(create_string_buffer(encoded_resource), c_bool(True), c_bool(False))
            except Exception as exc:
                if driver is not None:
                    try:
                        driver.close()
                    except Exception:
                        logger.exception("Failed to close a PM5020 session after connection failure")
                if driver is None and isinstance(exc, OSError):
                    raise PM5020DependencyError(
                        "Could not load the Thorlabs native TLPMX DLL. Install the Thorlabs Optical "
                        "Power Meter software and use a Python process with matching 32-bit or "
                        "64-bit architecture."
                    ) from exc
                raise PM5020CommunicationError(f"Could not open PM5020 resource {resource_name!r}: {exc}") from exc

            self._driver = driver
            self._state = _State.IDLE
            self._burst_config = None
            self.resource_name = resource_name
            logger.info("Connected to PM5020 at VISA resource %s", resource_name)

    @override
    def configure_channel(self, channel: PowerChannel, config: ChannelConfig) -> None:
        channel = PowerChannel(channel)
        with self._lock:
            driver = self._require_idle()
            try:
                driver.setFreqMode(config.frequency_mode, c_uint16(channel.value))
                driver.setPowerAutoRange(c_bool(config.auto_range), c_uint16(channel.value))
                if config.power_range_w is not None and not config.auto_range:
                    driver.setPowerRange(c_double(config.power_range_w), c_uint16(channel.value))
                if config.wavelength_nm is not None:
                    driver.setWavelength(c_double(config.wavelength_nm), c_uint16(channel.value))
                driver.setInputFilterState(c_bool(config.input_filter_enabled), c_uint16(channel.value))
            except Exception as exc:
                self._state = _State.ERROR
                raise PM5020CommunicationError(f"Could not configure PM5020 channel {channel.value}: {exc}") from exc

    @override
    def read_power(self, channel: PowerChannel) -> PowerReading:
        channel = PowerChannel(channel)
        with self._lock:
            driver = self._require_idle()
            power_w = c_double()
            try:
                driver.measPower(byref(power_w), c_uint16(channel.value))
            except Exception as exc:
                self._state = _State.ERROR
                raise PM5020CommunicationError(f"Could not read PM5020 channel {channel.value}: {exc}") from exc
            return PowerReading.now(channel, power_w.value)

    @override
    def configure_burst(self, config: BurstAcquisitionConfig) -> None:
        if set(config.channels) != {PowerChannel.CH1, PowerChannel.CH2}:
            raise ValueError("PM5020 burst acquisition must configure both CH1 and CH2")
        with self._lock:
            driver = self._require_idle()
            try:
                for channel in config.channels:
                    driver.confBurstArrayMeasPowerChannel(c_uint16(channel.value))
                driver.confBurstArrayMeasTrigger(
                    c_uint32(config.trigger_source),
                    c_uint32(config.initial_delay_counts),
                    c_uint32(config.samples_per_trigger),
                    c_uint32(config.averaging_count),
                )
            except Exception as exc:
                self._state = _State.ERROR
                raise PM5020CommunicationError(f"Could not configure PM5020 burst acquisition: {exc}") from exc
            self._burst_config = config

    @override
    def start_burst(self) -> None:
        with self._lock:
            driver = self._require_idle()
            if self._burst_config is None:
                raise PM5020StateError("Configure burst acquisition before arming it")
            try:
                driver.startBurstArrayMeasurement()
            except Exception as exc:
                self._state = _State.ERROR
                raise PM5020CommunicationError(f"Could not arm PM5020 burst acquisition: {exc}") from exc
            self._state = _State.ARMED

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
            driver = self._driver
            if driver is None:
                raise PM5020StateError("PM5020 is not connected")
            if self._state is not _State.ARMED:
                raise PM5020StateError("PM5020 burst acquisition is not armed")
            config = self._burst_config
            if config is None:
                raise PM5020StateError("Burst configuration is unavailable")
            if max_samples > config.max_samples:
                raise ValueError("max_samples cannot exceed the configured burst capacity")

            self._state = _State.RETRIEVING
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise PM5020Cancelled("PM5020 burst retrieval cancelled before sample-count query")
                count = c_uint32()
                driver.getBurstArraySamplesCount(byref(count))
                sample_count = count.value
                if sample_count == 0:
                    raise PM5020AcquisitionError("PM5020 reported zero burst samples")
                if sample_count > max_samples:
                    raise PM5020AcquisitionError(
                        f"PM5020 reported {sample_count} samples, exceeding capacity {max_samples}"
                    )

                timestamps: list[int] = []
                powers_ch1: list[float] = []
                powers_ch2: list[float] = []
                for start_index in range(0, sample_count, config.chunk_size):
                    if cancel_event is not None and cancel_event.is_set():
                        raise PM5020Cancelled(
                            f"PM5020 burst retrieval cancelled after {len(timestamps)} of {sample_count} samples"
                        )
                    samples_to_get = min(config.chunk_size, sample_count - start_index)
                    time_buffer = (c_uint32 * samples_to_get)()
                    ch1_buffer = (c_float * samples_to_get)()
                    ch2_buffer = (c_float * samples_to_get)()
                    driver.getBurstArraySamples(
                        c_uint32(start_index),
                        c_uint32(samples_to_get),
                        time_buffer,
                        ch1_buffer,
                        ch2_buffer,
                    )
                    timestamps.extend(time_buffer)
                    powers_ch1.extend(ch1_buffer)
                    powers_ch2.extend(ch2_buffer)

                result = BurstPowerData(tuple(timestamps), tuple(powers_ch1), tuple(powers_ch2))
                if result.sample_count != sample_count:
                    raise PM5020AcquisitionError(
                        f"PM5020 returned {result.sample_count} samples after reporting {sample_count}"
                    )
            except PM5020Error:
                self._state = _State.ERROR
                raise
            except Exception as exc:
                self._state = _State.ERROR
                raise PM5020CommunicationError(f"Could not retrieve PM5020 burst data: {exc}") from exc

            self._state = _State.IDLE
            return result

    @override
    def close(self) -> None:
        with self._lock:
            driver, self._driver = self._driver, None
            self._state = _State.DISCONNECTED
            self._burst_config = None
            self.resource_name = None
            if driver is None:
                return
            try:
                driver.close()
            except Exception as exc:
                logger.exception("Failed to close PM5020 session")
                raise PM5020CommunicationError(f"Failed to close PM5020 session: {exc}") from exc

    def _require_idle(self) -> Any:
        if self._driver is None:
            raise PM5020StateError("PM5020 is not connected")
        if self._state is not _State.IDLE:
            raise PM5020StateError(f"PM5020 is busy or requires recovery (state: {self._state.name.lower()})")
        return self._driver

    def __enter__(self) -> Self:
        if not self.is_connected():
            raise PM5020StateError("Connect PM5020 before entering its context manager")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
