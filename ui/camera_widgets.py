"""Defines all UI widgets related to camera display and control.

This module contains the components for a single camera's user interface,
including the main video display panel, parameter control sliders, and the
background workers needed for performance.

-   `ImageConversionWorker`: A QRunnable for offloading the conversion of a
    numpy frame to a QImage from the main GUI thread.
-   `ParameterControl`: A reusable compound widget (Label, Slider, LineEdit)
    for controlling a single hardware parameter with linear or log scaling.
-   `AspectLockedLabel`: A QLabel subclass that maintains the aspect ratio of
    its pixmap, essential for distortion-free video display.
-   `AutoOpWorker`: A QRunnable for handling one-shot auto-exposure/gain
    operations in the background.
-   `CameraPanel`: The main widget that aggregates all other components to
    display a camera feed and its associated controls.
"""

import logging
import math
import threading
import time
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PySide6 import QtCore, QtGui
from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    QObject,
    QPoint,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from app_settings import AppSettings
from config_model import CameraConfig
from hardware.camera import VimbaCam
from hardware.camera_capabilities import ROI
from ui.constants import (
    CAMERA_RESIZE_EVENT_THROTTLE_MS,
    CAMERA_RESIZE_UPDATE_DELAY_MS,
    CAMERA_WATCHDOG_INTERVAL_MS,
    MSG_CAMERA_CONNECTING,
    MSG_CAMERA_WAITING,
    OP_AUTO_EXPOSURE,
    OP_AUTO_GAIN,
)
from ui.typography import make_font

logger = logging.getLogger("LabApp.camera_widgets")

VIEW_MODES = (
    (964, "Full", "1.34:1"),
    (720, "720 crop", "1.79:1"),
    (480, "480 crop", "2.69:1"),
    (240, "240 crop", "5.38:1"),
    (120, "120 crop", "10.77:1"),
)


class ViewModeSignals(QObject):
    finished = Signal(object, str)


class CenteredComboBox(QComboBox):
    """A native combo box whose closed selection text is centered."""

    def paintEvent(self, _event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        text = option.currentText
        option.currentText = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        painter.drawItemText(
            text_rect,
            Qt.AlignmentFlag.AlignCenter,
            option.palette,
            self.isEnabled(),
            text,
        )


class ViewModeWorker(QRunnable):
    def __init__(self, camera: VimbaCam, height: int):
        super().__init__()
        self.camera = camera
        self.height = height
        self.signals = ViewModeSignals()

    def run(self):
        try:
            result = self.camera.apply_view_mode(1292, self.height, maximize_rate=True)
            self.signals.finished.emit(result, "")
        except Exception as exc:  # noqa: BLE001 - surface camera transaction and rollback failures.
            self.signals.finished.emit(None, str(exc))


class ImageConversionSignals(QObject):
    """Defines signals for the ImageConversionWorker.

    A separate QObject is required for signals because QRunnable does not
    inherit from QObject.
    """

    image_ready = Signal(QImage)
    conversion_error = Signal(str)


class ImageConversionWorker(QObject):
    """
    A persistent QObject worker that converts numpy arrays to QImages
    in a dedicated background thread.
    """

    # Define signals directly in the class
    image_ready = Signal(QImage)
    conversion_error = Signal(str)
    _frame_available = Signal()

    def __init__(self, is_mono: bool, camera_name: str, parent=None):
        super().__init__(parent)
        self.is_mono = is_mono
        self.camera_name = camera_name
        self._is_running = True
        self._frame_lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._frame_notification_pending = False
        self._active = False
        self.submitted_frames = 0
        self.converted_frames = 0
        self.coalesced_frames = 0
        self.max_pending_frames = 0
        self._frame_available.connect(self._process_latest_frame, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def stop(self):
        """Allows the worker to be stopped cleanly."""
        with self._frame_lock:
            self._is_running = False
            self._latest_frame = None

    def set_active(self, active: bool):
        with self._frame_lock:
            self._active = active and self._is_running
            if not self._active:
                self._latest_frame = None

    @Slot(np.ndarray)
    def submit_frame(self, frame: np.ndarray):
        """Thread-safe latest-frame mailbox; the camera-owned array stays alive."""
        if frame is None or frame.size == 0:
            return
        notify = False
        with self._frame_lock:
            if not self._is_running or not self._active:
                return
            self.submitted_frames += 1
            if self._latest_frame is not None:
                self.coalesced_frames += 1
            self._latest_frame = frame
            self.max_pending_frames = max(self.max_pending_frames, 1)
            if not self._frame_notification_pending:
                self._frame_notification_pending = True
                notify = True
        if notify:
            self._frame_available.emit()

    @Slot()
    def _process_latest_frame(self):
        with self._frame_lock:
            frame = self._latest_frame
            self._latest_frame = None
            self._frame_notification_pending = False
            running = self._is_running
        if running and frame is not None:
            self.process_frame(frame)

    # This is the new slot that will receive frames
    def process_frame(self, frame: np.ndarray):
        """
        The workhorse method that runs in the background thread.
        Converts the numpy frame to the appropriate QImage format.
        """
        with self._frame_lock:
            if not self._is_running or frame is None or frame.size == 0:
                return

        try:
            h, w = frame.shape[:2]
            q_img: QImage | None = None

            # --- The conversion logic remains exactly the same ---
            if self.is_mono:
                if frame.ndim == 3 and frame.shape[2] == 1:
                    frame = frame.reshape(h, w)
                if frame.ndim == 2:
                    if not frame.flags["C_CONTIGUOUS"]:
                        frame = np.ascontiguousarray(frame)
                    q_img = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_Grayscale8)
                else:
                    raise TypeError(f"Mono camera provided unexpected frame shape: {frame.shape}")
            else:  # Color
                if frame.ndim == 3 and frame.shape[2] == 3:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    q_img = QImage(frame_rgb.data, w, h, frame_rgb.strides[0], QImage.Format.Format_RGB888)
                else:
                    raise TypeError(f"Color camera provided unexpected frame shape: {frame.shape}")

            if q_img and self._is_running:
                # The QImage must be copied because the underlying numpy buffer
                # will go out of scope and be garbage-collected.
                self.image_ready.emit(q_img.copy())
                self.converted_frames += 1
            elif not q_img:
                self.conversion_error.emit("Converted QImage was null.")
        except Exception as e:
            error_msg = f"Panel {self.camera_name}: Unhandled error converting frame: {e}"
            logger.exception(error_msg)
            self.conversion_error.emit(str(e))


class ParameterControl(QWidget):
    """A compound widget for controlling a single camera parameter.

    This widget encapsulates a label, a slider, and a line edit, keeping them
    synchronized. It supports both linear and logarithmic scales for the slider.

    Attributes:
        valueChanged (Signal): Emits the new floating-point value whenever the
            user changes the slider or finishes editing the line edit.
    """

    valueChanged = Signal(float)

    def __init__(
        self,
        name: str,
        min_val: float,
        max_val: float,
        initial_val: float,
        scale: Literal["linear", "log"] = "linear",
        decimals: int = 0,
        parent: QWidget | None = None,
    ):
        """Initializes the ParameterControl widget.

        Args:
            name: The display name of the parameter (e.g., "Exposure (µs)").
            min_val: The minimum allowed value for the parameter.
            max_val: The maximum allowed value for the parameter.
            initial_val: The starting value for the control.
            scale: The mapping scale for the slider ('linear' or 'log').
            decimals: The number of decimal places to display in the line edit.
            parent: The parent widget.
        """
        super().__init__(parent)
        self.param_name = name
        # Logarithmic mapping requires a positive minimum. Linear ranges may
        # legitimately start at zero or below and must retain their endpoints.
        self.min_val = max(1e-9, min_val) if scale == "log" else min_val
        self.max_val = max_val
        self.scale = scale
        self.decimals = decimals

        self._init_ui()
        self._connect_signals()
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._restore_feedback_style)
        self._feedback_original_style = ""
        self.setValue(initial_val, emit_signal=False)

    def _init_ui(self):
        """Creates and arranges the label, slider, and line edit widgets."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.label = QLabel(f"{self.param_name}:")
        self.label.setFixedWidth(130)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)  # Always use a fixed high-resolution slider range
        self.edit = QLineEdit()
        self.edit.setValidator(QtGui.QDoubleValidator(self.min_val, self.max_val, self.decimals))
        self.edit.setFixedWidth(70)
        layout.addWidget(self.label)
        layout.addWidget(self.slider)
        layout.addWidget(self.edit)
        layout.setStretch(1, 1)  # Make slider expand

    def _connect_signals(self):
        """Connects internal signals for synchronization."""
        self.slider.valueChanged.connect(self._handle_slider_change)
        self.edit.editingFinished.connect(self._handle_edit_change)

    def _value_to_slider(self, value: float) -> int:
        """Converts a parameter value to a slider position (0-1000)."""
        if value <= self.min_val:
            return 0
        if value >= self.max_val:
            return 1000

        if self.scale == "log":
            try:
                log_min = math.log10(self.min_val)
                log_max = math.log10(self.max_val)
                log_val = math.log10(value)
                if log_max == log_min:
                    return 0
                return int(1000 * (log_val - log_min) / (log_max - log_min))
            except (ValueError, ZeroDivisionError):
                return self._value_to_slider_linear(value)  # Fallback to linear
        else:  # linear
            return self._value_to_slider_linear(value)

    def _value_to_slider_linear(self, value: float) -> int:
        """Linear conversion from value to slider position."""
        if self.max_val == self.min_val:
            return 0
        return int(1000 * (value - self.min_val) / (self.max_val - self.min_val))

    def _slider_to_value(self, slider_pos: int) -> float:
        """Converts a slider position (0-1000) to a parameter value."""
        if slider_pos <= 0:
            return self.min_val
        if slider_pos >= 1000:
            return self.max_val

        if self.scale == "log":
            try:
                log_min = math.log10(self.min_val)
                log_max = math.log10(self.max_val)
                if log_max == log_min:
                    return self.min_val
                return 10 ** ((slider_pos / 1000.0) * (log_max - log_min) + log_min)
            except (ValueError, ZeroDivisionError):
                return self._slider_to_value_linear(slider_pos)  # Fallback to linear
        else:  # linear
            return self._slider_to_value_linear(slider_pos)

    def _slider_to_value_linear(self, slider_pos: int) -> float:
        """Linear conversion from slider position to value."""
        if self.max_val == self.min_val:
            return self.min_val
        return self.min_val + (slider_pos / 1000.0) * (self.max_val - self.min_val)

    def _handle_slider_change(self, slider_pos: int):
        """Updates the line edit when the slider moves and emits the new value."""
        value = self._slider_to_value(slider_pos)
        self.edit.blockSignals(True)
        self.edit.setText(f"{value:.{self.decimals}f}")
        self.edit.blockSignals(False)
        self.valueChanged.emit(value)

    def _handle_edit_change(self):
        """Updates the slider when the line edit is finished and emits the new value."""
        try:
            value = float(self.edit.text())
            value = max(self.min_val, min(self.max_val, value))  # Clamp value
            self.edit.setText(f"{value:.{self.decimals}f}")
            self.slider.blockSignals(True)
            self.slider.setValue(self._value_to_slider(value))
            self.slider.blockSignals(False)
            self.valueChanged.emit(value)
        except ValueError:
            # Revert to current slider value if input is invalid
            current_value = self._slider_to_value(self.slider.value())
            self.edit.setText(f"{current_value:.{self.decimals}f}")

    def setValue(self, value: float, emit_signal: bool = False):
        """Programmatically sets the value of the control.

        Args:
            value: The new value to set. It will be clamped to the min/max range.
            emit_signal: If True, the `valueChanged` signal will be emitted.
        """
        value = max(self.min_val, min(self.max_val, value))
        with QtCore.QSignalBlocker(self.slider), QtCore.QSignalBlocker(self.edit):
            self.slider.setValue(self._value_to_slider(value))
            self.edit.setText(f"{value:.{self.decimals}f}")
        if emit_signal:
            self.valueChanged.emit(value)

    def value(self) -> float:
        """Returns the current value of the control."""
        return self._slider_to_value(self.slider.value())

    def visual_feedback(self, success: bool = True, duration_ms: int = 400):
        """Provides brief visual feedback on the line edit widget."""
        self._feedback_original_style = self.edit.styleSheet()
        color = "#e0ffe0" if success else "#ffe0e0"  # Light green/red
        self.edit.setStyleSheet(f"background-color: {color};")
        self._feedback_timer.start(duration_ms)

    def _restore_feedback_style(self):
        self.edit.setStyleSheet(self._feedback_original_style)


class AspectLockedLabel(QLabel):
    """A QLabel that maintains the aspect ratio of its displayed pixmap.

    This is crucial for displaying video frames without distortion when the
    widget is resized. It overrides Qt's layout methods to enforce the aspect
    ratio of the source image.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._aspect_ratio: float | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setScaledContents(False)  # We will do our own scaling

    def setAspectRatio(self, width: int, height: int):
        """Sets the aspect ratio to maintain.

        Args:
            width: The width of the source content.
            height: The height of the source content.
        """
        if height > 0:
            new_ar = width / height
            if self._aspect_ratio is None or abs(new_ar - self._aspect_ratio) > 1e-6:
                self._aspect_ratio = new_ar
                self.updateGeometry()
        else:
            self._aspect_ratio = None

    def hasHeightForWidth(self) -> bool:
        """Required override for aspect ratio-dependent widgets."""
        return self._aspect_ratio is not None

    def heightForWidth(self, width: int) -> int:
        """Calculates the required height to maintain the aspect ratio for a given width."""
        if self._aspect_ratio is not None and self._aspect_ratio > 1e-6:
            calculated_height = int(width / self._aspect_ratio)
            return calculated_height
        return super().heightForWidth(width)

    def sizeHint(self) -> QSize:
        w = self.width()
        if self.hasHeightForWidth():
            min_sensible_width = 100
            current_hint_width = max(w, min_sensible_width)
            sh = QSize(current_hint_width, self.heightForWidth(current_hint_width))
            return sh
        else:
            sh = super().sizeHint()
            return sh


class AutoOpWorker(QRunnable):
    def __init__(self, camera: VimbaCam, op_type: str, panel_callback: "CameraPanel"):
        super().__init__()
        if op_type not in [OP_AUTO_EXPOSURE, OP_AUTO_GAIN]:
            raise ValueError(f"Unknown auto operation type: {op_type}")
        self.camera = camera
        self.op_type = op_type
        self.panel_callback = panel_callback

    def run(self):
        try:
            result_value: float | None = None
            success = False
            camera_method_success = False

            match self.op_type:
                case "auto_exposure":
                    logger.debug(f"Worker: Calling camera.set_auto_exposure_once() for {self.camera.camera_name}")
                    camera_method_success = self.camera.set_auto_exposure_once()
                    if camera_method_success:
                        logger.info(
                            f"Worker: {self.camera.camera_name} - ExposureAuto 'Once' mode set. Waiting for adjustment..."
                        )
                        time.sleep(1)
                        result_value = self.camera.get_exposure()
                        logger.info(
                            f"Worker: {self.camera.camera_name} - Auto Exposure adjustment finished. New value: {result_value}"
                        )
                        success = result_value is not None
                case "auto_gain":
                    logger.debug(f"Worker: Calling camera.set_auto_gain_once() for {self.camera.camera_name}")
                    camera_method_success = self.camera.set_auto_gain_once()
                    if camera_method_success:
                        logger.info(
                            f"Worker: {self.camera.camera_name} - GainAuto 'Once' mode set. Waiting for adjustment..."
                        )
                        time.sleep(1)
                        result_value = self.camera.get_gain()
                        logger.info(
                            f"Worker: {self.camera.camera_name} - Auto Gain adjustment finished. New value: {result_value}"
                        )
                        success = result_value is not None
                case _:
                    # This case handles unknown operation types gracefully.
                    raise ValueError(f"Unknown auto operation type: {self.op_type}")

            if success and result_value is not None:
                QMetaObject.invokeMethod(
                    self.panel_callback,
                    "handle_auto_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, self.op_type),
                    Q_ARG(float, result_value),
                )
            elif not camera_method_success:
                raise RuntimeError(f"Camera method for {self.op_type} reported failure to set 'Once' mode.")
            elif result_value is None and camera_method_success:
                raise RuntimeError(
                    f"Camera method for {self.op_type} set 'Once' mode, but failed to retrieve new value."
                )
        except Exception as e:
            error_msg = f"Error during {self.op_type} for {self.camera.camera_name}: {e}"
            logger.exception(error_msg)
            QMetaObject.invokeMethod(
                self.panel_callback,
                "handle_auto_error",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, self.op_type),
                Q_ARG(str, str(e)),
            )
        finally:
            QMetaObject.invokeMethod(
                self.panel_callback,
                "handle_auto_finished",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, self.op_type),
            )


# =============================================================================
# Camera Panel (Refactored to use ParameterControl)
# =============================================================================
class CameraPanel(QFrame):
    """A widget that displays a live camera feed and its associated controls.

    This panel is the main UI component for a single camera. It includes:
    -   A video display area (`AspectLockedLabel`).
    -   Collapsible controls for exposure, gain, and gamma.
    -   Buttons for one-shot auto-operations.
    -   An FPS (frames per second) overlay.
    -   A watchdog timer to attempt recovery if the camera stream stops.

    The panel is designed to be created in a placeholder state and later have a
    live `VimbaCam` object assigned to it via `set_camera()`.
    """

    maximize_requested = Signal()
    recovery_requested = Signal(str)
    auto_operation_finished = Signal()
    _display_image_ready = Signal()

    def __init__(
        self,
        camera: VimbaCam | None,
        title: str,
        config: CameraConfig,
        parent: QWidget | None = None,
        settings: AppSettings | None = None,
    ):
        """Initializes the CameraPanel.

        Args:
            camera: A live `VimbaCam` instance, or `None` if this is a
                placeholder panel awaiting asynchronous initialization.
            title: The display title for the panel, used for logging and
                display before the camera is fully initialized.
            config: The `CameraConfig` object for this camera.
            parent: The parent widget.
        """
        super().__init__(parent)
        self.camera = camera
        self.config = config
        self._panel_title = title  # Use this for logging before camera is set
        self._latest_pixmap: QPixmap | None = None
        self._camera_error_active = False
        self._automatic_recovery_used = False
        self._recovery_active = False
        self._recovery_failed = False
        self._auto_op_active = False
        self._camera_mode_change_active = False
        self._current_roi: ROI | None = None
        self._view_mode_baseline: dict | None = None
        self._view_mode_changed = False
        self._panel_closing = False
        self._camera_shutdown_prepared = False
        self._presentation_enabled = False
        self._display_lock = threading.Lock()
        self._latest_qimage: QImage | None = None
        self._display_notification_pending = False
        self.coalesced_images = 0
        self.max_pending_images = 0
        self._controls_available_state: bool | None = None
        self._display_image_ready.connect(self._display_latest_converted_image, Qt.ConnectionType.QueuedConnection)
        self._display_size_cache: QtCore.QSize | None = None

        self._thread_pool = QThreadPool.globalInstance()

        self.conversion_thread: QThread | None = None
        self.conversion_worker: ImageConversionWorker | None = None

        self._last_resize_time: float = 0.0
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_display_update)
        self.controls_visible: bool = False

        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(CAMERA_WATCHDOG_INTERVAL_MS)
        self.watchdog_timer.setSingleShot(True)
        self.watchdog_timer.timeout.connect(self._on_watchdog_timeout)

        self._current_fps: float = 0.0
        self._show_fps: bool = True
        self._fps_font = make_font("mono", 10, QFont.Weight.Bold)
        self._fps_color = QColor("lime")
        self.setObjectName(f"cameraPanel_{camera.identifier if camera else title.replace(' ', '_')}")
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(4)

        self.title_label = None
        if config.backend == "simulation":
            self.title_label = QLabel(self._panel_title, self)
            self.title_label.setObjectName("simulatedCameraTitle")
            self.main_layout.addWidget(self.title_label)

        self.settings = settings
        self.camera_identifier = config.identifier
        self.last_save_dir = (
            settings.camera_screenshot_directory(self.camera_identifier) if settings is not None else Path.cwd()
        )

        self._init_ui()
        self.main_layout.addWidget(self.controls_container)

        self.video_container = QWidget(self)
        video_layout = QGridLayout(self.video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        self.video_label = AspectLockedLabel(self.video_container)
        if self.camera:
            self.video_label.setText(MSG_CAMERA_WAITING)
        else:
            self.video_label.setText(MSG_CAMERA_CONNECTING.format(self._panel_title))

        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: transparent; color: grey;")
        video_layout.addWidget(self.video_label, 0, 0)
        self.overlay_actions = QWidget(self.video_container)
        self.overlay_actions.setObjectName("cameraOverlayActions")
        overlay_layout = QHBoxLayout(self.overlay_actions)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(2)
        self.screenshot_btn = QToolButton(self.overlay_actions)
        self.screenshot_btn.setObjectName("cameraScreenshotButton")
        self.screenshot_btn.setIcon(QIcon(":/icons/camera-white.svg"))
        self.screenshot_btn.setToolTip("Save camera screenshot")
        self.screenshot_btn.setAccessibleName("Save camera screenshot")
        self.screenshot_btn.clicked.connect(self.take_screenshot)
        self.settings_button = QToolButton(self.overlay_actions)
        self.settings_button.setObjectName("cameraSettingsGear")
        self.settings_button.setIcon(QIcon(":/icons/settings.svg"))
        self.settings_button.setToolTip("Show or hide camera settings")
        self.settings_button.setAccessibleName("Toggle camera settings")
        self.settings_button.setCheckable(True)
        self.settings_button.setChecked(self.controls_visible)
        self.settings_button.toggled.connect(self.set_controls_visibility)
        overlay_layout.addWidget(self.screenshot_btn)
        overlay_layout.addWidget(self.settings_button)
        overlay_button_style = (
            "QToolButton { background: rgba(25, 25, 25, 190); color: white; border: 0; border-radius: 4px; }"
            "QToolButton:hover { background: rgba(55, 55, 55, 230); }"
            "QToolButton:pressed, QToolButton:checked { background: rgba(75, 75, 75, 235); }"
        )
        for button in (self.screenshot_btn, self.settings_button):
            button.setFixedSize(28, 28)
            button.setIconSize(QSize(16, 16))
            button.setStyleSheet(overlay_button_style)
        video_layout.addWidget(self.overlay_actions, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.main_layout.addWidget(self.video_container, stretch=1)

        self.controls_container.setVisible(self.controls_visible)
        self.clear_status_indicators()
        self._refresh_view_mode_from_camera()

    def set_camera(self, camera: VimbaCam):
        """Assigns the live camera object to the panel after initialization.

        This method connects the panel to the live camera's signals and
        populates the control widgets with the actual parameter ranges and
        values read from the camera hardware.

        Args:
            camera: The successfully initialized `VimbaCam` instance.
        """
        if self.camera is not None:
            logger.warning(f"CameraPanel for {self._panel_title} is already assigned a camera.")
            return

        self.camera = camera
        self.setObjectName(f"cameraPanel_{camera.identifier}")

        # --- START THE PERSISTENT WORKER ---
        self._start_conversion_worker()
        self._set_presentation_enabled(self.isVisible())

        # Update controls AFTER starting worker, just in case
        self._update_controls_from_camera()
        self._refresh_view_mode_from_camera()
        self._update_control_availability()

    def _update_control_availability(self):
        available = (
            not self._recovery_active
            and not self._recovery_failed
            and not self._auto_op_active
            and not self._camera_mode_change_active
        )
        if self._controls_available_state == available:
            return
        self._controls_available_state = available
        self.controls_container.setEnabled(available)
        self._update_view_mode_availability()

    def _arm_watchdog(self):
        if (
            self.camera
            and self.isVisible()
            and not self._recovery_active
            and not self._recovery_failed
            and not self._auto_op_active
            and not self._camera_mode_change_active
        ):
            self.watchdog_timer.start()

    @Slot()
    def _on_watchdog_timeout(self):
        if not self.camera or not self.isVisible() or self._recovery_active or self._auto_op_active:
            return
        if self._automatic_recovery_used:
            self._recovery_failed = True
            self._camera_error_active = True
            self.video_label.setText("Camera unavailable: no frames after recovery")
            self.video_label.setStyleSheet("background-color: #333; color: #ffc107;")
            self._update_control_availability()
            self.watchdog_timer.stop()
            return
        self._automatic_recovery_used = True
        self.recovery_started()
        self.recovery_requested.emit(self.camera.identifier)

    def recovery_started(self):
        self._recovery_active = True
        self._recovery_failed = False
        self.watchdog_timer.stop()
        self._camera_error_active = True
        self._latest_pixmap = None
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("Recovering camera…")
        self.video_label.setStyleSheet("background-color: #333; color: #ffc107;")
        self._update_control_availability()

    def recovery_succeeded(self):
        self._recovery_active = False
        self._recovery_failed = False
        self._camera_error_active = True
        self.video_label.setText("Camera reopened; waiting for frames…")
        self._refresh_view_mode_from_camera()
        self._update_control_availability()
        self._arm_watchdog()

    def recovery_failed(self, message: str):
        self._recovery_active = False
        self._recovery_failed = True
        self._camera_error_active = True
        self.watchdog_timer.stop()
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText(message or "Camera recovery failed")
        self.video_label.setStyleSheet("background-color: #333; color: #ffc107;")
        self._update_control_availability()

    @property
    def auto_operation_active(self) -> bool:
        return self._auto_op_active

    def _start_conversion_worker(self):
        """Creates and starts the dedicated thread and worker for image conversion."""
        if not self.camera or not self.camera.is_mono is not None:
            logger.error(f"Cannot start conversion worker for {self._panel_title}, camera not ready.")
            return

        self.conversion_thread = QThread(self)
        # Pass necessary info to the worker's constructor
        self.conversion_worker = ImageConversionWorker(is_mono=self.camera.is_mono, camera_name=self._panel_title)
        self.conversion_worker.moveToThread(self.conversion_thread)

        # Connect signals:
        # 1. Worker's output signals to the panel's slots
        self.conversion_worker.image_ready.connect(self._accept_converted_image, Qt.ConnectionType.DirectConnection)
        self.conversion_worker.conversion_error.connect(self._handle_conversion_error)

        # 2. Thread management signals
        self.conversion_thread.started.connect(
            lambda: logger.info(f"Conversion thread started for {self._panel_title}.")
        )
        self.conversion_thread.finished.connect(self.conversion_worker.deleteLater)

        # Start the thread
        self.conversion_thread.start()
        logger.info(f"Persistent conversion worker created for {self._panel_title}")

        # DirectConnection only enters the worker's mutex-protected mailbox. It
        # never touches widgets or performs conversion in the acquisition thread.
        self.camera.new_frame.connect(self.conversion_worker.submit_frame, Qt.ConnectionType.DirectConnection)

    def _update_controls_from_camera(self):
        """Refreshes control widgets with values from the live camera."""
        if not self.camera:
            return

        # --- REVISED LOGIC FOR GETTING RANGES ---
        exposure_range = self.camera.get_feature_range("ExposureTimeAbs")
        if exposure_range:
            exposure_min_us, exposure_max_us = exposure_range
        else:
            # Fallback values if the range can't be fetched
            exposure_min_us, exposure_max_us = 12.0, 8.45e7

        initial_exposure = self.camera.exposure_us or 10000.0

        self.exposure_control.min_val = exposure_min_us
        self.exposure_control.max_val = exposure_max_us
        self.exposure_control.setValue(initial_exposure)

        initial_gamma = self.camera.gamma or 1.0
        self.gamma_control.setValue(initial_gamma)
        # We can also update the gamma range if it's dynamic
        gamma_range = self.camera.get_feature_range("Gamma")
        if gamma_range:
            self.gamma_control.min_val, self.gamma_control.max_val = gamma_range

        gain_range = self.camera.get_feature_range("gain")
        self.gain_control.setEnabled(gain_range is not None and self._gain_is_writable())
        if gain_range:
            self.gain_control.min_val, self.gain_control.max_val = gain_range
            self.gain_control.setValue(self.camera.get_gain())

    def _refresh_view_mode_from_camera(self):
        if not self.camera or not callable(getattr(self.camera, "get_roi", None)):
            self._current_roi = None
            self._update_view_mode_availability()
            return
        roi = self.camera.get_roi()
        if roi is None:
            self._current_roi = None
            self.view_mode_combo.setCurrentIndex(self.view_mode_combo.count() - 1)
            self._update_view_mode_availability()
            return
        caps_report = (
            self.camera.get_capabilities() if callable(getattr(self.camera, "get_capabilities", None)) else None
        )
        features = caps_report.get("features", {}) if isinstance(caps_report, dict) else {}

        def centered_offset(extent: int, sensor_extent: int, feature_name: str) -> int:
            feature = features.get(feature_name, {})
            minimum = int(feature.get("minimum") or 0)
            increment = max(1, int(feature.get("increment") or 1))
            raw = max(minimum, (sensor_extent - extent) // 2)
            return minimum + round((raw - minimum) / increment) * increment

        sensor_width = int(features.get("width_max", {}).get("value") or roi.width)
        sensor_height = int(features.get("height_max", {}).get("value") or roi.height)
        matches = [
            i
            for i, (height, _label, _aspect) in enumerate(VIEW_MODES)
            if roi.width == 1292
            and roi.height == height
            and roi.offset_x == centered_offset(1292, sensor_width, "offset_x")
            and roi.offset_y == centered_offset(height, sensor_height, "offset_y")
        ]
        self.view_mode_combo.setCurrentIndex(matches[0] if matches else self.view_mode_combo.count() - 1)
        self._current_roi = roi
        self._update_view_mode_availability()

    def _update_view_mode_availability(self, *_args):
        if not hasattr(self, "view_mode_apply"):
            return
        physical = bool(
            self.camera
            and self.config.backend != "simulation"
            and callable(getattr(self.camera, "apply_view_mode", None))
        )
        if not physical:
            self.view_mode_apply.setToolTip("View mode ROI controls are available for physical cameras only.")
        selected = self.view_mode_combo.currentData()
        current = self._current_roi if physical else None
        same = (
            selected is not None and current is not None and current.width == 1292 and current.height == int(selected)
        )
        busy = self._recovery_active or self._recovery_failed or self._auto_op_active or self._camera_mode_change_active
        self.view_mode_combo.setEnabled(physical and not busy)
        self.view_mode_apply.setEnabled(physical and not busy and selected is not None and not same)

    def _apply_selected_view_mode(self):
        if not self.camera or self._camera_mode_change_active:
            return
        height = self.view_mode_combo.currentData()
        if height is None or not self.view_mode_apply.isEnabled():
            return
        if self._view_mode_baseline is None:
            capability = self.camera.get_frame_rate_capability()
            feature = capability.get("feature") if isinstance(capability, dict) else None
            if feature is None or feature.value is None:
                self._show_view_mode_feedback("Cannot read frame-rate baseline")
                return
            self._view_mode_baseline = {
                "roi": self.camera.get_roi(),
                "rate": feature.value,
                "enable": capability.get("enable_feature"),
            }
        self._camera_mode_change_active = True
        # A failed transaction may have changed hardware despite a rollback
        # error, so retain the baseline for normal-shutdown restoration too.
        self._view_mode_changed = True
        self.watchdog_timer.stop()
        self.video_label.setText("Applying camera mode…")
        self._update_control_availability()
        worker = ViewModeWorker(self.camera, int(height))
        worker.signals.finished.connect(self._finish_view_mode_change)
        self._active_view_mode_worker = worker
        self._thread_pool.start(worker)

    @Slot(object, str)
    def _finish_view_mode_change(self, result, error: str):
        self._camera_mode_change_active = False
        if result is not None:
            self._view_mode_changed = True
            self._camera_error_active = False
            self._latest_pixmap = None
            self.video_label.setPixmap(QPixmap())
            self.video_label.setText("Waiting for fresh frames…")
            self._refresh_view_mode_from_camera()
        else:
            self.settings_button.setChecked(True)
            self._show_view_mode_feedback(f"Mode change failed: {error}")
            if self.camera and not self.camera.is_streaming:
                self._recovery_failed = True
                self.recovery_requested.emit(self.camera.identifier)
        self._update_control_availability()
        self._arm_watchdog()
        self.auto_operation_finished.emit()

    def _show_view_mode_feedback(self, message: str):
        anchor = self.view_mode_apply
        QToolTip.showText(anchor.mapToGlobal(QPoint(0, anchor.height())), message, anchor, anchor.rect(), 3500)

    def _gain_is_writable(self) -> bool:
        checker = getattr(self.camera, "is_feature_writable", None)
        return bool(checker("gain")) if callable(checker) else True

    def _init_ui(self):
        """Build a compact camera toolbar and an optional live settings drawer."""
        self.controls_container = QWidget()
        controls_layout = QVBoxLayout(self.controls_container)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)

        # Keep the high-value actions visible while moving continuous tuning
        # controls into a non-modal drawer so the live image keeps visual priority.
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addStretch(1)
        self.view_mode_label = QLabel("Camera mode:")
        self.view_mode_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        toolbar.addWidget(self.view_mode_label)
        toolbar.addSpacing(8)

        self.view_mode_combo = CenteredComboBox()
        self.view_mode_combo.setObjectName("cameraViewModeCombo")
        self.view_mode_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.view_mode_combo.setMinimumContentsLength(20)
        self.view_mode_combo.setFixedWidth(230)
        self.view_mode_combo.setToolTip(
            "Select a centered sensor ROI. Smaller heights increase the available camera frame-rate range."
        )
        for height, label, _aspect in VIEW_MODES:
            self.view_mode_combo.addItem(f"{label} — 1292×{height}", height)
            self.view_mode_combo.setItemData(
                self.view_mode_combo.count() - 1,
                int(Qt.AlignmentFlag.AlignCenter),
                Qt.ItemDataRole.TextAlignmentRole,
            )
        self.view_mode_combo.addItem("Custom/current", None)
        self.view_mode_combo.setItemData(
            self.view_mode_combo.count() - 1,
            int(Qt.AlignmentFlag.AlignCenter),
            Qt.ItemDataRole.TextAlignmentRole,
        )
        self.view_mode_combo.activated.connect(self._update_view_mode_availability)
        toolbar.addWidget(self.view_mode_combo)
        toolbar.addSpacing(6)

        self.view_mode_apply = QPushButton("Apply")
        self.view_mode_apply.setObjectName("cameraViewModeApply")
        self.view_mode_apply.setFixedWidth(68)
        self.view_mode_apply.clicked.connect(self._apply_selected_view_mode)
        toolbar.addWidget(self.view_mode_apply)
        toolbar.addStretch(1)

        controls_layout.addLayout(toolbar)

        self.settings_drawer = QWidget(self.controls_container)
        self.settings_drawer.setObjectName("cameraSettingsDrawer")
        drawer_grid = QGridLayout(self.settings_drawer)
        drawer_grid.setContentsMargins(0, 2, 0, 0)
        drawer_grid.setVerticalSpacing(5)
        drawer_grid.setHorizontalSpacing(8)

        # Exposure is the most frequently tuned camera parameter during
        # alignment, followed by gain. Gamma is kept available but secondary.
        exposure_min_us, exposure_max_us = 12.0, 8.45e7
        if self.camera:
            exposure_range = self.camera.get_feature_range("ExposureTimeAbs")
            if exposure_range:
                exposure_min_us, exposure_max_us = exposure_range
        initial_exposure = self.camera.exposure_us if self.camera else 10000.0
        self.exposure_control = ParameterControl(
            name="Exposure (µs)",
            min_val=exposure_min_us,
            max_val=exposure_max_us,
            initial_val=initial_exposure,
            scale="log",
            decimals=0,
        )
        self.exposure_control.valueChanged.connect(lambda val: self._handle_parameter_changed("exposure", val))
        self.exposure_btn = QPushButton("Auto")
        self.exposure_btn.setToolTip("Run single-shot auto exposure")
        self.exposure_btn.setMinimumWidth(56)
        self.exposure_status = QLabel("")
        self.exposure_status.setFixedWidth(20)
        exposure_row = QHBoxLayout()
        exposure_row.setContentsMargins(0, 0, 0, 0)
        exposure_row.setSpacing(6)
        exposure_row.addWidget(self.exposure_control, stretch=1)
        exposure_row.addWidget(self.exposure_btn)
        exposure_row.addWidget(self.exposure_status)
        drawer_grid.addLayout(exposure_row, 0, 0, 1, 3)

        gain_min_db, gain_max_db = 0.0, 30.0
        if self.camera:
            gain_range = self.camera.get_feature_range("gain")
            if gain_range:
                gain_min_db, gain_max_db = gain_range
        gain_capability_getter = getattr(self.camera, "get_feature_capability", None)
        gain_capability = gain_capability_getter("gain") if callable(gain_capability_getter) else None
        gain_unit = getattr(gain_capability, "unit", None)
        if not gain_unit and getattr(gain_capability, "name", None) == "GainRaw":
            gain_unit = "raw"
        gain_label = f"Gain ({gain_unit})" if gain_unit else "Gain"
        self.gain_control = ParameterControl(
            name=gain_label,
            min_val=gain_min_db,
            max_val=gain_max_db,
            initial_val=self.camera.get_gain() if self.camera else 0.0,
            scale="linear",
            decimals=2,
        )
        self.gain_control.valueChanged.connect(lambda val: self._handle_parameter_changed("gain", val))
        self.gain_control.setEnabled(
            bool(self.camera and self.camera.get_feature_range("gain") and self._gain_is_writable())
        )
        self.gain_btn = QPushButton("Auto")
        self.gain_btn.setToolTip("Run single-shot auto gain (if supported)")
        self.gain_btn.setMinimumWidth(56)
        self.gain_status = QLabel("")
        self.gain_status.setFixedWidth(20)
        gain_row = QHBoxLayout()
        gain_row.setContentsMargins(0, 0, 0, 0)
        gain_row.setSpacing(6)
        gain_row.addWidget(self.gain_control, stretch=1)
        gain_row.addWidget(self.gain_btn)
        gain_row.addWidget(self.gain_status)
        drawer_grid.addLayout(gain_row, 1, 0, 1, 3)

        initial_gamma = self.camera.gamma if self.camera else 1.0
        gamma_min, gamma_max = 0.1, 4.0
        if self.camera:
            gamma_range = self.camera.get_feature_range("Gamma")
            if gamma_range:
                gamma_min, gamma_max = gamma_range
        self.gamma_control = ParameterControl(
            name="Gamma",
            min_val=gamma_min,
            max_val=gamma_max,
            initial_val=initial_gamma,
            scale="linear",
            decimals=2,
        )
        self.gamma_control.valueChanged.connect(lambda val: self._handle_parameter_changed("gamma", val))
        gamma_action_space = QWidget()
        gamma_action_space.setFixedWidth(self.exposure_btn.sizeHint().width() + self.exposure_status.width() + 6)
        gamma_row = QHBoxLayout()
        gamma_row.setContentsMargins(0, 0, 0, 0)
        gamma_row.setSpacing(6)
        gamma_row.addWidget(self.gamma_control, stretch=1)
        gamma_row.addWidget(gamma_action_space)
        drawer_grid.addLayout(gamma_row, 2, 0, 1, 3)

        drawer_grid.setColumnStretch(0, 1)

        controls_layout.addWidget(self.settings_drawer)
        self.settings_drawer.setVisible(False)

        self.exposure_btn.clicked.connect(lambda: self._start_auto_op(OP_AUTO_EXPOSURE))
        self.gain_btn.clicked.connect(lambda: self._start_auto_op(OP_AUTO_GAIN))

    def _handle_parameter_changed(self, name: str, value: float):
        """Handles the valueChanged signal from any ParameterControl widget."""
        # --- FIX: Guard against calls before camera is set ---
        if (
            not self.camera
            or self._recovery_active
            or self._recovery_failed
            or self._auto_op_active
            or self._camera_mode_change_active
        ):
            logger.warning(f"Parameter '{name}' changed, but camera is not yet available.")
            return

        success = False
        control_widget = None
        reverted_value_getter = None

        match name:
            case "gamma":
                success = self.camera.set_gamma(value)
                control_widget = self.gamma_control

                def reverted_value_getter():
                    return self.camera.gamma
            case "exposure":
                success = self.camera.set_exposure(value)
                control_widget = self.exposure_control

                def reverted_value_getter():
                    return self.camera.exposure_us
            case "gain":
                success = self.camera.set_gain(value)
                control_widget = self.gain_control

                def reverted_value_getter():
                    return self.camera.get_gain()
            case _:
                logger.warning(f"Unhandled parameter change: {name}")
                return

        if control_widget:
            control_widget.visual_feedback(success)
            if not success and reverted_value_getter:
                reverted_value = reverted_value_getter()
                if reverted_value is not None:
                    QTimer.singleShot(100, lambda: control_widget.setValue(reverted_value))

    @Slot(str)
    def _handle_camera_error_message(self, message: str):
        logger.info(f"CameraPanel '{self._panel_title}' received message: {message}")
        self._camera_error_active = True
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText(message)
        self.video_label.setStyleSheet("background-color: #333; color: #ffc107;")

    def set_controls_visibility(self, visible: bool):
        self.controls_visible = bool(visible)
        self.controls_container.setVisible(self.controls_visible)
        self.settings_drawer.setVisible(self.controls_visible)
        if hasattr(self, "settings_button"):
            with QtCore.QSignalBlocker(self.settings_button):
                self.settings_button.setChecked(self.controls_visible)
        if self.controls_visible:
            self._update_view_mode_availability()
        if self.settings is not None:
            self.settings.set_camera_controls_visible(self.camera_identifier, self.controls_visible)
        logger.debug(f"CameraPanel '{self._panel_title}' controls set to visible: {self.controls_visible}")

    def get_controls_visible(self) -> bool:
        return self.controls_visible

    @Slot()
    def take_screenshot(self):
        """Captures the currently displayed frame and opens a save dialog."""

        # 1. Validation: Do we actually have an image?
        if self._latest_pixmap is None or self._latest_pixmap.isNull():
            logger.warning(f"Cannot take screenshot for {self._panel_title}: No frame available.")
            self._flash_button_feedback(self.screenshot_btn, success=False)
            return

        # 2. Generate a smart default filename
        # Format: CameraName_YYYYMMDD-HHMMSS.png
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        # Sanitize the camera name to be file-system safe
        safe_name = (
            "".join(c for c in self._panel_title if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        )
        default_filename = f"{safe_name}_{timestamp}.png"

        # --- NEW: Use the memory ---
        initial_path = self.last_save_dir / default_filename
        # ---------------------------

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Screenshot",
            str(initial_path),  # <--- Pass the full path string
            "PNG Images (*.png);;JPEG Images (*.jpg);;BMP Images (*.bmp)",
        )

        if not file_path:
            return

        # --- NEW: Update the memory ---
        self.last_save_dir = Path(file_path).parent
        if self.settings is not None:
            self.settings.set_camera_screenshot_directory(self.camera_identifier, self.last_save_dir)
        # ------------------------------

        # 4. Save the file if user didn't cancel
        if file_path:
            try:
                # Note: _latest_pixmap is the full-resolution image from the camera
                # BEFORE it gets scaled down to fit the UI label.
                # It does NOT contain the FPS text overlay, which is scientifically preferred.
                success = self._latest_pixmap.save(file_path)

                if success:
                    logger.info(f"Screenshot saved to {file_path}")
                    self._flash_button_feedback(self.screenshot_btn, success=True)
                else:
                    logger.error(f"Qt reported failure saving screenshot to {file_path}")
                    self._flash_button_feedback(self.screenshot_btn, success=False)

            except Exception:
                logger.exception("Exception saving screenshot")
                self._flash_button_feedback(self.screenshot_btn, success=False)

    def _flash_button_feedback(self, button: QWidget, success: bool):
        """Helper to flash the button green (success) or red (failure)."""
        original_style = button.styleSheet()
        color = "#ccffcc" if success else "#ffcccc"  # Light green vs Light red
        button.setStyleSheet(f"background-color: {color}; border: 1px solid {'green' if success else 'red'};")

        # Revert after 500ms
        feedback_timer = QTimer(button)
        feedback_timer.setSingleShot(True)
        feedback_timer.timeout.connect(lambda: button.setStyleSheet(original_style))
        feedback_timer.start(500)

    # --- REFACTOR: All old slider/edit handler and helper methods are now removed ---
    # _exposure_to_slider, _slider_to_exposure, _handle_gamma_slider,
    # _handle_gamma_edit, _update_camera_gamma, _revert_gamma_ui, and their
    # exposure equivalents have been deleted. Their logic is now encapsulated
    # within the ParameterControl class.

    def _start_auto_op(self, op_type: str):
        """Starts an auto-operation in a worker thread."""
        # --- FIX: Guard against calls before camera is set ---
        if not self.camera or self._recovery_active or self._recovery_failed or self._auto_op_active:
            logger.warning(f"Cannot start '{op_type}': camera is not yet available.")
            return

        if op_type == OP_AUTO_EXPOSURE:
            if not self.exposure_btn.isEnabled():
                return
            self.exposure_btn.setEnabled(False)
            self.exposure_status.setText("⟳")
            self.exposure_status.setStyleSheet("color: orange; font-weight: bold;")
        elif op_type == OP_AUTO_GAIN:
            if not self.gain_btn.isEnabled():
                return
            self.gain_btn.setEnabled(False)
            self.gain_status.setText("⟳")
            self.gain_status.setStyleSheet("color: orange; font-weight: bold;")
        else:
            return

        self._auto_op_active = True
        self.watchdog_timer.stop()
        self.exposure_btn.setEnabled(False)
        self.gain_btn.setEnabled(False)
        self._update_control_availability()
        worker = AutoOpWorker(self.camera, op_type, self)
        self._thread_pool.start(worker)

    @Slot(str)
    def handle_auto_finished(self, op_type: str):
        self._auto_op_active = False
        self.exposure_btn.setEnabled(True)
        self.gain_btn.setEnabled(True)
        self._update_control_availability()
        self.auto_operation_finished.emit()
        self._arm_watchdog()

    @Slot(str, float)
    def handle_auto_result(self, op_type: str, result_value: float):
        logger.info(f"Auto operation '{op_type}' succeeded. Result: {result_value}")
        if op_type == "auto_exposure":
            # --- REFACTOR: Update ParameterControl directly ---
            self.exposure_control.setValue(result_value)
            self.exposure_status.setText("✓")
            self.exposure_status.setStyleSheet("color: green; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.clear_status_indicators("exposure"))
        elif op_type == "auto_gain":
            self.gain_control.setValue(result_value)
            self.gain_status.setText("✓")
            self.gain_status.setStyleSheet("color: green; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.clear_status_indicators("gain"))

    @Slot(str, str)
    def handle_auto_error(self, op_type: str, error_str: str):
        logger.error(f"Auto operation '{op_type}' failed: {error_str}")
        if op_type == "auto_exposure":
            self.exposure_status.setText("✗")
            self.exposure_status.setStyleSheet("color: red; font-weight: bold;")
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("exposure"))
            # --- REFACTOR: Revert UI using ParameterControl ---
            reverted_value = self.camera.exposure_us
            if reverted_value is not None:
                self.exposure_control.setValue(reverted_value)
        elif op_type == "auto_gain":
            self.gain_status.setText("✗")
            self.gain_status.setStyleSheet("color: red; font-weight: bold;")
            self.gain_control.setValue(self.camera.get_gain())
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("gain"))

    def clear_status_indicators(self, control: str | None = None):
        if control is None or control == "exposure":
            self.exposure_status.setText("")
        if control is None or control == "gain":
            self.gain_status.setText("")

    @Slot(np.ndarray)
    def process_new_frame_data(self, frame: np.ndarray | None = None):
        """Refresh recovery state from throttled camera activity."""
        if self._auto_op_active:
            return
        # A fresh frame marks recovery from a camera acquisition error. Ignore
        # conversion results already queued when the error was reported.
        if not self._recovery_active:
            state_changed = self._camera_error_active or self._automatic_recovery_used or self._recovery_failed
            self._camera_error_active = False
            self._automatic_recovery_used = False
            self._recovery_failed = False
            if state_changed:
                self._update_control_availability()
            self._arm_watchdog()

    @Slot(str)
    def _handle_conversion_error(self, error_msg: str):
        """Slot to handle errors from the image conversion worker."""
        logger.warning(f"Failed to process frame for {self._panel_title}: {error_msg}")
        # Optionally display an error state on the video label
        # self.set_frame_pixmap(None)

    @Slot(QImage)
    def _accept_converted_image(self, q_img: QImage):
        """Store conversion output and queue at most one GUI display event."""
        notify = False
        with self._display_lock:
            if self._panel_closing or not self._presentation_enabled:
                return
            if self._latest_qimage is not None:
                self.coalesced_images += 1
            self._latest_qimage = q_img
            self.max_pending_images = 1
            if not self._display_notification_pending:
                self._display_notification_pending = True
                notify = True
        if notify:
            self._display_image_ready.emit()

    @Slot()
    def _display_latest_converted_image(self):
        with self._display_lock:
            q_img = self._latest_qimage
            self._latest_qimage = None
            self._display_notification_pending = False
            active = self._presentation_enabled and not self._panel_closing
        if active and q_img is not None:
            self._display_converted_image(q_img)

    def _set_presentation_enabled(self, enabled: bool):
        with self._display_lock:
            self._presentation_enabled = enabled and not self._panel_closing
            if not self._presentation_enabled:
                self._latest_qimage = None
                self._display_notification_pending = False
        if self.conversion_worker is not None:
            self.conversion_worker.set_active(enabled)

    @Slot(QImage)
    def _display_converted_image(self, q_img: QImage):
        """Displays the converted QImage in the video label.

        This slot is connected to the `ImageConversionWorker.image_ready`
        signal and runs on the main GUI thread. It handles scaling the pixmap
        to fit the label while preserving aspect ratio and painting the FPS overlay.

        Args:
            q_img: The `QImage` converted by the background worker.
        """
        if self._camera_error_active:
            return
        if q_img.isNull():
            self.set_frame_pixmap(None)
            return

        try:
            self.video_label.setStyleSheet("background-color: transparent;")
            pixmap = QPixmap.fromImage(q_img)  # This is a fast operation

            # Set aspect ratio on the first valid frame
            if self.video_label._aspect_ratio is None and not pixmap.isNull():
                logger.debug(
                    f"Panel {self._panel_title}: Setting aspect ratio from first pixmap W:{pixmap.width()} H:{pixmap.height()}"
                )
                self.video_label.setAspectRatio(pixmap.width(), pixmap.height())

            self.set_frame_pixmap(pixmap)
        except Exception:
            logger.exception(f"Panel {self._panel_title}: Unhandled error displaying converted image")
            self.set_frame_pixmap(None)

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent):
        """Emits a signal to toggle fullscreen mode when double-clicked."""
        # Only trigger on left button double-click
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def showEvent(self, event: QtGui.QShowEvent):
        """Override for QFrame.showEvent."""
        super().showEvent(event)
        self._set_presentation_enabled(True)
        # --- FIX: Check if camera exists before using it. Use panel title for logging. ---
        if self.camera:
            logger.debug(f"CameraPanel for {self.camera.camera_name} shown, starting watchdog.")
            self._arm_watchdog()
        else:
            logger.debug(f"Placeholder CameraPanel '{self._panel_title}' shown.")

    def hideEvent(self, event: QtGui.QHideEvent):
        """Override for QFrame.hideEvent."""
        super().hideEvent(event)
        self._set_presentation_enabled(False)
        # --- FIX: Check if camera exists before using it. Use panel title for logging. ---
        if self.camera:
            logger.debug(f"CameraPanel for {self.camera.camera_name} hidden, stopping watchdog.")
            self.watchdog_timer.stop()
        else:
            logger.debug(f"Placeholder CameraPanel '{self._panel_title}' hidden.")

    @Slot(QPixmap)
    def set_frame_pixmap(self, pixmap: QPixmap | None):
        is_null_or_none = pixmap is None or pixmap.isNull()
        if not is_null_or_none:
            self._latest_pixmap = pixmap
            self._delayed_display_update()
        else:
            self._latest_pixmap = None
            self._delayed_display_update()

    @Slot(float)
    def update_fps(self, fps: float):
        self._current_fps = fps
        self.process_new_frame_data()

    def resizeEvent(self, event: QtGui.QResizeEvent):
        super().resizeEvent(event)
        now = time.monotonic()
        if now - self._last_resize_time > (CAMERA_RESIZE_EVENT_THROTTLE_MS / 1000.0):
            self._display_size_cache = self.video_label.size()
            self._resize_timer.start(CAMERA_RESIZE_UPDATE_DELAY_MS)
            self._last_resize_time = now
        else:
            self._resize_timer.start(CAMERA_RESIZE_UPDATE_DELAY_MS)

    def _delayed_display_update(self):
        if self._camera_error_active:
            return
        if self._latest_pixmap and not self._latest_pixmap.isNull():
            target_size = self.video_label.size()
            if target_size.isEmpty() or target_size.width() <= 0 or target_size.height() <= 0:
                if self._display_size_cache and not self._display_size_cache.isEmpty():
                    target_size = self._display_size_cache
                else:
                    return
            scaled_pixmap = self._latest_pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if self._show_fps:
                painter = QPainter(scaled_pixmap)
                painter.setPen(self._fps_color)
                painter.setFont(self._fps_font)
                painter.drawText(5, 20, f"FPS: {self._current_fps:.1f}")
                painter.end()
            self.video_label.setPixmap(scaled_pixmap)
        else:
            self.video_label.setText(MSG_CAMERA_WAITING)
            self.video_label.setStyleSheet("background-color: black; color: grey;")
            self.video_label.setPixmap(QPixmap())

    def prepare_camera_shutdown(self):
        """Restore camera state, then prevent new panel work before producer shutdown."""
        if self._camera_shutdown_prepared:
            return
        self._camera_shutdown_prepared = True
        self._panel_closing = True
        if self.camera and self._view_mode_changed and self._view_mode_baseline is not None:
            try:
                self.camera.restore_view_mode_baseline(self._view_mode_baseline)
            except Exception as exc:  # noqa: BLE001 - shutdown restoration is best effort.
                logger.warning("Could not restore camera view-mode baseline: %s", exc)
        self._set_presentation_enabled(False)
        self.watchdog_timer.stop()
        self._resize_timer.stop()

    def closeEvent(self, event: QtGui.QCloseEvent):
        """Tear down the conversion consumer after camera production has stopped."""
        logger.debug(f"Closing CameraPanel for {self._panel_title}")
        self.prepare_camera_shutdown()
        if self.camera and self.conversion_worker:
            # Disconnect the signal to prevent sending frames to a closing worker
            try:
                self.camera.new_frame.disconnect(self.conversion_worker.submit_frame)
            except (TypeError, RuntimeError):
                # This can happen if the connection was already broken. Safe to ignore.
                pass

        if self.conversion_thread and self.conversion_thread.isRunning():
            self.conversion_worker.stop()
            self.conversion_thread.quit()
            if not self.conversion_thread.wait(3000):
                logger.warning(f"Conversion thread for {self._panel_title} did not close gracefully.")

        super().closeEvent(event)
