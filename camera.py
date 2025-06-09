import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import cv2
import numpy as np
from PySide6.QtCore import QMutex, QMutexLocker, QObject, Signal, Slot
from vmbpy import *

logger = logging.getLogger("LabApp.camera")


@dataclass
class CameraSettings:
    exposure_us: float = 10000.0
    gamma: float = 1.0
    gain_db: float = 0.0
    pixel_format: Optional[PixelFormat] = None
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
        if (now - self.last_fps_update_time >= self.update_interval) and len(
            self.timestamps
        ) >= 2:
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

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with QMutexLocker(self.lock):
            if not self.buffer:
                return None
            return self.buffer[-1].copy()

    def clear(self):
        with QMutexLocker(self.lock):
            self.buffer.clear()


class VimbaCam(QObject):  # Inherit QObject for signals
    # Signals emitted from the Vimba callback thread (via invokeMethod or direct if safe)
    # We'll emit the numpy array directly
    new_frame = Signal(object)
    fps_updated = Signal(float)
    # Signals related to connection state (emitted from main thread methods)
    connected = Signal()
    disconnected = Signal()
    error = Signal(str)

    def __init__(
        self,
        identifier: str,
        camera_name: Optional[str] = None,
        flip_horizontal: bool = False,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        if not identifier:
            raise ValueError("Camera identifier cannot be empty.")
        self.identifier = identifier
        self.camera_name = camera_name or identifier
        self.flip_horizontal = flip_horizontal
        self.device: Optional[Camera] = None
        self.lock = QMutex()
        self.is_mono: Optional[bool] = None
        self.is_streaming: bool = False
        self._is_closing: bool = False
        self.frame_monitor = FrameRateMonitor()
        self.frame_buffer = FrameBuffer(max_size=3)
        self.settings = CameraSettings()
        self.setObjectName(f"VimbaCam_{self.identifier}")  # Set object name
        logger.info(
            f"VimbaCam instance created for identifier: {self.identifier} (Name: {self.camera_name})"
        )

    @staticmethod
    def list_cameras() -> List[Dict[str, Any]]:
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
                        logger.debug(
                            f"  Found Cam {i}: ID={info['id']}, Serial={info['serial']}"
                        )
                    except Exception as e:
                        logger.warning(f"Could not fully query cam {i}: {e}")
        except Exception as e:
            logger.error(f"Could not list cameras: {e}")
        return cameras_info

    # --- Vimba Frame Callback Handler ---
    def _frame_handler(self, cam: Camera, stream: Stream, frame: Frame):
        """
        Callback executed by Vimba for each incoming frame.
        Processes the frame and emits signals.
        IMPORTANT: This runs in a Vimba internal thread, not the Qt GUI thread.
        """
        # Check frame status first
        try:
            frame_status = frame.get_status()
            if frame_status != FrameStatus.Complete:
                logger.warning(
                    f"Received incomplete frame for {self.camera_name}: {frame_status}"
                )
                # Queue back incomplete frames immediately
                cam.queue_frame(frame)
                return
        except VmbCameraError as e_stat:
            logger.error(f"Error getting frame status for {self.camera_name}: {e_stat}")
            # Attempt to queue back even if status check failed
            try:
                cam.queue_frame(frame)
            except:
                pass
            return
        except Exception as e_stat_unexp:
            logger.error(f"Unexpected error getting frame status: {e_stat_unexp}")
            try:
                cam.queue_frame(frame)
            except:
                pass
            return

        # --- Process Complete Frame ---
        try:
            # Convert frame to OpenCV image
            current_image = frame.as_opencv_image()

            if current_image is not None and current_image.size > 0:
                min_val, max_val = np.min(current_image), np.max(current_image)
                # logger.debug(
                #     f"Handler {self.camera_name}: Frame RAW min/max: {min_val}/{max_val}, dtype: {current_image.dtype}, shape: {current_image.shape}"
                # )
                if np.all(current_image == 0):  # Specifically check if it's ALL black
                    logger.warning(
                        f"Handler {self.camera_name}: Frame from as_opencv_image() is ALL BLACK!"
                    )
            elif current_image is not None:  # size is 0 but not None
                logger.warning(
                    f"Handler {self.camera_name}: Frame from as_opencv_image() has size 0. Shape: {current_image.shape}"
                )
            else:  # current_image is None
                logger.warning(
                    f"Handler {self.camera_name}: Frame from as_opencv_image() is None."
                )

            # Apply horizontal flip if configured
            if self.flip_horizontal:
                current_image = cv2.flip(current_image, 1)

            # Update frame buffer (optional, but can be useful for latest frame access)
            # Need to copy as the underlying buffer will be reused by Vimba
            processed_image = current_image.copy()
            self.frame_buffer.add_frame(processed_image)

            # --- Emit signals (must be thread-safe to Qt) ---
            # Directly emitting the numpy array should be safe if the receiver
            # makes a copy or processes it quickly in the Qt event loop.
            self.new_frame.emit(processed_image)  # Emit the processed numpy array

            # Update FPS monitor
            fps = self.frame_monitor.update()
            # Emit FPS update (Qt signals are thread-safe)
            self.fps_updated.emit(fps)

        except VmbCameraError as e_proc:
            logger.error(
                f"Handler {self.camera_name}: VimbaError during frame processing: {e_proc}"
            )
        except Exception as e_proc_unexp:
            logger.exception(
                f"Handler {self.camera_name}: Unexpected error processing frame: {e_proc_unexp}"
            )
        finally:
            # --- CRITICAL: Queue frame back to Vimba acquisition engine ---
            try:
                cam.queue_frame(frame)
            except VmbCameraError as e_queue:
                logger.error(
                    f"Handler {self.camera_name}: CRITICAL - Failed to queue frame back: {e_queue}"
                )
                # This is serious, might indicate stream is broken.
                # Consider signaling a major error state.
                self.error.emit(f"CRITICAL Frame queueing error: {e_queue}")
            except Exception as e_queue_unexp:
                logger.exception(
                    f"Handler {self.camera_name}: CRITICAL - Unexpected error queueing frame: {e_queue_unexp}"
                )
                self.error.emit("CRITICAL Unexpected frame queueing error")

    def open(self) -> bool:
        """Opens camera and starts streaming using the callback handler."""
        logger.info(
            f"Attempting to open camera: {self.camera_name} (ID: {self.identifier})"
        )
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
                    feat.set("Continuous") if feat.is_writeable() else logger.warning(
                        "AcqMode not writeable"
                    )
                    logger.debug("Set AcqMode.")
                except VmbCameraError as e:
                    logger.warning(f"Could not set AcqMode: {e}")
                try:
                    feat = self.device.get_feature_by_name("TriggerMode")
                    feat.set("Off") if feat.is_writeable() else logger.warning(
                        "TrigMode not writeable"
                    )
                    logger.debug("Set TrigMode Off.")
                except VmbCameraError as e:
                    logger.warning(f"Could not set TrigMode: {e}")
                try:
                    feat = self.device.get_feature_by_name("ExposureAuto")
                    feat.set(
                        "Off"
                    ) if feat.is_writeable() and "Off" in feat.get_available_entries() else None
                    self.settings.is_auto_exposure_on = False
                    logger.debug("Set ExposureAuto Off.")
                except VmbCameraError as e:
                    logger.warning(f"Could not disable ExposureAuto: {e}")
                try:
                    feat = self.device.get_feature_by_name("GainAuto")
                    feat.set(
                        "Off"
                    ) if feat.is_writeable() and "Off" in feat.get_available_entries() else None
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
        if not self.device:
            return
        try:
            dev_formats = self.device.get_pixel_formats()
            cv_formats = intersect_pixel_formats(dev_formats, OPENCV_PIXEL_FORMATS)
            if not cv_formats:
                logger.error("No OpenCV compatible formats!")
                self.error.emit("No OpenCV format.")
                self.settings.pixel_format = self.device.get_pixel_format()
                self.is_mono = None
                return
            preferred_format = None
            mono_cv = intersect_pixel_formats(cv_formats, MONO_PIXEL_FORMATS)
            color_cv = intersect_pixel_formats(cv_formats, COLOR_PIXEL_FORMATS)
            if mono_cv:
                preferred_format = mono_cv[0]
                self.is_mono = True
                logger.info(f"Selecting Mono format: {preferred_format.name}")
            elif color_cv:
                for fmt in color_cv:
                    if fmt.name in ["RGB8", "BGR8"]:
                        preferred_format = fmt
                        break
                if not preferred_format:
                    preferred_format = color_cv[0]
                self.is_mono = False
                logger.info(f"Selecting Color format: {preferred_format.name}")
            else:
                logger.warning("No suitable Mono/Color format.")
                preferred_format = cv_formats[0]
                self.is_mono = None
            self.device.set_pixel_format(preferred_format)
            self.settings.pixel_format = preferred_format
            logger.info(
                f"Pixel format set to: {preferred_format.name}. Is Mono: {self.is_mono}"
            )
        except VmbCameraError as e:
            logger.error(f"Pixel format error: {e}")
            self.error.emit(f"Pixel format error: {e}")
        try:
            self.settings.pixel_format = self.device.get_pixel_format()
        except Exception as e:
            pass
            self.is_mono = None
            logger.error(f"Unexpected pixel format error: {e}", exc_info=True)
            self.error.emit(f"Unexpected pixel format error: {e}")
            self.is_mono = None

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
            logger.error(
                f"Unexpected error updating settings cache: {e}", exc_info=True
            )

    def _start_stream_internal(self):
        """Internal: Starts Vimba stream using the frame handler callback."""
        with QMutexLocker(self.lock):
            if self._is_closing or not self.device:
                logger.warning("Cannot start stream, closing or no device.")
                self.is_streaming = False
                return
            if self.is_streaming:
                logger.warning("Streaming already started.")
                return  # Already streaming

            logger.debug(f"_start_stream_internal for {self.camera_name}")
            try:
                # --- Use Callback Handler ---
                self.device.start_streaming(self._frame_handler, buffer_count=5)
                self.is_streaming = True
                logger.info(
                    f"Vimba streaming started with callback for {self.camera_name}."
                )

            except VmbCameraError as e:
                logger.error(f"Failed to start Vimba streaming: {e}")
                self.error.emit(f"Streaming start error: {e}")
                self.is_streaming = False
                try:
                    self.device.stop_streaming()  # Attempt cleanup
                except:
                    pass
            except Exception as e:
                logger.error(f"Unexpected error starting stream: {e}", exc_info=True)
                self.error.emit(f"Unexpected streaming start error: {e}")
                self.is_streaming = False
                try:
                    self.device.stop_streaming()
                except:
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
                    logger.error(
                        f"Unexpected error stopping Vimba streaming: {e}", exc_info=True
                    )
            elif self.device:
                logger.debug(
                    "Stop stream called, but streaming was not marked as active."
                )
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
                logger.error(
                    f"Unexpected error during device close: {e}", exc_info=True
                )
                self.device = None
                self.is_mono = None
                self.disconnected.emit()
                return False

    def close(self):
        """Stops streaming and closes the camera device connection cleanly."""
        if self._is_closing:
            logger.debug("Close already in progress.")
            return
        self._is_closing = True
        logger.info(f"Initiating close sequence for camera: {self.camera_name}")
        try:
            # Ensure operations happen assuming Vimba system is active (managed by app.py)
            self._stop_stream_internal()
            self.close_device_internal()
        except Exception as e:
            logger.error(f"Error during camera close sequence: {e}", exc_info=True)
            with QMutexLocker(self.lock):
                self.device = None
                self.is_mono = None  # Ensure cleared
            self.is_streaming = False
        self.frame_buffer.clear()
        logger.info(f"Close sequence finished for camera: {self.camera_name}")
        self._is_closing = False

    @Slot()
    def _attempt_recovery(self):
        """Attempts to close and reopen the camera."""
        logger.warning(f"Executing recovery attempt for {self.camera_name}...")
        self.close()
        QThread.msleep(500)  # Allow time for resources to release
        if not self._is_closing:
            self.open()
            if self.is_streaming:
                logger.info("Recovery successful.")
                self.error.emit("Camera connection restored.")
            else:
                logger.error("Recovery failed.")
                self.error.emit("Failed to reconnect camera.")

    # --- Feature Access Methods ---
    def get_latest_frame(self) -> Optional[np.ndarray]:
        return self.frame_buffer.get_latest_frame()

    def get_setting(self, setting_name: str) -> Any:
        return getattr(self.settings, setting_name, None)

    def get_feature_min_max(
        self, feature_name: str, default_value: float, is_max: bool = False
    ) -> float:
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

    def get_exposure(self) -> Optional[float]:
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

    def set_exposure(self, value_us: float) -> bool:
        if not self.device:
            logger.warning("Cannot set exposure: Camera not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return False
                try:
                    feat_auto = self.device.get_feature_by_name("ExposureAuto")
                    feat_auto.set("Off") if feat_auto.is_writeable() else None
                    self.settings.is_auto_exposure_on = False
                except VmbCameraError as e_auto:
                    logger.warning(f"Could not disable ExposureAuto: {e_auto}")
                feat_exp = self.device.get_feature_by_name("ExposureTimeAbs")
                min_val, max_val = feat_exp.get_range()
                set_val = max(min_val, min(max_val, value_us))
                if abs(set_val - value_us) > 1:
                    logger.warning(f"Exposure {value_us} us clamped to {set_val} us")
                feat_exp.set(set_val)
                self.settings.exposure_us = set_val
                return True
        except VmbCameraError as e:
            error_msg = f"Error setting exposure: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting exposure: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False

    def get_gamma(self) -> Optional[float]:  # ...
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

    def set_gamma(self, value: float) -> bool:  # ...
        if not self.device:
            logger.warning("Cannot set gamma: not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return False
                feat = self.device.get_feature_by_name("Gamma")
                if not feat.is_writeable():
                    logger.warning("Gamma not writeable.")
                    return False
                min_val, max_val = feat.get_range()
                set_val = max(min_val, min(max_val, value))
                if abs(set_val - value) > 0.01:
                    logger.warning(f"Gamma {value:.2f} clamped to {set_val:.2f}")
                feat.set(set_val)
                self.settings.gamma = set_val
                return True
        except VmbCameraError as e:
            error_msg = f"Error setting gamma: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting gamma: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False

    def get_gain(self) -> Optional[float]:  # ...
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

    def set_gain(self, value_db: float) -> bool:  # ...
        if not self.device:
            logger.warning("Cannot set gain: not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:
                    return False
                try:
                    feat_auto = self.device.get_feature_by_name("GainAuto")
                    feat_auto.set("Off") if feat_auto.is_writeable() else None
                    self.settings.is_auto_gain_on = False
                except VmbCameraError as e_auto:
                    logger.warning(f"Could not disable GainAuto: {e_auto}")
                feat_gain = self.device.get_feature_by_name("Gain")
                min_val, max_val = feat_gain.get_range()
                set_val = max(min_val, min(max_val, value_db))
                if abs(set_val - value_db) > 0.1:
                    logger.warning(
                        f"Gain {value_db:.1f} dB clamped to {set_val:.1f} dB"
                    )
                feat_gain.set(set_val)
                self.settings.gain_db = set_val
                return True
        except VmbCameraError as e:
            error_msg = f"Error setting gain: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting gain: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False

    def set_auto_exposure_once(self) -> bool:
        if not self.device:
            logger.warning("Cannot set auto exposure: Camera not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:  # Re-check after acquiring lock
                    return False

                feat_auto = self.device.get_feature_by_name("ExposureAuto")

                current_mode_obj = None
                current_mode_str = "N/A"
                is_currently_writable = feat_auto.is_writeable()
                available_entries_objs = []
                available_entry_names = []  # List of string names for the modes

                try:
                    if feat_auto.is_readable():
                        current_mode_obj = feat_auto.get()  # Gets the EnumEntry object
                        current_mode_str = str(
                            current_mode_obj
                        )  # Get string representation for logging

                    available_entries_objs = list(
                        feat_auto.get_available_entries()
                    )  # Make it a list for easier iteration
                    available_entry_names = [
                        str(entry) for entry in available_entries_objs
                    ]

                except VmbCameraError as e_log:
                    logger.warning(f"Could not fully log ExposureAuto state: {e_log}")

                logger.debug(
                    f"Attempting ExposureAuto 'Once'. Current mode: {current_mode_str}, "
                    f"Writable: {is_currently_writable}, Available Names: {available_entry_names}"
                )

                if not is_currently_writable:
                    logger.warning("ExposureAuto feature is not writable.")
                    return False

                if "Once" not in available_entry_names:
                    logger.error(
                        f"ExposureAuto mode 'Once' is not in available entry names: {available_entry_names}."
                    )
                    # Log the actual names if "Once" is not found for detailed debugging
                    if not available_entry_names:
                        logger.warning(
                            "No available entries found for ExposureAuto at all."
                        )
                    else:
                        logger.info("Detailed available ExposureAuto entries:")
                        for entry_obj in available_entries_objs:
                            logger.info(
                                f"  - Name: {str(entry_obj)}, Int Value: {entry_obj.value}"
                            )  # Log both
                    return False

                # If we reach here, "Once" should be available.
                # We need to find the EnumEntry object whose string representation is "Once"
                entry_to_set = None
                for entry_obj in available_entries_objs:
                    if str(entry_obj) == "Once":  # Compare string representation
                        entry_to_set = entry_obj
                        break

                if entry_to_set is None:
                    logger.error(
                        "Critical: 'Once' was in available_entry_names but could not find corresponding EnumEntry object."
                    )
                    return False

                feat_auto.set(entry_to_set)  # Set using the EnumEntry object
                self.settings.is_auto_exposure_on = True
                logger.info("ExposureAuto successfully set to 'Once'.")

                return True

        except VmbCameraError as e:
            error_msg = f"VimbaError setting ExposureAuto 'Once': {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting auto exposure: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False

    def set_auto_gain_once(self) -> bool:
        if not self.device:
            logger.warning("Cannot set auto gain: Camera not connected.")
            return False
        try:
            with QMutexLocker(self.lock):
                if not self.device:  # Re-check
                    return False

                feat_auto_gain = self.device.get_feature_by_name("GainAuto")

                current_mode_obj = None
                current_mode_str = "N/A"
                is_currently_writable = feat_auto_gain.is_writeable()
                available_entries_objs = []
                available_entry_names = []

                try:
                    if feat_auto_gain.is_readable():
                        current_mode_obj = feat_auto_gain.get()
                        current_mode_str = str(current_mode_obj)

                    available_entries_objs = list(
                        feat_auto_gain.get_available_entries()
                    )
                    available_entry_names = [
                        str(entry) for entry in available_entries_objs
                    ]

                except VmbCameraError as e_log:
                    logger.warning(f"Could not fully log GainAuto state: {e_log}")

                logger.debug(
                    f"Attempting GainAuto 'Once'. Current mode: {current_mode_str}, "
                    f"Writable: {is_currently_writable}, Available Names: {available_entry_names}"
                )

                if not is_currently_writable:
                    logger.warning("GainAuto feature is not writable.")
                    return False

                if "Once" not in available_entry_names:
                    logger.error(
                        f"GainAuto mode 'Once' is not in available entry names: {available_entry_names}."
                    )
                    if not available_entry_names:
                        logger.warning(
                            "No available entries found for GainAuto at all."
                        )
                    else:
                        logger.info("Detailed available GainAuto entries:")
                        for entry_obj in available_entries_objs:
                            logger.info(
                                f"  - Name: {str(entry_obj)}, Int Value: {entry_obj.value}"
                            )
                    return False

                entry_to_set = None
                for entry_obj in available_entries_objs:
                    if str(entry_obj) == "Once":
                        entry_to_set = entry_obj
                        break

                if entry_to_set is None:
                    logger.error(
                        "Critical: 'Once' was in available_entry_names but could not find corresponding EnumEntry object for GainAuto."
                    )
                    return False

                feat_auto_gain.set(entry_to_set)  # Set using the EnumEntry object
                self.settings.is_auto_gain_on = True
                logger.info("GainAuto successfully set to 'Once'.")

                return True

        except VmbCameraError as e:
            error_msg = f"VimbaError setting GainAuto 'Once': {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Unexpected error setting auto gain: {e}"
            logger.error(error_msg, exc_info=True)
            self.error.emit(error_msg)
            return False
