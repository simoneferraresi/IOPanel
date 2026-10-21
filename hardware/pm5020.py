"""Physical Thorlabs PM5020 adapter with lazy vendor loading and serialized access."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import os
import shutil
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable
from ctypes import byref, c_bool, c_double, c_float, c_uint16, c_uint32, create_string_buffer
from pathlib import Path
from threading import Event
from typing import Any, Self, override

from hardware.interfaces import AbstractPM5020
from hardware.pm5020_types import (
    BurstAcquisitionConfig,
    BurstPowerData,
    ChannelConfig,
    PM5020State,
    PowerChannel,
    PowerReading,
)

logger = logging.getLogger("LabApp.PM5020")

# Thorlabs support case CAS-778044-M8W8F1: vendor fix supplied Nov 2025;
# physically validated with 330 PM5020 samples on 2026-10-21.
_VERIFIED_PM5020_DLL_X64_SHA256 = "0e340f2abab40b15ad692d3b7621d5738ae40a571404fcaa986ae7860d4a08b8"
_KNOWN_BROKEN_PM5020_DLL_X64_SHA256 = "fbbe8cc3ebea45a5a42b437413adf862e9becbdf0d4bb132e2b768eafa2a4fef"


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_staged_file(source: Path, target: Path, expected_hash: str) -> None:
    if target.is_file() and _file_sha256(target) == expected_hash:
        return
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _load_thorlabs_factory(wrapper_path: Path | None, dll_path: Path | None) -> tuple[Callable[[], Any], Any | None]:
    """Load configured vendor files from a hash-validated, application-owned stage."""
    if wrapper_path is None or dll_path is None:
        raise PM5020DependencyError(
            "Physical PM5020 requires explicitly configured pm5020_wrapper_path "
            "and pm5020_dll_path; implicit SDK imports are disabled."
        )
    if not wrapper_path.is_file():
        raise PM5020DependencyError(f"Thorlabs wrapper was not found: {wrapper_path}")
    if not dll_path.is_file():
        raise PM5020DependencyError(f"Thorlabs native DLL was not found: {dll_path}")

    wrapper_hash = _file_sha256(wrapper_path)
    dll_hash = _file_sha256(dll_path)
    cache_root = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "IOPanel" / "pm5020-sdk"
    cache_dir = cache_root / f"{wrapper_hash[:16]}-{dll_hash[:16]}"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        staged_wrapper = cache_dir / "TLPMX.py"
        dll_name = "TLPMX_64.dll" if sys.maxsize > 2**32 else "TLPMX_32.dll"
        staged_dll = cache_dir / dll_name
        _replace_staged_file(wrapper_path, staged_wrapper, wrapper_hash)
        _replace_staged_file(dll_path, staged_dll, dll_hash)
    except OSError as exc:
        raise PM5020DependencyError(f"Could not stage the Thorlabs SDK under {cache_dir}: {exc}") from exc

    module_name = f"_iopanel_tlpmx_{wrapper_hash[:16]}_{dll_hash[:16]}"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, staged_wrapper)
        if spec is None or spec.loader is None:
            raise PM5020DependencyError(f"Could not create an import loader for Thorlabs wrapper: {staged_wrapper}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
    factory = getattr(module, "TLPMX", None)
    if not callable(factory):
        raise PM5020DependencyError(f"Thorlabs wrapper does not define a callable TLPMX class: {wrapper_path}")
    if not hasattr(os, "add_dll_directory"):
        return factory, None
    handles = []
    try:
        handles.append(os.add_dll_directory(str(cache_dir)))
        if dll_path.parent != cache_dir:
            handles.append(os.add_dll_directory(str(dll_path.parent)))
    except OSError:
        for handle in reversed(handles):
            handle.close()
        raise
    return factory, tuple(handles)


def _require_verified_pm5020_sdk(dll_path: Path | None) -> None:
    """Require the lab-verified native DLL before opening any physical PM5020 session.

    Simulated and explicitly injected fake drivers remain vendor-independent.
    """
    if sys.maxsize <= 2**32:
        raise PM5020DependencyError("Verified physical PM5020 connection currently requires 64-bit Python")
    if dll_path is None:
        raise PM5020DependencyError("Physical PM5020 requires an explicitly configured pm5020_dll_path")
    try:
        actual_sha = _file_sha256(dll_path)
    except OSError as exc:
        raise PM5020DependencyError(f"Cannot verify PM5020 native DLL at {dll_path}: {exc}") from exc
    if actual_sha != _VERIFIED_PM5020_DLL_X64_SHA256:
        condition = "known-broken" if actual_sha == _KNOWN_BROKEN_PM5020_DLL_X64_SHA256 else "unverified"
        raise PM5020DependencyError(
            f"PM5020 {condition} native DLL for physical connection: {dll_path} "
            f"(SHA-256 {actual_sha}); require lab-verified SHA-256 "
            f"{_VERIFIED_PM5020_DLL_X64_SHA256}. No physical PM5020 session was opened."
        )


class TLPMXPM5020(AbstractPM5020):
    """PM5020 backend using Thorlabs' locally installed ``TLPMX`` wrapper.

    The wrapper is imported only by :meth:`connect`. Every vendor call is made
    while holding one I/O lock. A separate state lock lets observers inspect
    transitions during blocking native calls. An armed or failed burst reserves the device until
    retrieval succeeds or the caller closes the connection.
    """

    def __init__(
        self,
        driver_factory: Callable[[], Any] | None = None,
        *,
        wrapper_path: str | Path | None = None,
        dll_path: str | Path | None = None,
    ):
        self._driver_factory = driver_factory
        self._wrapper_path = Path(wrapper_path).expanduser() if wrapper_path else None
        self._dll_path = Path(dll_path).expanduser() if dll_path else None
        self._dll_directory_handle: Any | None = None
        self._driver: Any | None = None
        self._state = PM5020State.DISCONNECTED
        self._state_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._burst_config: BurstAcquisitionConfig | None = None
        self.resource_name: str | None = None

    def _release_dll_directory_handles(self) -> None:
        """Release DLL search paths after native session cleanup, including failures."""
        handles, self._dll_directory_handle = self._dll_directory_handle, None
        if handles is None:
            return
        for handle in reversed(handles if isinstance(handles, tuple) else (handles,)):
            try:
                handle.close()
            except Exception:
                logger.exception("Failed to release a PM5020 DLL search-directory handle")

    @override
    def is_connected(self) -> bool:
        return self.state is not PM5020State.DISCONNECTED

    @property
    @override
    def state(self) -> PM5020State:
        with self._state_lock:
            return self._state

    def _set_state(self, state: PM5020State) -> None:
        with self._state_lock:
            self._state = state

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

        with self._io_lock:
            if self._driver is not None:
                raise PM5020StateError("PM5020 is already connected; close it before reconnecting")
            if self._driver_factory is None:
                # Reject incompatible binaries before loading the SDK or opening VISA.
                _require_verified_pm5020_sdk(self._dll_path)
                try:
                    factory, self._dll_directory_handle = _load_thorlabs_factory(self._wrapper_path, self._dll_path)
                except PM5020DependencyError:
                    raise
                except (ImportError, AttributeError, OSError) as exc:
                    raise PM5020DependencyError(
                        "Could not load the Thorlabs PM5020 SDK. Configure valid pm5020_wrapper_path and "
                        "pm5020_dll_path values in config.ini, install the Thorlabs VISA runtime, and "
                        "use a Python process with matching 32-bit or 64-bit architecture. "
                        f"Details: {exc}"
                    ) from exc
            else:
                factory = self._driver_factory

            driver = None
            try:
                driver = factory()
                driver.open(create_string_buffer(encoded_resource), c_bool(True), c_bool(False))
            except Exception as exc:
                try:
                    if driver is not None:
                        try:
                            driver.close()
                        except Exception:
                            logger.exception("Failed to close a PM5020 session after connection failure")
                finally:
                    self._release_dll_directory_handles()
                if driver is None and isinstance(exc, OSError):
                    raise PM5020DependencyError(
                        "Could not load the staged Thorlabs native TLPMX DLL or one of its dependencies. "
                        "Check the configured SDK paths, install the Thorlabs VISA runtime, and ensure "
                        "the DLL architecture matches Python (matching 32-bit or 64-bit architecture)."
                    ) from exc
                raise PM5020CommunicationError(f"Could not open PM5020 resource {resource_name!r}: {exc}") from exc

            self._driver = driver
            self._set_state(PM5020State.IDLE)
            self._burst_config = None
            self.resource_name = resource_name
            logger.info("Connected to PM5020 at VISA resource %s", resource_name)

    @override
    def configure_channel(self, channel: PowerChannel, config: ChannelConfig) -> None:
        channel = PowerChannel(channel)
        with self._io_lock:
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
                self._set_state(PM5020State.ERROR)
                raise PM5020CommunicationError(f"Could not configure PM5020 channel {channel.value}: {exc}") from exc

    @override
    def read_power(self, channel: PowerChannel) -> PowerReading:
        channel = PowerChannel(channel)
        with self._io_lock:
            driver = self._require_idle()
            power_w = c_double()
            try:
                driver.measPower(byref(power_w), c_uint16(channel.value))
            except Exception as exc:
                self._set_state(PM5020State.ERROR)
                raise PM5020CommunicationError(f"Could not read PM5020 channel {channel.value}: {exc}") from exc
            return PowerReading.now(channel, power_w.value)

    @override
    def configure_burst(self, config: BurstAcquisitionConfig) -> None:
        if set(config.channels) != {PowerChannel.CH1, PowerChannel.CH2}:
            raise ValueError("PM5020 burst acquisition must configure both CH1 and CH2")
        with self._io_lock:
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
                self._set_state(PM5020State.ERROR)
                raise PM5020CommunicationError(f"Could not configure PM5020 burst acquisition: {exc}") from exc
            self._burst_config = config

    @override
    def start_burst(self) -> None:
        with self._io_lock:
            driver = self._require_idle()
            if self._burst_config is None:
                raise PM5020StateError("Configure burst acquisition before arming it")
            try:
                driver.startBurstArrayMeasurement()
            except Exception as exc:
                self._set_state(PM5020State.ERROR)
                raise PM5020CommunicationError(f"Could not arm PM5020 burst acquisition: {exc}") from exc
            self._set_state(PM5020State.ARMED)

    @override
    def retrieve_burst(
        self,
        *,
        max_samples: int | None = None,
        cancel_event: Event | None = None,
    ) -> BurstPowerData:
        if max_samples is not None and (
            not isinstance(max_samples, int) or isinstance(max_samples, bool) or not 1 <= max_samples <= 15_000
        ):
            raise ValueError("max_samples must be between 1 and 15000")
        with self._io_lock:
            driver = self._driver
            if driver is None:
                raise PM5020StateError("PM5020 is not connected")
            if self.state is not PM5020State.ARMED:
                raise PM5020StateError("PM5020 burst acquisition is not armed")
            config = self._burst_config
            if config is None:
                raise PM5020StateError("Burst configuration is unavailable")
            effective_capacity = config.max_samples if max_samples is None else max_samples
            if effective_capacity > config.max_samples:
                raise ValueError("max_samples cannot exceed the configured burst capacity")

            self._set_state(PM5020State.RETRIEVING)
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise PM5020Cancelled("PM5020 burst retrieval cancelled before sample-count query")
                count = c_uint32()
                driver.getBurstArraySamplesCount(byref(count))
                sample_count = count.value
                if sample_count == 0:
                    raise PM5020AcquisitionError("PM5020 reported zero burst samples")
                if sample_count > effective_capacity:
                    raise PM5020AcquisitionError(
                        f"PM5020 reported {sample_count} samples, exceeding capacity {effective_capacity}"
                    )

                # Validated vendor DLL: fetch the full reported buffer in one call.
                # This allocates at most three arrays of 15,000 32-bit values.
                if cancel_event is not None and cancel_event.is_set():
                    raise PM5020Cancelled("PM5020 burst retrieval cancelled before native array read")
                time_buffer = (c_uint32 * sample_count)()
                ch1_buffer = (c_float * sample_count)()
                ch2_buffer = (c_float * sample_count)()
                driver.getBurstArraySamples(
                    c_uint32(0),
                    c_uint32(sample_count),
                    time_buffer,
                    ch1_buffer,
                    ch2_buffer,
                )
                result = BurstPowerData(tuple(time_buffer), tuple(ch1_buffer), tuple(ch2_buffer))
                if any(new <= old for old, new in zip(result.timestamps_ticks, result.timestamps_ticks[1:])):
                    raise PM5020AcquisitionError(
                        "PM5020 burst timestamps are repeated or non-increasing; "
                        "possible outdated Thorlabs native DLL or invalid capture. "
                        "Discard this burst and reconnect before retrying."
                    )
            except PM5020Error:
                self._set_state(PM5020State.ERROR)
                raise
            except Exception as exc:
                self._set_state(PM5020State.ERROR)
                raise PM5020CommunicationError(f"Could not retrieve PM5020 burst data: {exc}") from exc

            self._set_state(PM5020State.IDLE)
            return result

    @override
    def close(self) -> None:
        with self._io_lock:
            driver, self._driver = self._driver, None
            self._set_state(PM5020State.DISCONNECTED)
            self._burst_config = None
            self.resource_name = None
            try:
                if driver is not None:
                    driver.close()
            except Exception as exc:
                logger.exception("Failed to close PM5020 session")
                raise PM5020CommunicationError(f"Failed to close PM5020 session: {exc}") from exc
            finally:
                self._release_dll_directory_handles()

    def _require_idle(self) -> Any:
        if self._driver is None:
            raise PM5020StateError("PM5020 is not connected")
        state = self.state
        if state is not PM5020State.IDLE:
            raise PM5020StateError(f"PM5020 is busy or requires recovery (state: {state.name.lower()})")
        return self._driver

    def __enter__(self) -> Self:
        if not self.is_connected():
            raise PM5020StateError("Connect PM5020 before entering its context manager")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
