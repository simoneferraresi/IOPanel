import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

import numpy as np
from PySide6.QtCore import QMutex, QMutexLocker, QObject, QThread, Signal, Slot
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

logger = logging.getLogger("LabApp.camera")

# --- NEW: Type Alias for Clarity ---
CameraInfoDict: TypeAlias = dict[str, Any]
FeatureRange: TypeAlias = tuple[Any, Any] | None


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

    def update(self) -> float:
        """Adds a new timestamp and recalculates FPS if the update interval has passed."""
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

    def get_fps(self) -> float:
        """Returns the last calculated FPS value."""
        return self.fps


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
    _RECOVERY_DELAY_MS = 500

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

        self.frame_monitor = FrameRateMonitor()
        self.frame_buffer = FrameBuffer(max_size=3)
        self.settings = CameraSettings()
        self.setObjectName(f"VimbaCam_{self.identifier}")
        logger.info(f"VimbaCam instance created for identifier: {self.identifier} (Name: {self.camera_name})")

    @staticmethod
    def list_cameras() -> list[CameraInfoDict]:
        """
        Discovers all connected Vimba-compatible cameras.

        This static method scans the system using the Vimba API and returns
        a list of dictionaries, each containing essential information about
        a detected camera.

        Returns:
            A list of dictionaries, where each dictionary represents a camera
            and contains keys like 'id', 'serial', 'model', and 'name'.
        """
        cameras_info = []
        logger.info("Listing available Vimba cameras...")
        try:
            with VmbSystem.get_instance() as vmb:
                all_cams = vmb.get_all_cameras()
                logger.info(f"Total cameras detected: {len(all_cams)}")

                for i, cam in enumerate(all_cams):
                    info = {"numeric_index": i}
                    try:
                        # Use the high-level vmbpy getters
                        info["id"] = cam.get_id()
                        info["serial"] = cam.get_serial()
                        info["model"] = cam.get_model()
                        info["name"] = cam.get_name()
                        cameras_info.append(info)
                        logger.debug(f"  Found Cam {i}: ID={info['id']}, Serial={info.get('serial')}")
                    except Exception as e:
                        # If querying fails, try to get at least the ID for a better log message
                        cam_id_for_log = f"at index {i}"
                        try:
                            cam_id_for_log = cam.get_id()
                        except Exception:
                            # If even getting the ID fails, we stick with the index.
                            pass
                        logger.warning(f"Could not fully query camera '{cam_id_for_log}': {e}")

        except VmbSystemError as e:
            logger.error(f"Vimba system error while listing cameras: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred while listing cameras: {e}")
        return cameras_info

    # --- Vimba Frame Callback Handler ---
    def _frame_handler(self, cam: Camera, stream: Stream, frame: Frame):
        """
        Callback executed by Vimba for each incoming frame.
        Processes the frame and emits signals.
        IMPORTANT: This runs in a Vimba internal thread, not the Qt GUI thread.
        """
        try:
            if self._is_frame_valid(frame):
                self._process_and_emit_frame(frame)
        except Exception as e:
            # This top-level catch handles unexpected errors during processing
            logger.exception(
                f"Handler {self.camera_name}: Unhandled error in frame processing: {e}"
            )
        finally:
            # Re-queue the frame regardless of whether it was valid or if processing failed.
            # This is the most robust pattern.
            self._requeue_frame(cam, frame)

    def _is_frame_valid(self, frame: Frame) -> bool:
        """Checks frame status. Returns True if frame is complete, False otherwise."""
        try:
            frame_status = frame.get_status()
            if frame_status == FrameStatus.Complete:
                return True
            else:
                logger.warning(
                    f"Received invalid frame for {self.camera_name}: {frame_status.name}"
                )
                return False
        except VmbCameraError as e:
            logger.error(f"Error getting frame status for {self.camera_name}: {e}")
            return False

    def _process_and_emit_frame(self, frame: Frame):
        """
        Converts a valid frame to a numpy array, processes it (e.g., flip),
        and emits the necessary signals for the GUI.
        This method assumes the frame is valid.
        """
        # Convert frame to OpenCV image
        current_image = frame.as_opencv_image()

        if current_image is None or current_image.size == 0:
            logger.warning(
                f"Handler {self.camera_name}: Frame from as_opencv_image() is None or empty."
            )
            return

                # Apply horizontal flip if configured
                if self.flip_horizontal:
                    current_image = cv2.flip(current_image, 1)

                # Update frame buffer. Must copy as the underlying buffer will be reused by Vimba.
                processed_image = current_image.copy()
                self.frame_buffer.add_frame(processed_image)

                # Emit signals for the GUI
                self.new_frame.emit(processed_image)
                self.fps_updated.emit(self.frame_monitor.update())
        except Exception as e:
            logger.exception(f"Handler {self.camera_name}: Unhandled error in frame processing: {e}")
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
        except VmbCameraError as e:
            logger.error(f"Vimba error during camera open sequence: {e}")
            self.error.emit(f"Open error: {e}")
            self.close()
            return False
        except Exception as e:
            logger.exception(f"Unexpected error during camera open sequence: {e}")
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

    @Slot()
    def attempt_recovery(self):
        """Attempts to close and reopen the camera. Public slot for recovery mechanisms."""
        logger.warning(f"Executing recovery attempt for {self.camera_name}...")
        self.close()
        # Use QThread.msleep() for a non-blocking delay
        QThread.msleep(self._RECOVERY_DELAY_MS)

        # After closing, _is_closing is True. We must reset it before opening again.
        # The open() method handles this, so we just need to call it.
        if self.open():
            logger.info(f"Recovery successful for {self.camera_name}.")
            # Use a more descriptive message for the user
            self.error.emit(f"Connection to '{self.camera_name}' restored.")
        else:
            logger.error(f"Recovery failed for {self.camera_name}.")
            self.error.emit(f"Failed to reconnect '{self.camera_name}'.")

    def _open_device_internal(self) -> bool:
        """Internal: Opens device. Assumes VimbaSystem is ACTIVE."""
        try:
            vmb = VmbSystem.get_instance()
            cam_opened = vmb.get_camera_by_id(self.identifier)
            cam_opened.__enter__()
            self.device = cam_opened
            logger.info(f"Successfully opened camera device: {self.camera_name}")
            self._configure_camera()
            self._update_settings_cache()
            return True
        except VmbCameraError as e:
            error_msg = f"Failed to open camera {self.camera_name}: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            self.device = None
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
                except VmbCameraError as e:
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
            except VmbCameraError:
                logger.info("Gamma feature not available/writable.")

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
        except VmbCameraError:
            pass  # Features may not exist

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
                feat = self.device.get_feature_by_name(feature_name)
                return feat.get_range() if feat.is_readable() else None
        except VmbCameraError as e:
            logger.warning(f"Could not get range for '{feature_name}': {e}")
            return None

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
        except VmbCameraError as e:
            logger.warning(f"Error getting feature '{feature_name}': {e}")
            return getattr(self.settings, cache_attr, default)

    def get_exposure(self) -> float:
        return self._get_feature_value("ExposureTimeAbs", "exposure_us", 10000.0)

    def get_gain(self) -> float:
        return self._get_feature_value("Gain", "gain_db", 0.0)

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
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False

    def set_exposure(self, value_us: float) -> bool:
        def action():
            self.device.get_feature_by_name("ExposureAuto").set("Off")
            feat = self.device.get_feature_by_name("ExposureTimeAbs")
            min_val, max_val = feat.get_range()
            set_val = max(min_val, min(max_val, value_us))
            feat.set(set_val)
            self.settings.exposure_us = set_val
            self.settings.is_auto_exposure_on = False

        return self._set_feature(action, "Exposure")

    def set_gain(self, value_db: float) -> bool:
        def action():
            self.device.get_feature_by_name("GainAuto").set("Off")
            feat = self.device.get_feature_by_name("Gain")
            min_val, max_val = feat.get_range()
            set_val = max(min_val, min(max_val, value_db))
            feat.set(set_val)
            self.settings.gain_db = set_val
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
