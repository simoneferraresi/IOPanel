import logging
import math
import time
from typing import Literal, Optional

import cv2
import numpy as np
from PySide6 import QtCore, QtGui
from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
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
from ui.theme import CAMERA_PANEL_STYLE

logger = logging.getLogger("LabApp.camera_widgets")


class ParameterControl(QWidget):
    """
    A compound widget for controlling a single camera parameter.

    Encapsulates a label, a logarithmic or linear slider, and a line edit,
    keeping them synchronized. Emits a `valueChanged` signal when the
    parameter is changed by the user.
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
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.param_name = name
        self.min_val = max(1e-9, min_val)  # Ensure min_val is positive for log scale
        self.max_val = max_val
        self.scale = scale
        self.decimals = decimals

        self._init_ui()
        self._connect_signals()

        # Set initial value without emitting signal
        self.setValue(initial_val, emit_signal=False)

    def _init_ui(self):
        """Creates the label, slider, and line edit widgets."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.label = QLabel(f"{self.param_name}:")
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)  # Always use a fixed slider range

        self.edit = QLineEdit()
        self.edit.setValidator(
            QtGui.QDoubleValidator(self.min_val, self.max_val, self.decimals)
        )
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
        """Programmatically sets the value of the control."""
        value = max(self.min_val, min(self.max_val, value))
        self.slider.blockSignals(True)
        self.edit.blockSignals(True)

        self.slider.setValue(self._value_to_slider(value))
        self.edit.setText(f"{value:.{self.decimals}f}")

        self.slider.blockSignals(False)
        self.edit.blockSignals(False)

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
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._aspect_ratio: Optional[float] = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setScaledContents(False)

    def setAspectRatio(self, width: int, height: int):
        if height > 0:
            new_ar = width / height
            if self._aspect_ratio is None or abs(new_ar - self._aspect_ratio) > 1e-6:
                self._aspect_ratio = new_ar
                self.updateGeometry()
        else:
            self._aspect_ratio = None

    def hasHeightForWidth(self) -> bool:
        return self._aspect_ratio is not None

    def heightForWidth(self, width: int) -> int:
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
        if op_type not in ["auto_exposure", "auto_gain"]:
            raise ValueError(f"Unknown auto operation type: {op_type}")
        self.camera = camera
        self.op_type = op_type
        self.panel_callback = panel_callback

    def run(self):
        try:
            result_value: Optional[float] = None
            success = False
            camera_method_success = False

            if self.op_type == "auto_exposure":
                logger.debug(
                    f"Worker: Calling camera.set_auto_exposure_once() for {self.camera.camera_name}"
                )
                camera_method_success = self.camera.set_auto_exposure_once()
                if camera_method_success:
                    logger.info(
                        f"Worker: {self.camera.camera_name} - ExposureAuto 'Once' mode set. Waiting for adjustment..."
                    )
                    time.sleep(1.5)
                    result_value = self.camera.get_exposure()
                    logger.info(
                        f"Worker: {self.camera.camera_name} - Auto Exposure adjustment finished. New value: {result_value}"
                    )
                    success = result_value is not None
                else:
                    logger.error(
                        f"Worker: {self.camera.camera_name} - camera.set_auto_exposure_once() returned False."
                    )
                    success = False

            elif self.op_type == "auto_gain":
                logger.debug(
                    f"Worker: Calling camera.set_auto_gain_once() for {self.camera.camera_name}"
                )
                camera_method_success = self.camera.set_auto_gain_once()
                if camera_method_success:
                    logger.info(
                        f"Worker: {self.camera.camera_name} - GainAuto 'Once' mode set. Waiting for adjustment..."
                    )
                    time.sleep(1.5)
                    result_value = self.camera.get_gain()
                    logger.info(
                        f"Worker: {self.camera.camera_name} - Auto Gain adjustment finished. New value: {result_value}"
                    )
                    success = result_value is not None
                else:
                    logger.error(
                        f"Worker: {self.camera.camera_name} - camera.set_auto_gain_once() returned False."
                    )
                    success = False

            if success and result_value is not None:
                QMetaObject.invokeMethod(
                    self.panel_callback,
                    "handle_auto_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, self.op_type),
                    Q_ARG(float, result_value),
                )
            elif not camera_method_success:
                raise RuntimeError(
                    f"Camera method for {self.op_type} reported failure to set 'Once' mode."
                )
            elif result_value is None and camera_method_success:
                raise RuntimeError(
                    f"Camera method for {self.op_type} set 'Once' mode, but failed to retrieve new value."
                )

        except Exception as e:
            error_msg = (
                f"Error during {self.op_type} for {self.camera.camera_name}: {e}"
            )
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
    """
    Displays a live camera feed with collapsible controls for adjusting exposure,
    gain, and gamma. Provides visual feedback for auto operations.
    Includes FPS display overlay.
    """

    def __init__(
        self,
        camera: VimbaCam,
        title: str,
        config: CameraConfig,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.camera = camera
        self.config = config
        self._panel_title = title
        self._latest_pixmap: Optional[QPixmap] = None
        self._display_size_cache: Optional[QtCore.QSize] = None
        self._thread_pool = QThreadPool.globalInstance()
        self._last_resize_time: float = 0.0
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_display_update)
        self.controls_visible: bool = False

        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(3000)
        self.watchdog_timer.setSingleShot(True)
        self.watchdog_timer.timeout.connect(self.camera.attempt_recovery)

        self._current_fps: float = 0.0
        self._show_fps: bool = True
        self._fps_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        self._fps_color = QColor("lime")

        self.setObjectName(f"cameraPanel_{camera.identifier}")
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

        # --- REFACTOR: UI setup using ParameterControl ---
        self._init_ui()

        self.main_layout.addWidget(self.controls_container)

        self.video_label = AspectLockedLabel(self)
        self.video_label.setText("Initializing Camera...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: transparent; color: grey;")
        self.main_layout.addWidget(self.video_label, stretch=1)

        self.controls_container.setVisible(self.controls_visible)
        self.clear_status_indicators()

    def _init_ui(self):
        """Initializes the UI, now using the ParameterControl widget."""
        self.controls_container = QWidget()
        controls_grid = QGridLayout(self.controls_container)
        controls_grid.setVerticalSpacing(5)
        controls_grid.setHorizontalSpacing(8)

        # --- REFACTOR: Create Gamma control ---
        self.gamma_control = ParameterControl(
            name="Gamma",
            min_val=0.1,
            max_val=4.0,
            initial_val=self.camera.get_setting("gamma") or 1.0,
            scale="linear",
            decimals=2,
        )
        self.gamma_control.valueChanged.connect(
            lambda val: self._handle_parameter_changed("gamma", val)
        )
        controls_grid.addWidget(self.gamma_control, 0, 0, 1, 3)

        # --- REFACTOR: Create Exposure control ---
        exposure_min_us = self.camera.get_feature_min_max("ExposureTimeAbs", 12.0)
        exposure_max_us = self.camera.get_feature_min_max(
            "ExposureTimeAbs", 8.45e7, is_max=True
        )
        self.exposure_control = ParameterControl(
            name="Exposure (µs)",
            min_val=exposure_min_us,
            max_val=exposure_max_us,
            initial_val=self.camera.get_setting("exposure_us") or 10000.0,
            scale="log",
            decimals=0,
        )
        self.exposure_control.valueChanged.connect(
            lambda val: self._handle_parameter_changed("exposure", val)
        )
        controls_grid.addWidget(self.exposure_control, 1, 0, 1, 3)

        # Auto Buttons remain the same
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

        # Connect button signals
        self.exposure_btn.clicked.connect(lambda: self._start_auto_op("auto_exposure"))
        self.gain_btn.clicked.connect(lambda: self._start_auto_op("auto_gain"))

    # --- REFACTOR: New generic handler for parameter changes ---
    def _handle_parameter_changed(self, name: str, value: float):
        """
        Handles the valueChanged signal from any ParameterControl widget.

        Args:
            name: The name of the parameter that changed (e.g., 'gamma', 'exposure').
            value: The new float value of the parameter.
        """
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
                # Revert UI if setting camera failed
                reverted_value = self.camera.get_setting(
                    f"{name}_us" if name == "exposure" else name
                )
                if reverted_value is not None:
                    QTimer.singleShot(
                        100, lambda: control_widget.setValue(reverted_value)
                    )

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
        logger.debug(
            f"CameraPanel '{self._panel_title}' controls set to visible: {self.controls_visible}"
        )

    def get_controls_visible(self) -> bool:
        return self.controls_visible

    # --- REFACTOR: All old slider/edit handler and helper methods are now removed ---
    # _exposure_to_slider, _slider_to_exposure, _handle_gamma_slider,
    # _handle_gamma_edit, _update_camera_gamma, _revert_gamma_ui, and their
    # exposure equivalents have been deleted. Their logic is now encapsulated
    # within the ParameterControl class.

    def _start_auto_op(self, op_type: str):
        # This method remains largely the same, but now updates ParameterControl on success
        if op_type == "auto_exposure":
            if not self.exposure_btn.isEnabled():
                return
            self.exposure_btn.setEnabled(False)
            self.exposure_status.setText("⟳")
            self.exposure_status.setStyleSheet("color: orange; font-weight: bold;")
        elif op_type == "auto_gain":
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
            reverted_value = self.camera.get_setting("exposure_us")
            if reverted_value is not None:
                self.exposure_control.setValue(reverted_value)
        elif op_type == "auto_gain":
            self.gain_status.setText("✗")
            self.gain_status.setStyleSheet("color: red; font-weight: bold;")
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("gain"))
            self.gain_btn.setEnabled(True)

    def clear_status_indicators(self, control: Optional[str] = None):
        if control is None or control == "exposure":
            self.exposure_status.setText("")
        if control is None or control == "gain":
            self.gain_status.setText("")

    # --- The rest of the CameraPanel methods (frame processing, etc.) remain unchanged ---
    @Slot(object)
    def process_new_frame_data(self, frame: Optional[np.ndarray]):
        self.watchdog_timer.start()
        if frame is None:
            self.set_frame_pixmap(None)
            return
        if not self.camera or self.camera.device is None:
            self.set_frame_pixmap(None)
            return
        try:
            is_mono = self.camera.is_mono
            h, w = frame.shape[:2]
            q_img: Optional[QImage] = None
            if is_mono is None:
                logger.log(
                    logging.DEBUG if hasattr(self, "_warned_mono") else logging.WARNING,
                    f"Mono status unknown for {self.camera.camera_name}, assuming color for conversion.",
                )
                self._warned_mono = True
                if len(frame.shape) == 3 and frame.shape[2] == 3:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    q_img = QImage(
                        frame_rgb.data,
                        w,
                        h,
                        frame_rgb.strides[0],
                        QImage.Format.Format_RGB888,
                    )
                else:
                    logger.error(
                        f"Cannot process frame for {self.camera.camera_name}: Unknown format (shape: {frame.shape})"
                    )
                    self.set_frame_pixmap(None)
                    return
            elif is_mono:
                processed_mono_frame = None
                if frame.ndim == 2:
                    processed_mono_frame = frame
                elif frame.ndim == 3 and frame.shape[2] == 1:
                    processed_mono_frame = frame.reshape(h, w)
                if processed_mono_frame is not None:
                    if not processed_mono_frame.flags["C_CONTIGUOUS"]:
                        processed_mono_frame = np.ascontiguousarray(
                            processed_mono_frame
                        )
                    q_img = QImage(
                        processed_mono_frame.data,
                        w,
                        h,
                        processed_mono_frame.strides[0],
                        QImage.Format.Format_Grayscale8,
                    )
                else:
                    logger.error(
                        f"Could not process mono frame for {self.camera.camera_name}, shape {frame.shape} not handled."
                    )
                    self.set_frame_pixmap(None)
                    return
            else:
                if frame.ndim == 3 and frame.shape[2] == 3:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    q_img = QImage(
                        frame_rgb.data,
                        w,
                        h,
                        frame_rgb.strides[0],
                        QImage.Format.Format_RGB888,
                    )
                else:
                    logger.error(
                        f"Expected 3D array for Color camera {self.camera.camera_name}, got shape {frame.shape}"
                    )
                    self.set_frame_pixmap(None)
                    return

            if q_img is not None:
                self.video_label.setStyleSheet("background-color: transparent;")
                pixmap = QPixmap.fromImage(q_img.copy())
                is_first_valid_pixmap_for_ar_setting = False
                if hasattr(self.video_label, "setAspectRatio"):
                    current_label_ar = getattr(self.video_label, "_aspect_ratio", None)
                    if current_label_ar is None and not pixmap.isNull():
                        is_first_valid_pixmap_for_ar_setting = True
                if is_first_valid_pixmap_for_ar_setting:
                    logger.debug(
                        f"Panel {self._panel_title}: Setting aspect ratio on AspectLockedLabel from pixmap W:{pixmap.width()} H:{pixmap.height()}"
                    )
                    self.video_label.setAspectRatio(pixmap.width(), pixmap.height())
                self.set_frame_pixmap(pixmap)
            else:
                self.set_frame_pixmap(None)
        except cv2.error as cv_err:
            logger.error(
                f"Panel {self._panel_title}: OpenCV error processing frame: {cv_err}"
            )
            self.set_frame_pixmap(None)
        except Exception as e:
            logger.exception(
                f"Panel {self._panel_title}: Error processing frame data: {e}"
            )
            self.set_frame_pixmap(None)

    def showEvent(self, event: QtGui.QShowEvent):
        super().showEvent(event)
        logger.debug(
            f"CameraPanel for {self.camera.camera_name} shown, starting watchdog."
        )
        self.watchdog_timer.start()

    def hideEvent(self, event: QtGui.QHideEvent):
        super().hideEvent(event)
        logger.debug(
            f"CameraPanel for {self.camera.camera_name} hidden, stopping watchdog."
        )
        self.watchdog_timer.stop()

    @Slot(QPixmap)
    def set_frame_pixmap(self, pixmap: Optional[QPixmap]):
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
        if now - self._last_resize_time > 0.1:
            self._display_size_cache = self.video_label.size()
            self._resize_timer.start(50)
            self._last_resize_time = now
        else:
            self._resize_timer.start(50)

    def _delayed_display_update(self):
        if self._latest_pixmap and not self._latest_pixmap.isNull():
            target_size = self.video_label.size()
            if (
                target_size.isEmpty()
                or target_size.width() <= 0
                or target_size.height() <= 0
            ):
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
            self.video_label.setText("Waiting for Camera...")
            self.video_label.setStyleSheet("background-color: black; color: grey;")
            self.video_label.setPixmap(QPixmap())

    def closeEvent(self, event: QtGui.QCloseEvent):
        logger.debug(f"Closing CameraPanel for {self.camera.camera_name}")
        self.watchdog_timer.stop()
        self._resize_timer.stop()
        super().closeEvent(event)
