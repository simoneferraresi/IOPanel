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
import time
from typing import Literal

import cv2
import numpy as np
from PySide6 import QtCore, QtGui
from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from config_model import CameraConfig
from hardware.camera import VimbaCam
from ui.constants import (
    CAMERA_RESIZE_EVENT_THROTTLE_MS,
    CAMERA_RESIZE_UPDATE_DELAY_MS,
    CAMERA_WATCHDOG_INTERVAL_MS,
    MSG_CAMERA_CONNECTING,
    MSG_CAMERA_WAITING,
    OP_AUTO_EXPOSURE,
    OP_AUTO_GAIN,
)
from ui.theme import CAMERA_PANEL_STYLE

logger = logging.getLogger("LabApp.camera_widgets")


class ImageConversionSignals(QObject):
    """Defines signals for the ImageConversionWorker.

    A separate QObject is required for signals because QRunnable does not
    inherit from QObject.
    """

    image_ready = Signal(QImage)
    conversion_error = Signal(str)


class ImageConversionWorker(QRunnable):
    """A QRunnable worker that converts a numpy array to a QImage.

    This worker runs on a background thread from the global QThreadPool to
    prevent the expensive numpy-to-QImage conversion from blocking the GUI thread,
    which is critical for maintaining a high and smooth frame rate display.
    """

    def __init__(self, frame: np.ndarray, is_mono: bool, camera_name: str):
        """Initializes the image conversion worker.

        Args:
            frame: The raw numpy array frame received from the camera.
            is_mono: A boolean indicating if the frame is monochrome (True) or
                color (False). This determines the conversion logic.
            camera_name: The name of the camera panel, used for logging.
        """
        super().__init__()
        self.frame = frame
        self.is_mono = is_mono
        self.camera_name = camera_name
        self.signals = ImageConversionSignals()

    @Slot()
    def run(self):
        """The workhorse method that runs in the background thread.

        Converts the numpy frame to the appropriate QImage format. Emits
        `image_ready` on success or `conversion_error` on failure.
        """
        try:
            h, w = self.frame.shape[:2]
            q_img: QImage | None = None

            if self.is_mono:
                # Standardize mono frame to 2 dimensions (H, W)
                frame = self.frame
                if frame.ndim == 3 and frame.shape[2] == 1:
                    frame = self.frame.reshape(h, w)

                if frame.ndim == 2:
                    # Ensure the memory layout is C-contiguous for QImage
                    if not frame.flags["C_CONTIGUOUS"]:
                        frame = np.ascontiguousarray(frame)
                    q_img = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_Grayscale8)
                else:
                    raise TypeError(f"Mono camera provided unexpected frame shape: {frame.shape}")
            else:  # Color
                if self.frame.ndim == 3 and self.frame.shape[2] == 3:
                    # Convert BGR (from OpenCV) to RGB for QImage
                    frame_rgb = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
                    q_img = QImage(frame_rgb.data, w, h, frame_rgb.strides[0], QImage.Format.Format_RGB888)
                else:
                    raise TypeError(f"Color camera provided unexpected frame shape: {self.frame.shape}")

            if q_img:
                # The QImage must be copied because the underlying numpy buffer
                # will go out of scope and be garbage-collected.
                self.signals.image_ready.emit(q_img.copy())
            else:
                self.signals.conversion_error.emit("Converted QImage was null.")
        except Exception as e:
            error_msg = f"Panel {self.camera_name}: Unhandled error converting frame: {e}"
            logger.exception(error_msg)
            self.signals.conversion_error.emit(str(e))


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
        self.min_val = max(1e-9, min_val)  # Ensure min_val is positive for log scale
        self.max_val = max_val
        self.scale = scale
        self.decimals = decimals

        self._init_ui()
        self._connect_signals()
        self.setValue(initial_val, emit_signal=False)

    def _init_ui(self):
        """Creates and arranges the label, slider, and line edit widgets."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.label = QLabel(f"{self.param_name}:")
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
        original_style = self.edit.styleSheet()
        color = "#e0ffe0" if success else "#ffe0e0"  # Light green/red
        self.edit.setStyleSheet(f"background-color: {color};")
        QTimer.singleShot(duration_ms, lambda: self.edit.setStyleSheet(original_style))


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

            if self.op_type == OP_AUTO_EXPOSURE:
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
                else:
                    logger.error(f"Worker: {self.camera.camera_name} - camera.set_auto_exposure_once() returned False.")
                    success = False

            elif self.op_type == OP_AUTO_GAIN:
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
                else:
                    logger.error(f"Worker: {self.camera.camera_name} - camera.set_auto_gain_once() returned False.")
                    success = False

            if success and result_value is not None:
                QMetaObject.invokeMethod(
                    self.panel_callback,
                    "handle_auto_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, self.op_type),  # op_type is still the string
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
            logger.error(error_msg, exc_info=True)
            QMetaObject.invokeMethod(
                self.panel_callback,
                "handle_auto_error",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, self.op_type),
                Q_ARG(str, str(e)),
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

    def __init__(
        self,
        camera: VimbaCam | None,
        title: str,
        config: CameraConfig,
        parent: QWidget | None = None,
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
        self._display_size_cache: QtCore.QSize | None = None
        self._thread_pool = QThreadPool.globalInstance()
        self._last_resize_time: float = 0.0
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_display_update)
        self.controls_visible: bool = False

        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(CAMERA_WATCHDOG_INTERVAL_MS)
        self.watchdog_timer.setSingleShot(True)
        if self.camera:
            self.watchdog_timer.timeout.connect(self.camera.attempt_recovery)

        self._current_fps: float = 0.0
        self._show_fps: bool = True
        self._fps_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        self._fps_color = QColor("lime")
        self.setObjectName(f"cameraPanel_{camera.identifier if camera else title.replace(' ', '_')}")
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setMinimumSize(320, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        try:
            self.setStyleSheet(CAMERA_PANEL_STYLE)
        except NameError:
            logger.warning("CAMERA_PANEL_STYLE not found, using default styles.")

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 4, 4)
        self.main_layout.setSpacing(4)

        self._init_ui()
        self.main_layout.addWidget(self.controls_container)

        self.video_label = AspectLockedLabel(self)
        if self.camera:
            self.video_label.setText(MSG_CAMERA_WAITING)
        else:
            self.video_label.setText(MSG_CAMERA_CONNECTING.format(self._panel_title))

        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: transparent; color: grey;")
        self.main_layout.addWidget(self.video_label, stretch=1)

        self.controls_container.setVisible(self.controls_visible)
        self.clear_status_indicators()

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

        self.watchdog_timer.timeout.connect(self.camera.attempt_recovery)
        self._update_controls_from_camera()

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

    def _init_ui(self):
        """Initializes the UI, using defaults if camera is not yet available."""
        self.controls_container = QWidget()
        controls_grid = QGridLayout(self.controls_container)
        controls_grid.setVerticalSpacing(5)
        controls_grid.setHorizontalSpacing(8)

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
        controls_grid.addWidget(self.gamma_control, 0, 0, 1, 3)

        # --- REVISED LOGIC FOR EXPOSURE ---
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
        controls_grid.addWidget(self.exposure_control, 1, 0, 1, 3)

        auto_btn_layout = QHBoxLayout()
        self.exposure_btn = QPushButton("Auto Exposure")
        self.exposure_btn.setToolTip("Run single-shot auto exposure")
        self.exposure_status = QLabel("")
        self.exposure_status.setFixedWidth(20)
        auto_btn_layout.addWidget(self.exposure_btn)
        auto_btn_layout.addWidget(self.exposure_status)
        auto_btn_layout.addStretch(1)
        self.gain_btn = QPushButton("Auto Gain")
        self.gain_btn.setToolTip("Run single-shot auto gain (if supported)")
        self.gain_status = QLabel("")
        self.gain_status.setFixedWidth(20)
        auto_btn_layout.addWidget(self.gain_btn)
        auto_btn_layout.addWidget(self.gain_status)
        controls_grid.addLayout(auto_btn_layout, 2, 0, 1, 3)
        controls_grid.setColumnStretch(1, 1)

        self.exposure_btn.clicked.connect(lambda: self._start_auto_op(OP_AUTO_EXPOSURE))
        self.gain_btn.clicked.connect(lambda: self._start_auto_op(OP_AUTO_GAIN))

    def _handle_parameter_changed(self, name: str, value: float):
        """Handles the valueChanged signal from any ParameterControl widget."""
        # --- FIX: Guard against calls before camera is set ---
        if not self.camera:
            logger.warning(f"Parameter '{name}' changed, but camera is not yet available.")
            return

        success = False
        control_widget = None
        if name == "gamma":
            success = self.camera.set_gamma(value)
            control_widget = self.gamma_control
        elif name == "exposure":
            success = self.camera.set_exposure(value)
            control_widget = self.exposure_control

        if control_widget:
            control_widget.visual_feedback(success)
            if not success:
                if name == "exposure":
                    reverted_value = self.camera.exposure_us
                else:  # Assumes 'gamma'
                    reverted_value = self.camera.gamma

                if reverted_value is not None:
                    QTimer.singleShot(100, lambda: control_widget.setValue(reverted_value))

    @Slot(str)
    def _handle_camera_error_message(self, message: str):
        logger.info(f"CameraPanel '{self._panel_title}' received message: {message}")
        self.video_label.setText(message)
        self.video_label.setPixmap(QPixmap())
        self.video_label.setStyleSheet("background-color: #333; color: #ffc107;")

    def set_controls_visibility(self, visible: bool):
        if self.controls_visible == visible:
            return
        self.controls_visible = visible
        self.controls_container.setVisible(self.controls_visible)
        logger.debug(f"CameraPanel '{self._panel_title}' controls set to visible: {self.controls_visible}")

    def get_controls_visible(self) -> bool:
        return self.controls_visible

    # --- REFACTOR: All old slider/edit handler and helper methods are now removed ---
    # _exposure_to_slider, _slider_to_exposure, _handle_gamma_slider,
    # _handle_gamma_edit, _update_camera_gamma, _revert_gamma_ui, and their
    # exposure equivalents have been deleted. Their logic is now encapsulated
    # within the ParameterControl class.

    def _start_auto_op(self, op_type: str):
        """Starts an auto-operation in a worker thread."""
        # --- FIX: Guard against calls before camera is set ---
        if not self.camera:
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

        worker = AutoOpWorker(self.camera, op_type, self)
        self._thread_pool.start(worker)

    @Slot(str, float)
    def handle_auto_result(self, op_type: str, result_value: float):
        logger.info(f"Auto operation '{op_type}' succeeded. Result: {result_value}")
        if op_type == "auto_exposure":
            # --- REFACTOR: Update ParameterControl directly ---
            self.exposure_control.setValue(result_value)
            self.exposure_status.setText("✓")
            self.exposure_status.setStyleSheet("color: green; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.clear_status_indicators("exposure"))
            self.exposure_btn.setEnabled(True)
        elif op_type == "auto_gain":
            self.gain_status.setText("✓")
            self.gain_status.setStyleSheet("color: green; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.clear_status_indicators("gain"))
            self.gain_btn.setEnabled(True)

    @Slot(str, str)
    def handle_auto_error(self, op_type: str, error_str: str):
        logger.error(f"Auto operation '{op_type}' failed: {error_str}")
        if op_type == "auto_exposure":
            self.exposure_status.setText("✗")
            self.exposure_status.setStyleSheet("color: red; font-weight: bold;")
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("exposure"))
            self.exposure_btn.setEnabled(True)
            # --- REFACTOR: Revert UI using ParameterControl ---
            reverted_value = self.camera.exposure_us
            if reverted_value is not None:
                self.exposure_control.setValue(reverted_value)
        elif op_type == "auto_gain":
            self.gain_status.setText("✗")
            self.gain_status.setStyleSheet("color: red; font-weight: bold;")
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("gain"))
            self.gain_btn.setEnabled(True)

    def clear_status_indicators(self, control: str | None = None):
        if control is None or control == "exposure":
            self.exposure_status.setText("")
        if control is None or control == "gain":
            self.gain_status.setText("")

    @Slot(np.ndarray)
    def process_new_frame_data(self, frame: np.ndarray):
        """Receives a raw numpy frame and initiates its display process.

        This slot is connected to the `VimbaCam.new_frame` signal. It creates
        an `ImageConversionWorker` to convert the frame to a `QImage` on a
        background thread, ensuring the GUI remains responsive.

        Args:
            frame: The raw numpy array frame from the camera.
        """
        if not self.camera or not self.isVisible():
            return

        self.watchdog_timer.start()

        if frame is None or frame.size == 0 or not self.camera.device:
            self.set_frame_pixmap(None)
            return

        # is_mono is guaranteed by VimbaCam to be a boolean
        is_mono = self.camera.is_mono

        # Create and configure the worker
        worker = ImageConversionWorker(frame, is_mono, self._panel_title)
        worker.signals.image_ready.connect(self._display_converted_image)
        worker.signals.conversion_error.connect(self._handle_conversion_error)

        # Submit to the global thread pool for execution
        self._thread_pool.start(worker)

    @Slot(str)
    def _handle_conversion_error(self, error_msg: str):
        """Slot to handle errors from the image conversion worker."""
        logger.warning(f"Failed to process frame for {self._panel_title}: {error_msg}")
        # Optionally display an error state on the video label
        # self.set_frame_pixmap(None)

    @Slot(QImage)
    def _display_converted_image(self, q_img: QImage):
        """Displays the converted QImage in the video label.

        This slot is connected to the `ImageConversionWorker.image_ready`
        signal and runs on the main GUI thread. It handles scaling the pixmap
        to fit the label while preserving aspect ratio and painting the FPS overlay.

        Args:
            q_img: The `QImage` converted by the background worker.
        """
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
        except Exception as e:
            logger.exception(f"Panel {self._panel_title}: Unhandled error displaying converted image: {e}")
            self.set_frame_pixmap(None)

    def showEvent(self, event: QtGui.QShowEvent):
        """Override for QFrame.showEvent."""
        super().showEvent(event)
        # --- FIX: Check if camera exists before using it. Use panel title for logging. ---
        if self.camera:
            logger.debug(f"CameraPanel for {self.camera.camera_name} shown, starting watchdog.")
            self.watchdog_timer.start()
        else:
            logger.debug(f"Placeholder CameraPanel '{self._panel_title}' shown.")

    def hideEvent(self, event: QtGui.QHideEvent):
        """Override for QFrame.hideEvent."""
        super().hideEvent(event)
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

    def closeEvent(self, event: QtGui.QCloseEvent):
        """Handles the widget close event.

        Ensures that all timers associated with this panel are stopped to
        prevent them from firing after the widget is gone.

        Args:
            event: The close event.
        """
        logger.debug(f"Closing CameraPanel for {self._panel_title}")
        # --- FIX: Check if camera exists before stopping its timer. ---
        if self.camera:
            self.watchdog_timer.stop()
        self._resize_timer.stop()
        super().closeEvent(event)
