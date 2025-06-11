import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import cv2
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
    intersect_pixel_formats,
)

logger = logging.getLogger("LabApp.camera")


@dataclass
class CameraSettings:
    exposure_us: float = 10000.0
    gamma: float = 1.0
    gain_db: float = 0.0
    pixel_format: PixelFormat | None = None
    is_auto_exposure_on: bool = False
    is_auto_gain_on: bool = False


class FrameRateMonitor:
    def __init__(self, window_size: int = 30):
        self.timestamps: deque[float] = deque(maxlen=window_size)
        self.fps: float = 0.0
        self.last_fps_update_time: float = 0.0
        self.update_interval: float = 0.5

    def update(self) -> float:
        now = time.monotonic()
        self.timestamps.append(now)
        if (now - self.last_fps_update_time >= self.update_interval) and len(self.timestamps) >= 2:
            time_diff = self.timestamps[-1] - self.timestamps[0]
            if time_diff > 1e-9:
                self.fps = (len(self.timestamps) - 1) / time_diff
            else:
                self.fps = 0.0
            self.last_fps_update_time = now
        return self.fps

    def get_fps(self) -> float:
        return self.fps


class FrameBuffer:
    def __init__(self, max_size: int = 3):
        self.buffer: deque[np.ndarray] = deque(maxlen=max_size)
        self.lock = QMutex()

    def add_frame(self, frame: np.ndarray):
        with QMutexLocker(self.lock):
            self.buffer.append(frame)

    def get_latest_frame(self) -> np.ndarray | None:
        with QMutexLocker(self.lock):
            if not self.buffer:
                return None
            return self.buffer[-1].copy()

    def clear(self):
        with QMutexLocker(self.lock):
            self.buffer.clear()


class VimbaCam(QObject):
    """Manages a Vimba-compatible camera.

    This class abstracts the Vimba API details for a single camera, handling
    connection, streaming, and settings adjustment. It operates on its own by
    registering a callback (`_frame_handler`) with the Vimba transport layer,
    which runs in a separate, high-priority Vimba thread.

    It emits signals for new frames, FPS updates, connection status, and errors,
    making it suitable for integration into a Qt application.

    Attributes:
        new_frame (Signal): Emits a numpy.ndarray for each valid new frame.
        fps_updated (Signal): Emits the calculated frames-per-second (float).
        connected (Signal): Emitted when the camera is successfully opened.
        disconnected (Signal): Emitted when the camera is closed.
        error (Signal): Emits an error message string when issues occur.
    """

    _DEFAULT_STREAM_BUFFER_COUNT = 5
    _RECOVERY_DELAY_MS = 500

    # Signals emitted from the Vimba callback thread (via invokeMethod or direct if safe)
    # We'll emit the numpy array directly
    new_frame = Signal(np.ndarray)
    fps_updated = Signal(float)
    # Signals related to connection state (emitted from main thread methods)
    connected = Signal()
    disconnected = Signal()
    error = Signal(str)

    def __init__(
        self,
        identifier: str,
        camera_name: str | None = None,
        flip_horizontal: bool = False,
        parent: QObject | None = None,
    ):
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
        self.setObjectName(f"VimbaCam_{self.identifier}")  # Set object name
        logger.info(f"VimbaCam instance created for identifier: {self.identifier} (Name: {self.camera_name})")

    @staticmethod
    def list_cameras() -> list[dict[str, Any]]:
        cameras_info = []
        logger.info("Listing available Vimba cameras...")
        try:
            with VmbSystem.get_instance() as vmb:
                all_cams = vmb.get_all_cameras()
                logger.info(f"Total cameras detected: {len(all_cams)}")
                for i, cam in enumerate(all_cams):
                    info = {"numeric_index": i}
                    try:
                        with cam:
                            info["id"] = cam.get_id()
                            info["serial"] = cam.get_serial_number()
                            info["model"] = cam.get_model_name()
                            info["name"] = cam.get_name()
                            cameras_info.append(info)
                        logger.debug(f"  Found Cam {i}: ID={info['id']}, Serial={info['serial']}")
                    except Exception as e:
                        logger.warning(f"Could not fully query cam {i}: {e}")
        except Exception as e:
            logger.error(f"Could not list cameras: {e}")
        return cameras_info

    def _set_auto_mode_once(self, feature_name: str) -> bool:
        """Private helper to set an 'Auto' feature to 'Once' mode."""
        if not self.device:
            logger.warning(f"Cannot set {feature_name}: Camera not connected.")
            return False

        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return False

                feat_auto = self.device.get_feature_by_name(feature_name)

                if not feat_auto.is_writeable():
                    logger.warning(f"{feature_name} feature is not writable.")
                    return False

                available_entries = [str(e) for e in feat_auto.get_available_entries()]
                if "Once" not in available_entries:
                    logger.error(f"{feature_name} mode 'Once' is not available. Options: {available_entries}")
                    return False

                feat_auto.set("Once")  # vmbpy allows setting by string name
                logger.info(f"{feature_name} successfully set to 'Once'.")
                return True

        except VmbCameraError as e:
            error_msg = f"VimbaError setting {feature_name} 'Once': {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting {feature_name}: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False

    # --- Vimba Frame Callback Handler ---
    def _frame_handler(self, cam: Camera, stream: Stream, frame: Frame):
        """
        Callback executed by Vimba for each incoming frame.
        """
        # --- FIX: Add a guard at the very top of the handler ---
        if self._is_closing:
            return  # Immediately exit if a close operation is in progress

        try:
            if self._is_frame_valid(frame):
                self._process_and_emit_frame(frame)
        except Exception as e:
            # This top-level catch handles unexpected errors during processing
            logger.exception(f"Handler {self.camera_name}: Unhandled error in frame processing: {e}")
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
                logger.warning(f"Received invalid frame for {self.camera_name}: {frame_status.name}")
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
            logger.warning(f"Handler {self.camera_name}: Frame from as_opencv_image() is None or empty.")
            return

        # Apply horizontal flip if configured
        if self.flip_horizontal:
            current_image = cv2.flip(current_image, 1)

        # Update frame buffer. Must copy as the underlying buffer will be reused by Vimba.
        processed_image = current_image.copy()
        self.frame_buffer.add_frame(processed_image)

        # --- Emit signals (must be thread-safe to Qt) ---
        self.new_frame.emit(processed_image)

        # Update FPS monitor and emit signal
        fps = self.frame_monitor.update()
        self.fps_updated.emit(fps)

    def _requeue_frame(self, cam: Camera, frame: Frame):
        """
        CRITICAL: Always attempts to queue the frame back to the Vimba acquisition engine.
        This must be called for every frame that is received, regardless of its status.
        """
        try:
            cam.queue_frame(frame)
        except VmbCameraError as e:
            logger.error(f"Handler {self.camera_name}: CRITICAL - Failed to queue frame back: {e}")
            # This is serious, might indicate stream is broken.
            self.error.emit(f"CRITICAL Frame queueing error: {e}")
        except Exception as e_unexp:
            logger.exception(f"Handler {self.camera_name}: CRITICAL - Unexpected error queueing frame: {e_unexp}")
            self.error.emit("CRITICAL Unexpected frame queueing error")

    def open(self) -> bool:
        """Opens the camera and starts streaming.

        This method orchestrates the entire camera opening sequence. It finds and
        opens the physical device, configures it with optimal default settings
        (e.g., pixel format, acquisition mode), and starts the Vimba streaming
        engine with the internal frame handler.

        Returns:
            bool: True if the camera was successfully opened and is streaming,
                  False otherwise. Any failures will emit an `error` signal.
        """
        logger.info(f"Attempting to open camera: {self.camera_name} (ID: {self.identifier})")
        self._is_closing = False
        if self.device:
            logger.warning(f"Camera {self.camera_name} already open.")
            return True

        # Assumes VimbaSystem was started externally in app.py
        try:
            if not self._open_device_internal():
                return False
            # Start streaming using the internal frame handler
            self._start_stream_internal()
            if self.is_streaming:
                logger.info(f"Camera {self.camera_name} opened and streaming started.")
                return True
            else:
                logger.error(f"Camera {self.camera_name} failed to start streaming.")
                self.close()
                return False
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

    def _open_device_internal(self) -> bool:
        """Internal: Opens device. Assumes VimbaSystem is ACTIVE."""
        if self.device:
            return True
        logger.debug(f"_open_device_internal for {self.identifier}")
        try:
            vmb = VmbSystem.get_instance()
            cam_opened = vmb.get_camera_by_id(self.identifier)
            cam_opened.__enter__()
            with QMutexLocker(self.lock):
                self.device = cam_opened
            logger.info(f"Successfully opened camera: {self.camera_name}")
            self._configure_camera()
            self._update_settings_cache()
            self.connected.emit()
            return True
        except VmbCameraError as e:
            error_msg = f"Failed to open camera {self.camera_name}: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            with QMutexLocker(self.lock):
                self.device = None
            return False

    def _configure_camera(self):
        logger.debug(f"Configuring default settings for {self.camera_name}...")
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    logger.error("Cannot configure: device not open.")
                    return
                try:
                    feat = self.device.get_feature_by_name("AcquisitionMode")
                    (feat.set("Continuous") if feat.is_writeable() else logger.warning("AcqMode not writeable"))
                    logger.debug("Set AcqMode.")
                except VmbCameraError as e:
                    logger.warning(f"Could not set AcqMode: {e}")
                try:
                    feat = self.device.get_feature_by_name("TriggerMode")
                    (feat.set("Off") if feat.is_writeable() else logger.warning("TrigMode not writeable"))
                    logger.debug("Set TrigMode Off.")
                except VmbCameraError as e:
                    logger.warning(f"Could not set TrigMode: {e}")
                try:
                    feat = self.device.get_feature_by_name("ExposureAuto")
                    (feat.set("Off") if feat.is_writeable() and "Off" in feat.get_available_entries() else None)
                    self.settings.is_auto_exposure_on = False
                    logger.debug("Set ExposureAuto Off.")
                except VmbCameraError as e:
                    logger.warning(f"Could not disable ExposureAuto: {e}")
                try:
                    feat = self.device.get_feature_by_name("GainAuto")
                    (feat.set("Off") if feat.is_writeable() and "Off" in feat.get_available_entries() else None)
                    self.settings.is_auto_gain_on = False
                    logger.debug("Set GainAuto Off.")
                except VmbCameraError as e:
                    logger.warning(f"Could not disable GainAuto: {e}")
                try:
                    feat = self.device.get_feature_by_name("Gamma")
                    if feat.is_writeable():
                        min_g, max_g = feat.get_range()
                        target_gamma = max(min_g, min(max_g, 1.0))
                        feat.set(target_gamma)
                        self.settings.gamma = target_gamma
                        logger.debug(f"Set Gamma to {target_gamma:.2f}.")
                except VmbCameraError as e:
                    logger.info(f"Gamma feature not available/writable: {e}")
                self._set_pixel_format()
        except Exception as e:
            logger.exception(f"Unexpected error configuring {self.camera_name}: {e}")
            self.error.emit(f"Config error: {e}")

    def _set_pixel_format(self):
        """
        Determines and sets the best OpenCV-compatible pixel format.
        This method now guarantees that `self.is_mono` will be either True or False.
        If a suitable format cannot be found or set, it raises a VmbCameraError.
        """
        if not self.device:
            raise VmbCameraError("Cannot set pixel format: device not open.")

        try:
            dev_formats = self.device.get_pixel_formats()
            # Find all formats that are compatible with Vimba's OpenCV transform
            cv_formats = intersect_pixel_formats(dev_formats, OPENCV_PIXEL_FORMATS)

            if not cv_formats:
                raise VmbCameraError("No OpenCV-compatible pixel formats found on this camera.")

            # --- Determine the best format to use ---
            preferred_format = None
            is_mono = None

            # 1. Prioritize monochrome formats for simplicity and performance
            mono_cv = intersect_pixel_formats(cv_formats, MONO_PIXEL_FORMATS)
            if mono_cv:
                # Prefer 8-bit mono if available
                preferred_format = next((f for f in mono_cv if f.name == "Mono8"), mono_cv[0])
                is_mono = True
                logger.info(f"Selecting Mono format: {preferred_format.name}")

            # 2. If no mono, look for common color formats
            else:
                color_cv = intersect_pixel_formats(cv_formats, COLOR_PIXEL_FORMATS)
                if color_cv:
                    # Prefer 8-bit BGR or RGB as they are most common for OpenCV
                    preferred_format = next((f for f in color_cv if f.name in ["BGR8", "RGB8"]), None)
                    if not preferred_format:
                        # Fallback to the first available color format
                        preferred_format = color_cv[0]
                    is_mono = False
                    logger.info(f"Selecting Color format: {preferred_format.name}")

            # 3. If no suitable mono or color format was found, this is an error
            if preferred_format is None or is_mono is None:
                raise VmbCameraError(
                    f"Could not find a supported mono or color format. Available CV formats: {[f.name for f in cv_formats]}"
                )

            # --- Set the format and update state ---
            self.device.set_pixel_format(preferred_format)
            self.settings.pixel_format = preferred_format
            self.is_mono = is_mono  # This is now guaranteed to be True or False
            logger.info(f"Pixel format set to: {self.settings.pixel_format.name}. Is Mono: {self.is_mono}")

        except VmbCameraError as e:
            # Re-raise Vimba errors to be caught by the calling `open` method
            logger.error(f"Failed to configure pixel format for {self.camera_name}: {e}")
            self.error.emit(f"Pixel format error: {e}")
            raise  # Let the caller handle this failure
        except Exception as e:
            # Wrap unexpected errors in a VmbCameraError
            logger.error(f"Unexpected pixel format error: {e}", exc_info=True)
            self.error.emit(f"Unexpected pixel format error: {e}")
            raise VmbCameraError(f"Unexpected error setting pixel format: {e}") from e

    def _update_settings_cache(self):
        logger.debug(f"Updating settings cache for {self.camera_name}...")
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return
                try:
                    feat = self.device.get_feature_by_name("ExposureTimeAbs")
                    self.settings.exposure_us = feat.get()
                except VmbCameraError as e:
                    logger.warning(f"Could not cache exposure: {e}")
                try:
                    feat_auto = self.device.get_feature_by_name("ExposureAuto")
                    self.settings.is_auto_exposure_on = feat_auto.get() != "Off"
                except VmbCameraError:
                    logger.debug("Feature ExposureAuto not found/readable.")
                try:
                    feat = self.device.get_feature_by_name("Gamma")
                    self.settings.gamma = feat.get()
                except VmbCameraError as e:
                    logger.warning(f"Could not cache gamma: {e}")
                try:  # Gain
                    gain_val = 0.0
                    try:
                        feat_gain = self.device.get_feature_by_name("Gain")
                        gain_val = feat_gain.get()
                    except VmbCameraError:
                        logger.debug("Feature 'Gain' not found/readable.")
                    self.settings.gain_db = gain_val
                    try:
                        feat_auto = self.device.get_feature_by_name("GainAuto")
                        self.settings.is_auto_gain_on = feat_auto.get() != "Off"
                    except VmbCameraError:
                        logger.debug("Feature 'GainAuto' not found/readable.")
                except Exception as e_gain:
                    logger.warning(f"Could not cache gain settings: {e_gain}")
        except Exception as e:
            logger.error(f"Unexpected error updating settings cache: {e}", exc_info=True)

    def _start_stream_internal(self):
        """Internal: Starts Vimba stream using the frame handler callback."""
        with QMutexLocker(self.lock):
            if self._is_closing or not self.device:
                logger.warning("Cannot start stream, closing or no device.")
                self.is_streaming = False
                return
            if self.is_streaming:
                logger.warning("Streaming already started.")
                return

            logger.debug(f"_start_stream_internal for {self.camera_name}")

            # Assume failure until proven otherwise
            self.is_streaming = False

            try:
                self.device.start_streaming(self._frame_handler, buffer_count=self._DEFAULT_STREAM_BUFFER_COUNT)
                # If we reach here, it was successful
                self.is_streaming = True
                logger.info(f"Vimba streaming started with callback for {self.camera_name}.")

            except VmbCameraError as e:
                logger.error(f"Failed to start Vimba streaming: {e}")
                self.error.emit(f"Streaming start error: {e}")
                # The 'finally' block will handle cleanup

            except Exception as e:
                logger.error(f"Unexpected error starting stream: {e}", exc_info=True)
                self.error.emit(f"Unexpected streaming start error: {e}")
                # The 'finally' block will handle cleanup

            finally:
                # This block runs regardless of whether an exception occurred or not.
                # If streaming failed, we attempt to clean up.
                if not self.is_streaming and self.device:
                    logger.debug("Attempting stream cleanup after a start failure.")
                    try:
                        self.device.stop_streaming()
                    except Exception as cleanup_e:  # <--- CORRECTED: Specific exception
                        logger.error(f"Error during cleanup after a failed stream start: {cleanup_e}")
                        # Pass is appropriate as we don't want to raise a new exception here.
                        pass

    def _stop_stream_internal(self):
        """Internal: Stops Vimba stream. Assumes VimbaSystem is ACTIVE."""
        logger.debug(f"Stopping stream internally for {self.camera_name}...")
        # No separate QThread to stop anymore

        with QMutexLocker(self.lock):
            # Check if streaming flag is set before trying to stop
            if self.device and self.is_streaming:
                try:
                    logger.debug("Stopping Vimba hardware streaming...")
                    self.device.stop_streaming()
                    logger.info(f"Vimba streaming stopped for {self.camera_name}.")
                except VmbCameraError as e:
                    logger.error(f"Error stopping Vimba streaming: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error stopping Vimba streaming: {e}", exc_info=True)
            elif self.device:
                logger.debug("Stop stream called, but streaming was not marked as active.")
            else:
                logger.debug("Stop stream called, but device is not open.")
        self.is_streaming = False  # Ensure flag is reset
        logger.debug(f"Stream stopping sequence complete for {self.camera_name}.")

    def close_device_internal(self):
        """Internal: Closes device. Assumes VimbaSystem is ACTIVE."""
        logger.debug(f"_close_device_internal for {self.camera_name}")
        with QMutexLocker(self.lock):
            if not self.device:
                return True
            logger.info(f"Closing camera device: {self.camera_name}")
            try:
                self.device.__exit__(None, None, None)
                logger.info(f"Camera device {self.camera_name} closed successfully.")
                self.device = None
                self.is_mono = None
                self.disconnected.emit()
                return True
            except VmbCameraError as e:
                logger.error(f"Vimba error closing device: {e}")
                self.error.emit(f"Device close error: {e}")
                self.device = None
                self.is_mono = None
                self.disconnected.emit()
                return False
            except Exception as e:
                logger.error(f"Unexpected error during device close: {e}", exc_info=True)
                self.device = None
                self.is_mono = None
                self.disconnected.emit()
                return False

    def close(self):
        """Stops streaming and closes the camera device connection cleanly."""
        # --- FIX: Set the closing flag immediately and under a lock ---
        with QMutexLocker(self.lock):
            if self._is_closing:
                logger.debug("Close already in progress.")
                return
            self._is_closing = True

        logger.info(f"Initiating close sequence for camera: {self.camera_name}")

        # Vimba's stop_streaming is synchronous and will wait for callbacks to finish.
        # This is the correct order of operations.
        self._stop_stream_internal()
        self.close_device_internal()

        self.frame_buffer.clear()
        logger.info(f"Close sequence finished for camera: {self.camera_name}")

    @Slot()
    def attempt_recovery(self):
        """Attempts to close and reopen the camera. Public slot for recovery mechanisms."""
        logger.warning(f"Executing recovery attempt for {self.camera_name}...")
        self.close()
        # Use QThread.msleep() for a non-blocking delay if this is called from the GUI thread
        QThread.msleep(self._RECOVERY_DELAY_MS)
        if not self._is_closing:
            self.open()
            if self.is_streaming:
                logger.info(f"Recovery successful for {self.camera_name}.")
                # Use a more descriptive message for the user
                self.error.emit(f"Connection to '{self.camera_name}' restored.")
            else:
                logger.error(f"Recovery failed for {self.camera_name}.")
                self.error.emit(f"Failed to reconnect '{self.camera_name}'.")

    # --- Feature Access Methods ---
    def get_latest_frame(self) -> np.ndarray | None:
        return self.frame_buffer.get_latest_frame()

    def get_setting(self, setting_name: str) -> Any:
        return getattr(self.settings, setting_name, None)

    def get_feature_min_max(self, feature_name: str, default_value: float, is_max: bool = False) -> float:
        if not self.device:
            return default_value
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return default_value
                feat = self.device.get_feature_by_name(feature_name)
                if feat.is_readable():
                    min_val, max_val = feat.get_range()
                    return max_val if is_max else min_val
                else:
                    logger.warning(f"Feature '{feature_name}' not readable.")
                    return default_value
        except VmbCameraError as e:
            logger.warning(f"Could not get range for '{feature_name}': {e}")
            return default_value
        except Exception as e:
            logger.error(
                f"Unexpected error getting range for '{feature_name}': {e}",
                exc_info=True,
            )
            return default_value

    def get_exposure(self) -> float | None:
        if not self.device:
            return self.settings.exposure_us
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return self.settings.exposure_us
                feat = self.device.get_feature_by_name("ExposureTimeAbs")
                val = feat.get()
                self.settings.exposure_us = val
                return val
        except VmbCameraError as e:
            logger.error(f"Error getting exposure: {e}")
            self.error.emit(f"Exposure read error: {e}")
            return self.settings.exposure_us
        except Exception as e:
            logger.error(f"Unexpected error getting exposure: {e}", exc_info=True)
            self.error.emit(f"Unexpected exposure read error: {e}")
            return self.settings.exposure_us

    def get_gamma(self) -> float | None:  # ...
        if not self.device:
            return self.settings.gamma
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return self.settings.gamma
                feat = self.device.get_feature_by_name("Gamma")
                val = feat.get()
                self.settings.gamma = val
                return val
        except VmbCameraError as e:
            logger.info(f"Gamma not available/readable: {e}")
            return self.settings.gamma
        except Exception as e:
            logger.error(f"Unexpected error getting gamma: {e}", exc_info=True)
            self.error.emit(f"Unexpected gamma read error: {e}")
            return self.settings.gamma

    def get_gain(self) -> float | None:  # ...
        if not self.device:
            return self.settings.gain_db
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return self.settings.gain_db
                try:
                    feat = self.device.get_feature_by_name("Gain")
                    val = feat.get()
                    self.settings.gain_db = val
                    return val
                except VmbCameraError:
                    logger.debug("Feature 'Gain' not found/readable.")
                    return self.settings.gain_db
        except Exception as e:
            logger.error(f"Unexpected error getting gain: {e}", exc_info=True)
            self.error.emit(f"Unexpected gain read error: {e}")
            return self.settings.gain_db

    def _set_feature_value(
        self,
        feature_name: str,
        value: float,
        setting_attr: str,
        auto_feature_name: str | None = None,
        auto_setting_attr: str | None = None,
    ) -> bool:
        """
        Generic helper to set a camera feature's value, optionally disabling its 'Auto' mode.
        This method is thread-safe.

        Args:
            feature_name: The Vimba name of the feature to set (e.g., "ExposureTimeAbs").
            value: The desired value for the feature.
            setting_attr: The attribute name in `self.settings` to update (e.g., "exposure_us").
            auto_feature_name: The Vimba name of the corresponding 'Auto' feature (e.g., "ExposureAuto").
            auto_setting_attr: The attribute name in `self.settings` for the auto status.

        Returns:
            True on success, False on failure.
        """
        if not self.device:
            logger.warning(f"Cannot set {feature_name}: Camera not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return False

                # 1. Disable Auto mode if applicable
                if auto_feature_name:
                    try:
                        feat_auto = self.device.get_feature_by_name(auto_feature_name)
                        if feat_auto.is_writeable() and "Off" in feat_auto.get_available_entries():
                            feat_auto.set("Off")
                            if auto_setting_attr:
                                setattr(self.settings, auto_setting_attr, False)
                    except VmbCameraError as e_auto:
                        logger.warning(f"Could not disable {auto_feature_name}: {e_auto}")

                # 2. Set the primary feature value
                feat = self.device.get_feature_by_name(feature_name)
                if not feat.is_writeable():
                    logger.warning(f"Feature '{feature_name}' is not writable.")
                    return False

                min_val, max_val = feat.get_range()
                set_val = max(min_val, min(max_val, value))
                feat.set(set_val)

                # 3. Update the local settings cache
                setattr(self.settings, setting_attr, set_val)
                logger.debug(f"Set {feature_name} to {set_val} (requested {value})")
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
        return self._set_feature_value(
            feature_name="ExposureTimeAbs",
            value=value_us,
            setting_attr="exposure_us",
            auto_feature_name="ExposureAuto",
            auto_setting_attr="is_auto_exposure_on",
        )

    def set_gamma(self, value: float) -> bool:
        # Gamma typically does not have an "Auto" mode to disable
        return self._set_feature_value(feature_name="Gamma", value=value, setting_attr="gamma")

    def set_gain(self, value_db: float) -> bool:
        return self._set_feature_value(
            feature_name="Gain",
            value=value_db,
            setting_attr="gain_db",
            auto_feature_name="GainAuto",
            auto_setting_attr="is_auto_gain_on",
        )

    def set_auto_exposure_once(self) -> bool:
        if self._set_auto_mode_once("ExposureAuto"):
            self.settings.is_auto_exposure_on = True
            return True
        return False

    def set_auto_gain_once(self) -> bool:
        if self._set_auto_mode_once("GainAuto"):
            self.settings.is_auto_gain_on = True
            return True
        return False
