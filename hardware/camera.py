from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtCore import QMutex, QMutexLocker, QObject, Signal

from hardware.camera_capabilities import (
    FEATURE_ALIASES,
    apply_roi,
    centered_offset,
    find_feature,
    inspect_camera,
    inspect_feature,
    read_roi,
)
from hardware.camera_discovery import (
    DISCOVERY_TIMEOUT_SECONDS,
    CameraDiscoveryCancelled,
    CameraNotFoundError,
    poll_camera_list,
    wait_for_camera_by_id,
)

try:
    from vmbpy import (
        COLOR_PIXEL_FORMATS,
        MONO_PIXEL_FORMATS,
        OPENCV_PIXEL_FORMATS,
        Camera,
        Frame,
        FrameStatus,
        PixelFormat,
        Stream,
        VmbCameraError,
        VmbSystem,
        VmbSystemError,
        intersect_pixel_formats,
    )

    VIMBA_AVAILABLE = True
    VIMBA_IMPORT_ERROR: Exception | None = None
except Exception as exc:
    # VmbPy can fail during import when its VmbC runtime is missing/incompatible.
    # Treat only the optional dependency errors as unavailable; surface other bugs.
    error_type = type(exc)
    is_vmbpy_system_error = error_type.__module__ == "vmbpy.error" and error_type.__name__ == "VmbSystemError"
    if not isinstance(exc, ImportError) and not is_vmbpy_system_error:
        raise

    COLOR_PIXEL_FORMATS = MONO_PIXEL_FORMATS = OPENCV_PIXEL_FORMATS = ()
    Camera = Frame = FrameStatus = PixelFormat = Stream = object
    VmbSystem = None
    intersect_pixel_formats = None
    VIMBA_AVAILABLE = False
    VIMBA_IMPORT_ERROR: Exception | None = exc

    class VmbCameraError(Exception):
        """Fallback exception name used when the optional Vimba binding is absent."""

    class VmbSystemError(Exception):
        """Fallback exception name used when the optional Vimba binding is absent."""


logger = logging.getLogger("LabApp.camera")

# --- NEW: Type Alias for Clarity ---
type CameraInfoDict = dict[str, Any]
type FeatureRange = tuple[Any, Any] | None


def _frame_rate_enable_is_enabled(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"on", "enabled", "true", "1"}:
        return True
    if normalized in {"off", "disabled", "false", "0"}:
        return False
    return None


@dataclass
class CameraSettings:
    """Holds a cache of the last known camera settings."""

    exposure_us: float = 10000.0
    gamma: float = 1.0
    gain_db: float = 0.0
    pixel_format: PixelFormat | None = None
    is_auto_exposure_on: bool = False
    is_auto_gain_on: bool = False


class FrameRateMonitor:
    """Calculates frames per second over a sliding window."""

    def __init__(self, window_size: int = 30):
        self.timestamps: deque[float] = deque(maxlen=window_size)
        self.fps: float = 0.0
        self.last_fps_update_time: float = 0.0
        self.update_interval: float = 0.5  # Update FPS value every 0.5 seconds

    def update(self) -> float | None:
        """Add a timestamp and return an FPS value only when recalculated."""
        now = time.monotonic()
        self.timestamps.append(now)
        if (now - self.last_fps_update_time >= self.update_interval) and len(self.timestamps) >= 2:
            time_diff = self.timestamps[-1] - self.timestamps[0]
            if time_diff > 1e-9:  # Avoid division by zero
                self.fps = (len(self.timestamps) - 1) / time_diff
            else:
                self.fps = 0.0
            self.last_fps_update_time = now
            return self.fps
        return None

    def get_fps(self) -> float:
        """Returns the last calculated FPS value."""
        return self.fps

    def reset(self) -> None:
        self.timestamps.clear()
        self.fps = 0.0
        self.last_fps_update_time = 0.0


class FrameBuffer:
    """A thread-safe buffer to store the latest few frames from a camera."""

    def __init__(self, max_size: int = 3):
        self.buffer: deque[np.ndarray] = deque(maxlen=max_size)
        self.lock = QMutex()

    def add_frame(self, frame: np.ndarray):
        """Adds a new frame to the buffer, evicting the oldest if full."""
        with QMutexLocker(self.lock):
            self.buffer.append(frame)

    def get_latest_frame(self) -> np.ndarray | None:
        """Returns a copy of the most recent frame in the buffer."""
        with QMutexLocker(self.lock):
            if not self.buffer:
                return None
            return self.buffer[-1].copy()

    def clear(self):
        """Empties the buffer."""
        with QMutexLocker(self.lock):
            self.buffer.clear()


class VimbaCam(QObject):
    """
    Manages a Vimba-compatible camera, abstracting Vimba API details.

    This class handles the connection, streaming, and settings for a single camera.
    It operates on its own by registering a callback (`_frame_handler`) with the
    Vimba transport layer, which runs in a separate, high-priority Vimba thread.
    All public methods of this class are designed to be thread-safe and are
    intended to be called from the main Qt GUI thread.

    It emits signals for new frames, FPS updates, connection status, and errors,
    making it suitable for integration into a Qt application.

    Attributes:
        new_frame (Signal): Emits a new frame as a numpy.ndarray.
        fps_updated (Signal): Emits the current calculated FPS as a float.
        connected (Signal): Emitted when the camera successfully starts streaming.
        disconnected (Signal): Emitted when the camera connection is closed.
        error (Signal): Emits an error message string for display in the UI.
    """

    _DEFAULT_STREAM_BUFFER_COUNT = 5
    _RECOVERY_DELAY_SECONDS = 0.5

    new_frame = Signal(np.ndarray)
    fps_updated = Signal(float)
    connected = Signal()
    disconnected = Signal()
    error = Signal(str)  # For simple UI messages, string is acceptable here.
    # Could be upgraded to a structured error if needed.

    def __init__(
        self,
        identifier: str,
        camera_name: str | None = None,
        flip_horizontal: bool = False,
        parent: QObject | None = None,
    ):
        """
        Initializes the VimbaCam instance.

        Args:
            identifier: The unique ID of the camera (e.g., 'DEV_...').
            camera_name: An optional user-friendly name for the camera.
                         If None, the identifier is used.
            flip_horizontal: If True, incoming frames will be flipped horizontally.
            parent: The parent QObject for Qt's memory management.

        Raises:
            ValueError: If the camera identifier is empty.
        """
        super().__init__(parent)
        if not identifier:
            raise ValueError("Camera identifier cannot be empty.")
        self.identifier = identifier
        self.camera_name = camera_name or identifier
        self.flip_horizontal = flip_horizontal

        self.device: Camera | None = None
        self.lock = QMutex()
        self.is_mono: bool | None = None
        self.is_streaming: bool = False
        self._is_closing: bool = False
        self._discovery_cancel_event = threading.Event()

        self.frame_monitor = FrameRateMonitor()
        self.frame_buffer = FrameBuffer(max_size=3)
        self.settings = CameraSettings()
        self.setObjectName(f"VimbaCam_{self.identifier}")
        logger.info(f"VimbaCam instance created for identifier: {self.identifier} (Name: {self.camera_name})")

    @staticmethod
    def list_cameras(
        *,
        on_update: Callable[[list[CameraInfoDict]], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[CameraInfoDict]:
        """
        Discovers all connected Vimba-compatible cameras.

        This static method scans the system using the Vimba API and returns
        a list of dictionaries, each containing essential information about
        a detected camera.

        Returns:
            A list of dictionaries, where each dictionary represents a camera
            and contains keys like 'id', 'serial', 'model', and 'name'.
        """
        if not VIMBA_AVAILABLE:
            logger.warning("Camera discovery is unavailable: the optional Vimba binding is not installed.")
            return []

        cameras_info: list[CameraInfoDict] = []
        logger.info("Listing available Vimba cameras...")
        try:
            with VmbSystem.get_instance() as vmb:

                def describe(cameras: list[Any]) -> list[CameraInfoDict]:
                    result: list[CameraInfoDict] = []
                    for i, cam in enumerate(cameras):
                        info = {"numeric_index": i}
                        try:
                            info["id"] = cam.get_id()
                            info["serial"] = cam.get_serial()
                            info["model"] = cam.get_model()
                            info["name"] = cam.get_name()
                            result.append(info)
                            logger.debug(f"  Found Cam {i}: ID={info['id']}, Serial={info.get('serial')}")
                        except Exception as e:  # noqa: BLE001 - metadata getters are optional.
                            try:
                                cam_id_for_log = cam.get_id()
                            except Exception:  # noqa: BLE001 - retain index if ID lookup also fails.
                                cam_id_for_log = f"at index {i}"
                            logger.warning(f"Could not fully query camera '{cam_id_for_log}': {e}")
                    return result

                def publish(cameras: list[Any]) -> None:
                    nonlocal cameras_info
                    cameras_info = describe(cameras)
                    logger.info("Total cameras currently detected: %d", len(cameras_info))
                    if on_update is not None:
                        on_update(cameras_info)

                if on_update is None:
                    publish(list(vmb.get_all_cameras()))
                else:
                    poll_camera_list(vmb, cancel_event=cancel_event, on_update=publish)

        except VmbSystemError as e:
            logger.error(f"Vimba system error while listing cameras: {e}")
            if on_update is not None:
                raise
        except Exception as e:  # Preserve VmbPy metadata and enumeration fallbacks.
            logger.error(f"An unexpected error occurred while listing cameras: {e}")
            if on_update is not None:
                raise
        return cameras_info

    # --- Vimba Frame Callback Handler ---
    def _frame_handler(self, cam: Camera, stream: Stream, frame: Frame):
        """Callback executed by Vimba for each incoming frame."""
        import cv2

        if self._is_closing:
            return

        try:
            if frame.get_status() == FrameStatus.Complete:
                # Convert frame to OpenCV image
                current_image = frame.as_opencv_image()
                if current_image is None or current_image.size == 0:
                    logger.warning(f"Handler {self.camera_name}: Frame from as_opencv_image() is None or empty.")
                    return

                # Apply horizontal flip if configured
                if self.flip_horizontal:
                    current_image = cv2.flip(current_image, 1)

                # Update frame buffer. Must copy as the underlying buffer will be reused by Vimba.
                processed_image = current_image.copy()
                self.frame_buffer.add_frame(processed_image)

                # Emit signals for the GUI
                self.new_frame.emit(processed_image)
                fps = self.frame_monitor.update()
                if fps is not None:
                    self.fps_updated.emit(fps)
        except Exception:
            logger.exception(f"Handler {self.camera_name}: Unhandled error in frame processing")
        finally:
            # CRITICAL: Always re-queue the frame.
            try:
                # The lock here prevents a race condition on shutdown.
                with QMutexLocker(self.lock):
                    if not self._is_closing and self.device:
                        cam.queue_frame(frame)
            except VmbCameraError as e:
                logger.error(f"Handler {self.camera_name}: CRITICAL - Failed to queue frame back: {e}")
                self.error.emit(f"CRITICAL Frame queueing error: {e}")

    # --- Open/Close and Configuration ---
    def open(self) -> bool:
        """Opens the camera and starts streaming."""
        logger.info(f"Attempting to open camera: {self.camera_name} (ID: {self.identifier})")
        self._is_closing = False
        if not VIMBA_AVAILABLE:
            message = "Vimba camera support is unavailable. Use a VmbPy binding compatible with the installed Vimba X/VmbC runtime."
            logger.error(message)
            self.error.emit(message)
            return False

        if self.device:
            logger.warning(f"Camera {self.camera_name} already open.")
            return True

        try:
            if not self._open_device_internal():
                return False

            self.device.start_streaming(self._frame_handler, buffer_count=self._DEFAULT_STREAM_BUFFER_COUNT)
            self.is_streaming = True
            logger.info(f"Camera {self.camera_name} opened and streaming started.")
            self.connected.emit()
            return True
        except CameraNotFoundError as e:
            logger.error("Camera discovery timed out for %s: %s", self.identifier, e)
            self.error.emit(f"Camera discovery timeout: {e}")
            self.close()
            return False
        except VmbCameraError as e:
            logger.error(f"Vimba error during camera open sequence: {e}")
            self.error.emit(f"Open error: {e}")
            self.close()
            return False
        except Exception as e:
            logger.exception("Unexpected error during camera open sequence")
            self.error.emit(f"Unexpected open error: {e}")
            self.close()
            return False

    def close(self):
        """Stops streaming and closes the camera device connection cleanly."""
        with QMutexLocker(self.lock):
            if self._is_closing:
                return
            self._is_closing = True

        logger.info(f"Initiating close sequence for camera: {self.camera_name}")

        if self.device and self.is_streaming:
            try:
                self.device.stop_streaming()
                logger.info(f"Vimba streaming stopped for {self.camera_name}.")
            except VmbCameraError as e:
                logger.error(f"Error stopping Vimba streaming: {e}")
        self.is_streaming = False

        if self.device:
            try:
                self.device.__exit__(None, None, None)
                logger.info(f"Camera device {self.camera_name} closed successfully.")
            except VmbCameraError as e:
                logger.error(f"Vimba error closing device: {e}")
            finally:
                self.device = None
                self.is_mono = None
                self.disconnected.emit()

        self.frame_buffer.clear()
        logger.info(f"Close sequence finished for camera: {self.camera_name}")

    def recover_once(self) -> bool:
        """Blocking close/reopen primitive for a dedicated recovery worker."""
        logger.warning(f"Executing recovery attempt for {self.camera_name}...")
        self.close()
        time.sleep(self._RECOVERY_DELAY_SECONDS)

        self._discovery_cancel_event.clear()
        success = self.open()
        if success:
            logger.info(f"Recovery successful for {self.camera_name}.")
        else:
            logger.error(f"Recovery failed for {self.camera_name}.")
        return success

    def set_discovery_cancel_event(self, event: threading.Event) -> None:
        """Allow an owning initialization worker to cancel a pending open."""
        self._discovery_cancel_event = event

    def _open_device_internal(self) -> bool:
        """Internal: Opens device. Assumes VimbaSystem is ACTIVE."""
        entered_camera = None
        try:
            vmb = VmbSystem.get_instance()
            cam_opened = wait_for_camera_by_id(
                vmb,
                self.identifier,
                cancel_event=self._discovery_cancel_event,
                on_wait=lambda: logger.info(
                    "Waiting up to %.1fs for camera %s to become discoverable.",
                    DISCOVERY_TIMEOUT_SECONDS,
                    self.identifier,
                ),
            )
            cam_opened.__enter__()
            entered_camera = cam_opened
            self.device = cam_opened
            logger.info(f"Successfully opened camera device: {self.camera_name}")
            self._configure_camera()
            self._update_settings_cache()
            return True
        except CameraDiscoveryCancelled:
            logger.info("Discovery wait cancelled for camera %s.", self.identifier)
            self.device = None
            self.is_streaming = False
            self.is_mono = None
            return False
        except VmbCameraError as e:
            error_msg = f"Failed to open camera {self.camera_name}: {e}"
            logger.error(error_msg)
            if entered_camera is not None:
                try:
                    entered_camera.__exit__(None, None, None)
                except Exception:
                    logger.exception(f"Failed to release partially opened camera {self.camera_name}")
            self.device = None
            self.is_streaming = False
            self.is_mono = None
            self.error.emit(error_msg)
            return False

    def _configure_camera(self):
        """Sets default acquisition and trigger modes, and determines pixel format."""
        if not self.device:
            return
        with QMutexLocker(self.lock):
            if not self.device:
                return

            # Use a helper to safely set features
            def _safe_set(name, value):
                try:
                    feat = self.device.get_feature_by_name(name)
                    if feat.is_writeable():
                        feat.set(value)
                        logger.debug(f"Set {name} to {value}.")
                except Exception as e:  # noqa: BLE001 - SDK-specific feature errors are optional here.
                    logger.warning(f"Could not set feature '{name}': {e}")

            _safe_set("AcquisitionMode", "Continuous")
            _safe_set("TriggerMode", "Off")
            _safe_set("ExposureAuto", "Off")
            _safe_set("GainAuto", "Off")

            try:
                feat = self.device.get_feature_by_name("Gamma")
                if feat.is_writeable():
                    min_g, max_g = feat.get_range()
                    target_gamma = max(min_g, min(max_g, 1.0))
                    feat.set(target_gamma)
            except Exception as exc:  # noqa: BLE001 - Gamma is optional and must not prevent startup.
                logger.info("Gamma feature not available/writable: %s", exc)

            self._set_pixel_format()

    def _set_pixel_format(self):
        """Determines and sets the best OpenCV-compatible pixel format."""
        if not self.device:
            raise VmbCameraError("Cannot set pixel format: device not open.")

        dev_formats = self.device.get_pixel_formats()
        cv_formats = intersect_pixel_formats(dev_formats, OPENCV_PIXEL_FORMATS)
        if not cv_formats:
            raise VmbCameraError("No OpenCV-compatible pixel formats found on this camera.")

        preferred_format = None
        is_mono = None

        # Prioritize 8-bit mono formats
        mono_cv = intersect_pixel_formats(cv_formats, MONO_PIXEL_FORMATS)
        if mono_cv:
            preferred_format = next((f for f in mono_cv if f.name == "Mono8"), mono_cv[0])
            is_mono = True
        else:
            # Fallback to color formats
            color_cv = intersect_pixel_formats(cv_formats, COLOR_PIXEL_FORMATS)
            if color_cv:
                preferred_format = next((f for f in color_cv if f.name in ["BGR8", "RGB8"]), color_cv[0])
                is_mono = False

        if preferred_format is None or is_mono is None:
            raise VmbCameraError("Could not find a supported mono or color format.")

        self.device.set_pixel_format(preferred_format)
        self.settings.pixel_format = preferred_format
        self.is_mono = is_mono
        logger.info(f"Pixel format set to: {preferred_format.name}. Is Mono: {self.is_mono}")

    def _update_settings_cache(self):
        """Reads initial values from the camera and populates the settings cache."""
        if not self.device:
            return
        self.settings.exposure_us = self.get_exposure()
        self.settings.gamma = self.get_gamma()
        self.settings.gain_db = self.get_gain()
        try:
            self.settings.is_auto_exposure_on = self.device.get_feature_by_name("ExposureAuto").get() != "Off"
            self.settings.is_auto_gain_on = self.device.get_feature_by_name("GainAuto").get() != "Off"
        except Exception as exc:  # noqa: BLE001 - Auto selectors are optional across devices.
            logger.debug("Could not read auto selector state: %s", exc)

    # --- Feature Access Methods ---

    def get_latest_frame(self) -> np.ndarray | None:
        return self.frame_buffer.get_latest_frame()

    # Properties for direct, safe access to cached settings
    @property
    def exposure_us(self) -> float:
        return self.settings.exposure_us

    @property
    def gamma(self) -> float:
        return self.settings.gamma

    @property
    def gain_db(self) -> float:
        return self.settings.gain_db

    @property
    def is_auto_exposure_on(self) -> bool:
        return self.settings.is_auto_exposure_on

    @property
    def is_auto_gain_on(self) -> bool:
        return self.settings.is_auto_gain_on

    def get_feature_range(self, feature_name: str) -> FeatureRange:
        """
        Gets the (min, max) range of a feature.

        Args:
            feature_name: The name of the feature to query (e.g., "ExposureTimeAbs").

        Returns:
            A tuple of (min_value, max_value) if the feature is readable and
            has a range. Returns None on failure or if the feature doesn't exist.
        """
        if not self.device:
            return None
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return None
                aliases = FEATURE_ALIASES.get(feature_name, (feature_name,))
                cap = inspect_feature(self.device, aliases)
                if cap.available and cap.readable and cap.minimum is not None and cap.maximum is not None:
                    return cap.minimum, cap.maximum
                return None
        except Exception as e:  # noqa: BLE001 - GenICam features vary by camera/firmware.
            logger.warning(f"Could not get range for '{feature_name}': {e}")
            return None

    def get_feature_capability(self, feature_name: str):
        """Return the refreshed feature descriptor, if a camera is open."""
        if not self.device:
            return None
        try:
            with QMutexLocker(self.lock):
                return inspect_feature(self.device, FEATURE_ALIASES.get(feature_name, (feature_name,)))
        except Exception as exc:  # noqa: BLE001 - this is an optional capability query.
            logger.debug("Could not inspect feature %s: %s", feature_name, exc)
            return None

    def is_feature_writable(self, feature_name: str) -> bool:
        if not self.device:
            return False
        try:
            with QMutexLocker(self.lock):
                cap = inspect_feature(self.device, FEATURE_ALIASES.get(feature_name, (feature_name,)))
                return cap.available and cap.writable
        except Exception as exc:  # noqa: BLE001 - capability queries are best-effort.
            logger.debug("Could not inspect writability for %s: %s", feature_name, exc)
            return False

    def _get_feature_value(self, feature_name: str, cache_attr: str, default: Any) -> Any:
        """Generic private helper to get a feature's value and update the cache."""
        if not self.device:
            return getattr(self.settings, cache_attr, default)
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return getattr(self.settings, cache_attr, default)
                val = self.device.get_feature_by_name(feature_name).get()
                setattr(self.settings, cache_attr, val)
                return val
        except Exception as e:  # noqa: BLE001 - GenICam missing/unreadable errors are SDK-specific.
            logger.warning(f"Error getting feature '{feature_name}': {e}")
            return getattr(self.settings, cache_attr, default)

    def get_exposure(self) -> float:
        return self._get_feature_alias_value(FEATURE_ALIASES["exposure"], "exposure_us", 10000.0)

    def get_gain(self) -> float:
        return self._get_feature_alias_value(FEATURE_ALIASES["gain"], "gain_db", 0.0)

    def _get_feature_alias_value(self, aliases: tuple[str, ...], cache_attr: str, default: Any) -> Any:
        if not self.device:
            return getattr(self.settings, cache_attr, default)
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return getattr(self.settings, cache_attr, default)
                cap = inspect_feature(self.device, aliases)
                if cap.readable:
                    setattr(self.settings, cache_attr, cap.value)
                    return cap.value
        except Exception as exc:  # noqa: BLE001 - optional feature failures are non-fatal.
            logger.warning("Could not read camera feature %s: %s", aliases[0], exc)
        return getattr(self.settings, cache_attr, default)

    def get_capabilities(self) -> dict[str, Any] | None:
        """Return a best-effort capability report for an open camera."""
        if not self.device:
            return None
        try:
            with QMutexLocker(self.lock):
                return inspect_camera(self.device) if self.device else None
        except Exception as exc:  # noqa: BLE001 - diagnostics must not break streaming.
            logger.warning("Could not inspect camera capabilities: %s", exc)
            return None

    def get_roi(self):
        if not self.device:
            return None
        try:
            with QMutexLocker(self.lock):
                return read_roi(self.device) if self.device else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read camera ROI: %s", exc)
            return None

    def set_roi(self, roi) -> bool:
        """Apply an explicitly requested ROI, restoring the original on failure."""
        if not self.device or self.is_streaming:
            logger.warning("ROI changes require an open, stopped camera")
            return False
        try:
            with QMutexLocker(self.lock):
                apply_roi(self.device, roi)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not set camera ROI: %s", exc)
            self.error.emit(f"ROI error: {exc}")
            return False

    def apply_view_mode(self, width: int, height: int, maximize_rate: bool = True) -> dict[str, Any]:
        """Stop, configure a centered ROI and legal rate, then restart acquisition.

        Streaming is stopped and restarted outside ``self.lock`` because the
        frame callback needs that lock to finish requeueing outstanding frames.
        """
        if not self.device or not self.is_streaming:
            raise RuntimeError("View mode changes require an open, streaming physical camera")
        device = self.device
        was_streaming = self.is_streaming
        with QMutexLocker(self.lock):
            previous_roi = read_roi(device)
            previous_rate = inspect_feature(device, FEATURE_ALIASES["frame_rate"])
            previous_enable = inspect_feature(device, FEATURE_ALIASES["frame_rate_enable"])
        if previous_rate.readable is False or previous_rate.value is None:
            raise RuntimeError("Cannot read current acquisition frame rate for transactional view mode change")

        self.is_streaming = False
        try:
            device.stop_streaming()
        except Exception:
            self.is_streaming = was_streaming
            raise

        def write_rate(value: float) -> float:
            cap = inspect_feature(device, FEATURE_ALIASES["frame_rate"])
            if not cap.available or not cap.writable or cap.minimum is None or cap.maximum is None:
                raise RuntimeError("Acquisition frame-rate feature is unavailable or read-only")
            value = min(float(cap.maximum), max(float(cap.minimum), value))
            if cap.increment:
                value = float(cap.minimum) + float(
                    np.floor((value - float(cap.minimum)) / float(cap.increment) + 1e-9)
                ) * float(cap.increment)
            _, feature = find_feature(device, FEATURE_ALIASES["frame_rate"])
            if feature is None:
                raise RuntimeError("Acquisition frame-rate feature disappeared")
            feature.set(value)
            actual = float(feature.get())
            if not np.isclose(actual, value, rtol=1e-5, atol=1e-5):
                raise RuntimeError(f"Frame-rate readback {actual:g} does not match requested {value:g}")
            return actual

        def set_enable(enabled: bool) -> None:
            cap = inspect_feature(device, FEATURE_ALIASES["frame_rate_enable"])
            if not cap.available or not cap.readable or _frame_rate_enable_is_enabled(cap.value) is enabled:
                return
            _, feature = find_feature(device, FEATURE_ALIASES["frame_rate_enable"])
            if feature is None or not cap.writable:
                raise RuntimeError("Frame-rate control is disabled and cannot be enabled")
            values = {str(v).lower(): v for v in cap.values}
            candidate = values.get("on", True) if enabled else values.get("off", False)
            feature.set(candidate)

        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    raise RuntimeError("Camera closed during view mode change")
                rate_cap = inspect_feature(device, FEATURE_ALIASES["frame_rate"])
                enable_cap = inspect_feature(device, FEATURE_ALIASES["frame_rate_enable"])
                enabled_before = _frame_rate_enable_is_enabled(enable_cap.value) if enable_cap.readable else None
                if enable_cap.available and enabled_before is False:
                    set_enable(True)
                sensor_width = int(inspect_feature(device, FEATURE_ALIASES["width_max"]).value or previous_roi.width)
                sensor_height = int(inspect_feature(device, FEATURE_ALIASES["height_max"]).value or previous_roi.height)
                offset_x_cap = inspect_feature(device, FEATURE_ALIASES["offset_x"])
                offset_y_cap = inspect_feature(device, FEATURE_ALIASES["offset_y"])
                target = type(previous_roi)(
                    width,
                    height,
                    centered_offset(offset_x_cap, sensor_width, width),
                    centered_offset(offset_y_cap, sensor_height, height),
                )
                if width > previous_roi.width or height > previous_roi.height:
                    # The feature minimum is the conservative legal rate before
                    # enlargement; the old ROI's maximum says nothing about the
                    # larger geometry's legal ceiling.
                    minimum = rate_cap.minimum
                    if minimum is not None and float(previous_rate.value) > float(minimum):
                        write_rate(float(minimum))
                apply_roi(device, target)
                actual_roi = read_roi(device)
                if actual_roi != target:
                    raise RuntimeError(f"ROI readback {actual_roi} does not match requested {target}")
                refreshed = inspect_feature(device, FEATURE_ALIASES["frame_rate"])
                roi_cap = float(refreshed.maximum) if refreshed.maximum is not None else None
                if maximize_rate:
                    if roi_cap is None:
                        raise RuntimeError("Could not query frame-rate maximum after applying ROI")
                    final_rate = write_rate(roi_cap)
                else:
                    final_rate = write_rate(float(previous_rate.value))
                if enabled_before is False:
                    set_enable(False)
            device.start_streaming(self._frame_handler, buffer_count=self._DEFAULT_STREAM_BUFFER_COUNT)
            self.is_streaming = True
            self.frame_monitor.reset()
            self.frame_buffer.clear()
            self.fps_updated.emit(0.0)
            return {
                "roi": actual_roi,
                "aspect_ratio": actual_roi.width / actual_roi.height,
                "roi_cap_fps": roi_cap,
                "frame_rate_fps": final_rate,
            }
        except Exception as original:
            rollback_errors: list[str] = []
            try:
                with QMutexLocker(self.lock):
                    if not self.device:
                        raise RuntimeError("camera is no longer open")
                    current = inspect_feature(device, FEATURE_ALIASES["frame_rate"])
                    if current.maximum is not None and float(previous_rate.value) > float(current.maximum):
                        write_rate(float(current.maximum))
                    apply_roi(device, previous_roi)
                    write_rate(float(previous_rate.value))
                    if previous_enable.readable:
                        desired = _frame_rate_enable_is_enabled(previous_enable.value)
                        set_enable(desired)
            except Exception as rollback_error:  # noqa: BLE001 - retain original failure and report rollback failures.
                rollback_errors.append(f"rollback: {rollback_error}")
            try:
                if was_streaming and self.device:
                    device.start_streaming(self._frame_handler, buffer_count=self._DEFAULT_STREAM_BUFFER_COUNT)
                    self.is_streaming = True
                    self.frame_monitor.reset()
                    self.frame_buffer.clear()
                    self.fps_updated.emit(0.0)
            except Exception as restart_error:  # noqa: BLE001 - report failure to resume acquisition.
                self.is_streaming = False
                rollback_errors.append(f"restart: {restart_error}")
            detail = f"View mode change failed: {original}"
            if rollback_errors:
                detail += "; " + "; ".join(rollback_errors)
            raise RuntimeError(detail) from original

    def restore_view_mode_baseline(self, baseline: dict[str, Any]) -> None:
        """Best-effort normal-shutdown restoration of ROI/rate state only."""
        if not self.device or not self.is_streaming:
            return
        device = self.device
        self.is_streaming = False
        device.stop_streaming()
        try:
            with QMutexLocker(self.lock):
                current = inspect_feature(device, FEATURE_ALIASES["frame_rate"])
                original_rate = float(baseline["rate"])
                if current.maximum is not None and original_rate > float(current.maximum):
                    _, rate_feature = find_feature(device, FEATURE_ALIASES["frame_rate"])
                    if rate_feature is not None:
                        rate_feature.set(float(current.maximum))
                apply_roi(device, baseline["roi"])
                _, rate_feature = find_feature(device, FEATURE_ALIASES["frame_rate"])
                if rate_feature is not None:
                    rate_feature.set(original_rate)
                enable = baseline.get("enable")
                if enable is not None and enable.available and enable.readable:
                    _, enable_feature = find_feature(device, FEATURE_ALIASES["frame_rate_enable"])
                    if enable_feature is not None and enable.writable:
                        enable_feature.set(enable.value)
        finally:
            device.start_streaming(self._frame_handler, buffer_count=self._DEFAULT_STREAM_BUFFER_COUNT)
            self.is_streaming = True
            self.frame_monitor.reset()
            self.frame_buffer.clear()

    def get_frame_rate_capability(self) -> dict[str, Any] | None:
        """Refresh the frame-rate feature/range from the current ROI state."""
        if not self.device:
            return None
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return None
                rate = inspect_feature(self.device, FEATURE_ALIASES["frame_rate"])
                enabled = inspect_feature(self.device, FEATURE_ALIASES["frame_rate_enable"])
                return {
                    "feature": rate,
                    "enable_feature": enabled,
                    "enable_required": (
                        _frame_rate_enable_is_enabled(enabled.value) is False if enabled.readable else None
                    ),
                }
        except Exception as exc:  # noqa: BLE001 - optional feature query cannot break acquisition.
            logger.warning("Could not inspect frame-rate capability: %s", exc)
            return None

    def set_frame_rate(self, value_fps: float) -> bool:
        """Set an explicitly requested, freshly range-checked frame rate.

        This never runs during startup. Camera XML ranges are re-queried on each
        call because ROI changes can alter the legal maximum.
        """

        if self.is_streaming:
            logger.warning("Frame-rate changes require a stopped camera")
            return False

        def action():
            cap = inspect_feature(self.device, FEATURE_ALIASES["frame_rate"])
            if not cap.available or not cap.writable or cap.minimum is None or cap.maximum is None:
                raise ValueError("Acquisition frame-rate feature is unavailable or read-only")
            value = float(value_fps)
            if not cap.minimum <= value <= cap.maximum:
                raise ValueError(f"Requested frame rate {value:g} is outside [{cap.minimum}, {cap.maximum}]")
            if cap.increment:
                steps = (value - cap.minimum) / cap.increment
                if abs(steps - round(steps)) > 1e-5:
                    raise ValueError(f"Requested frame rate does not match increment {cap.increment}")
            _, rate_feature = find_feature(self.device, FEATURE_ALIASES["frame_rate"])
            original_rate = cap.value
            enable_cap = inspect_feature(self.device, FEATURE_ALIASES["frame_rate_enable"])
            changed_enable = enable_cap.available and _frame_rate_enable_is_enabled(enable_cap.value) is False
            enable_feature = None
            rate_write_attempted = False
            try:
                if changed_enable:
                    _, enable_feature = find_feature(self.device, FEATURE_ALIASES["frame_rate_enable"])
                    if not enable_cap.writable or enable_feature is None:
                        raise ValueError("Explicit frame-rate control is disabled and cannot be enabled")
                    values = {str(v).lower(): v for v in enable_cap.values}
                    enable_feature.set(values.get("on", True))
                    # Refresh range after enabling, just as after an ROI change.
                    cap = inspect_feature(self.device, FEATURE_ALIASES["frame_rate"])
                    if cap.minimum is not None and cap.maximum is not None and not cap.minimum <= value <= cap.maximum:
                        raise ValueError(
                            f"Requested frame rate {value:g} is outside refreshed range [{cap.minimum}, {cap.maximum}]"
                        )
                if rate_feature is None:
                    raise ValueError("Acquisition frame-rate feature disappeared")
                rate_write_attempted = True
                rate_feature.set(value)
                actual = rate_feature.get()
                if not np.isclose(float(actual), value, rtol=1e-5, atol=1e-5):
                    raise ValueError(f"Camera accepted frame rate {actual}, not {value}")
            except Exception:
                if rate_write_attempted and rate_feature is not None and original_rate is not None:
                    try:
                        rate_feature.set(original_rate)
                    except Exception as restore_error:  # noqa: BLE001 - log a failed rollback explicitly.
                        logger.error("Could not restore previous frame rate: %s", restore_error)
                if changed_enable and enable_feature is not None:
                    try:
                        values = {str(v).lower(): v for v in enable_cap.values}
                        enable_feature.set(values.get("off", False))
                    except Exception as restore_error:  # noqa: BLE001 - retain original failure, log rollback failure.
                        logger.error("Could not restore frame-rate enable state: %s", restore_error)
                raise

        return self._set_feature(action, "AcquisitionFrameRate")

    def get_gamma(self) -> float:
        return self._get_feature_value("Gamma", "gamma", 1.0)

    def _set_feature(self, func: Callable[[], Any], feature_name: str) -> bool:
        """Generic private helper to execute a feature-setting function within a lock."""
        if not self.device:
            logger.warning(f"Cannot set {feature_name}: Camera not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return False
                func()
                return True
        except VmbCameraError as e:
            error_msg = f"Error setting {feature_name}: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting {feature_name}: {e}"
            logger.exception(error_msg)
            self.error.emit(error_msg)
            return False

    def set_exposure(self, value_us: float) -> bool:
        def action():
            self.device.get_feature_by_name("ExposureAuto").set("Off")
            _, feat = find_feature(self.device, FEATURE_ALIASES["exposure"])
            if feat is None:
                raise VmbCameraError("Exposure feature is unavailable")
            min_val, max_val = feat.get_range()
            set_val = max(min_val, min(max_val, value_us))
            feat.set(set_val)
            self.settings.exposure_us = set_val
            self.settings.is_auto_exposure_on = False

        return self._set_feature(action, "Exposure")

    def set_gain(self, value_db: float) -> bool:
        def action():
            self.device.get_feature_by_name("GainAuto").set("Off")
            _, feat = find_feature(self.device, FEATURE_ALIASES["gain"])
            if feat is None:
                raise VmbCameraError("Gain feature is unavailable")
            min_val, max_val = feat.get_range()
            set_val = max(min_val, min(max_val, value_db))
            try:
                increment = feat.get_increment()
            except Exception:  # noqa: BLE001 - not all GenICam feature types expose increments.
                increment = None
            if increment:
                set_val = min_val + round((set_val - min_val) / increment) * increment
                set_val = max(min_val, min(max_val, set_val))
            feat.set(set_val)
            self.settings.gain_db = feat.get()
            self.settings.is_auto_gain_on = False

        return self._set_feature(action, "Gain")

    def set_gamma(self, value: float) -> bool:
        def action():
            feat = self.device.get_feature_by_name("Gamma")
            min_val, max_val = feat.get_range()
            set_val = max(min_val, min(max_val, value))
            feat.set(set_val)
            self.settings.gamma = set_val

        return self._set_feature(action, "Gamma")

    def set_auto_exposure_once(self) -> bool:
        def action():
            self.device.get_feature_by_name("ExposureAuto").set("Once")
            self.settings.is_auto_exposure_on = True

        return self._set_feature(action, "ExposureAuto Once")

    def set_auto_gain_once(self) -> bool:
        def action():
            self.device.get_feature_by_name("GainAuto").set("Once")
            self.settings.is_auto_gain_on = True

        return self._set_feature(action, "GainAuto Once")
