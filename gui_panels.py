import logging
import math
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import cv2

# import matplotlib  # Matplotlib for PlotWidget - REMOVED
import numpy as np

# matplotlib.use("QtAgg")  # Use the QtAgg backend - REMOVED
# PyQtGraph for HistogramWidget AND NOW PlotWidget
import pyqtgraph as pg
import scipy.io as sio  # For .mat saving

# from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas # REMOVED
# from matplotlib.figure import Figure # REMOVED
from PySide6 import QtCore, QtGui, QtWidgets

# PySide6 Components
from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget  # For parent type hint

# --- MATLAB Engine import ---
# Try importing, but allow the rest of the module to load if it fails.
# Saving .fig will fail later if this import doesn't work.
try:
    import matlab.engine

    MATLAB_ENGINE_AVAILABLE = True
except ImportError:
    logging.getLogger("LabApp.gui_panels").warning(
        "MATLAB Engine for Python not found. Saving to .fig format will be disabled."
    )
    MATLAB_ENGINE_AVAILABLE = False
except Exception as e:  # Catch other potential import errors (e.g., path issues)
    logging.getLogger("LabApp.gui_panels").error(
        f"Error importing MATLAB Engine: {e}. Saving to .fig format will be disabled."
    )
    MATLAB_ENGINE_AVAILABLE = False


# Local modules and styles
from camera import VimbaCam
from styles import CAMERA_PANEL_STYLE

# Configure a dedicated logger for this module.
logger = logging.getLogger("LabApp.gui_panels")


class AspectLockedLabel(QLabel):
    """
    A QLabel that attempts to maintain a specific aspect ratio.
    The aspect ratio is derived from the pixmap it's intended to display.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._aspect_ratio: Optional[float] = None
        # Important: set to Expanding so it can take space, but heightForWidth will constrain it.
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setScaledContents(False)  # We handle scaling manually in CameraPanel

    def setAspectRatio(self, width: int, height: int):
        """Sets the target aspect ratio for the label."""
        if height > 0:
            new_ar = width / height
            if self._aspect_ratio is None or abs(new_ar - self._aspect_ratio) > 1e-6:
                self._aspect_ratio = new_ar
                self.updateGeometry()  # Crucial: tell layout system size hint might change
        else:
            self._aspect_ratio = None  # Invalid aspect ratio

    def hasHeightForWidth(self) -> bool:
        """Returns True if the label has a defined aspect ratio."""
        return self._aspect_ratio is not None

    def heightForWidth(self, width: int) -> int:
        """Calculates the ideal height for a given width to maintain the aspect ratio."""
        if self._aspect_ratio is not None and self._aspect_ratio > 1e-6:
            calculated_height = int(width / self._aspect_ratio)
            return calculated_height
        return super().heightForWidth(width)  # Fallback to default QLabel behavior

    def sizeHint(self) -> QSize:
        """Provides a size hint that respects the aspect ratio."""
        # The base sizeHint might be too small or not aspect-ratio aware.
        # We rely on the layout providing a width, and heightForWidth will adjust.
        # However, a good sizeHint can help the initial layout.
        w = self.width()  # Current width given by layout (can be small initially)
        if self.hasHeightForWidth():
            # If we have an aspect ratio, hint based on current width
            # If width is very small (e.g. before layout), provide a sensible minimum.
            min_sensible_width = 100  # Arbitrary minimum
            current_hint_width = max(w, min_sensible_width)
            sh = QSize(current_hint_width, self.heightForWidth(current_hint_width))
            return sh
        else:
            sh = super().sizeHint()
            return sh


# =============================================================================
# Worker to offload blocking auto operations
# =============================================================================
class AutoOpWorker(QRunnable):
    """
    A worker runnable to perform blocking camera auto operations (Exposure/Gain).
    Calls the corresponding method on the VimbaCam instance and posts results/errors
    back to the CameraPanel via its handler methods.
    """

    def __init__(self, camera: VimbaCam, op_type: str, panel_callback: "CameraPanel"):
        super().__init__()
        if op_type not in ["auto_exposure", "auto_gain"]:
            raise ValueError(f"Unknown auto operation type: {op_type}")
        self.camera = camera
        self.op_type = op_type
        self.panel_callback = panel_callback

    def run(self):
        """Executes the requested auto operation."""
        try:
            result_value: Optional[float] = None
            success = False  # Overall success of the auto operation
            camera_method_success = False  # Success of just setting the mode

            if self.op_type == "auto_exposure":
                logger.debug(
                    f"Worker: Calling camera.set_auto_exposure_once() for {self.camera.camera_name}"
                )
                camera_method_success = self.camera.set_auto_exposure_once()
                if camera_method_success:
                    logger.info(
                        f"Worker: {self.camera.camera_name} - ExposureAuto 'Once' mode set. Waiting for adjustment..."
                    )
                    # Wait for the camera to adjust. This duration might need tuning.
                    time.sleep(
                        1.5
                    )  # Example: 1.5 seconds. Adjust based on camera behavior.
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
                    time.sleep(1.5)  # Example: 1.5 seconds.
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

            # After auto operation, ensure the respective auto mode is turned OFF
            # if it was successfully turned on (or to "Once").
            # This step is crucial if "Once" leaves the camera in an auto state that isn't "Off".
            # However, for a true "Once", it should ideally revert to "Off" automatically.
            # Let's assume for now "Once" reverts automatically or doesn't need explicit "Off".
            # If issues persist (e.g., settings keep changing), we add explicit "Off" here.

            if success and result_value is not None:
                QMetaObject.invokeMethod(
                    self.panel_callback,
                    "handle_auto_result",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, self.op_type),
                    Q_ARG(float, result_value),
                )
            # elif not success (already handled by the RuntimeError below if camera_method_success was false)
            elif not camera_method_success:  # If setting the mode itself failed
                raise RuntimeError(
                    f"Camera method for {self.op_type} reported failure to set 'Once' mode."
                )
            elif (
                result_value is None and camera_method_success
            ):  # Mode set, but couldn't get value
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
# Camera Panel
# =============================================================================
class CameraPanel(QFrame):
    """
    Displays a live camera feed with collapsible controls for adjusting exposure,
    gain (TODO), and gamma. Provides visual feedback for auto operations.
    Includes FPS display overlay.
    """

    def __init__(
        self,
        camera: VimbaCam,
        title: str,
        config: Dict[str, Any],
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
        self.controls_visible: bool = False  # Default to hidden

        # FPS display related
        self._current_fps: float = 0.0
        self._show_fps: bool = True
        self._fps_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        self._fps_color = QColor("lime")

        # --- UI Setup ---
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

        self.controls_container = QWidget()
        self.controls_layout = QVBoxLayout(self.controls_container)
        self.controls_layout.setContentsMargins(0, 0, 0, 0)
        self.controls_layout.setSpacing(4)

        controls_grid = QGridLayout()
        controls_grid.setVerticalSpacing(5)
        controls_grid.setHorizontalSpacing(8)

        # Gamma controls
        gamma_label = QLabel("Gamma:")
        gamma_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self.gamma_slider.setRange(10, 400)
        self.gamma_edit = QLineEdit()
        self.gamma_edit.setFixedWidth(50)
        self.gamma_edit.setValidator(QtGui.QDoubleValidator(0.1, 4.0, 2))
        initial_gamma = self.camera.get_setting("gamma") or 1.0
        self.gamma_slider.setValue(int(initial_gamma * 100))
        self.gamma_edit.setText(f"{initial_gamma:.2f}")
        controls_grid.addWidget(gamma_label, 0, 0)
        controls_grid.addWidget(self.gamma_slider, 0, 1)
        controls_grid.addWidget(self.gamma_edit, 0, 2)

        # Exposure controls
        exposure_label = QLabel("Exposure (µs):")
        exposure_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._exposure_min_us = self.camera.get_feature_min_max("ExposureTimeAbs", 12.0)
        self._exposure_max_us = self.camera.get_feature_min_max(
            "ExposureTimeAbs", 8.45e7, is_max=True
        )
        self.exposure_slider = QSlider(Qt.Orientation.Horizontal)
        self.exposure_slider.setRange(0, 1000)
        self.exposure_edit = QLineEdit()
        self.exposure_edit.setFixedWidth(70)
        self.exposure_edit.setValidator(
            QtGui.QDoubleValidator(self._exposure_min_us, self._exposure_max_us, 2)
        )
        initial_exposure_us = self.camera.get_setting("exposure_us") or 10000.0
        self.exposure_slider.setValue(self._exposure_to_slider(initial_exposure_us))
        self.exposure_edit.setText(f"{initial_exposure_us:.0f}")
        controls_grid.addWidget(exposure_label, 1, 0)
        controls_grid.addWidget(self.exposure_slider, 1, 1)
        controls_grid.addWidget(self.exposure_edit, 1, 2)

        # Auto Buttons
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
        self.controls_layout.addLayout(controls_grid)
        self.main_layout.addWidget(self.controls_container)

        # Video display label
        self.video_label = AspectLockedLabel(self)
        self.video_label.setText("Initializing Camera...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: transparent; color: grey;")
        self.main_layout.addWidget(self.video_label, stretch=1)

        self.controls_container.setVisible(
            self.controls_visible
        )  # Set initial visibility
        self._connect_control_signals()
        self.clear_status_indicators()

    def _connect_control_signals(self):
        """Connect signals for sliders, line edits, and buttons."""
        self.exposure_btn.clicked.connect(lambda: self._start_auto_op("auto_exposure"))
        self.gain_btn.clicked.connect(lambda: self._start_auto_op("auto_gain"))
        self.gamma_slider.valueChanged.connect(self._handle_gamma_slider)
        self.gamma_edit.editingFinished.connect(self._handle_gamma_edit)
        self.exposure_slider.valueChanged.connect(self._handle_exposure_slider)
        self.exposure_edit.editingFinished.connect(self._handle_exposure_edit)

    def set_controls_visibility(self, visible: bool):
        """Sets the visibility of the camera controls container."""
        if self.controls_visible == visible:
            return
        self.controls_visible = visible
        self.controls_container.setVisible(self.controls_visible)
        logger.debug(
            f"CameraPanel '{self._panel_title}' controls set to visible: {self.controls_visible}"
        )

    def get_controls_visible(self) -> bool:
        """Returns the current visibility state of the controls."""
        return self.controls_visible

    def _exposure_to_slider(self, exposure_us: float) -> int:
        """Converts exposure time (µs) to a slider position (0-1000) using logarithmic scaling."""
        min_exp = self._exposure_min_us
        max_exp = self._exposure_max_us
        if exposure_us <= min_exp:
            return 0
        if exposure_us >= max_exp:
            return 1000
        if (
            min_exp <= 0 or max_exp <= 0 or exposure_us <= 0
        ):  # Ensure log inputs are positive
            logger.warning(
                f"Invalid exposure range or value for log scaling: min_exp={min_exp}, max_exp={max_exp}, exposure_us={exposure_us}"
            )
            # Attempt a linear mapping or return a boundary value if log scale fails
            if max_exp > min_exp:  # Basic linear scaling as fallback
                return int(1000 * (exposure_us - min_exp) / (max_exp - min_exp))
            return 0  # Default to min if range is invalid

        try:
            log_min = math.log10(min_exp)
            log_max = math.log10(max_exp)
            log_val = math.log10(exposure_us)
            if log_max == log_min:  # Avoid division by zero if min_exp == max_exp
                return 0 if exposure_us <= min_exp else 1000
            return int(1000 * (log_val - log_min) / (log_max - log_min))
        except ValueError:
            logger.warning(
                f"Math error converting exposure {exposure_us} to slider (min: {min_exp}, max: {max_exp})."
            )
            if exposure_us <= min_exp:
                return 0
            if exposure_us >= max_exp:
                return 1000
            # Fallback linear logic if logs fail for unexpected reasons
            if max_exp > min_exp:
                return int(1000 * (exposure_us - min_exp) / (max_exp - min_exp))
            return 0

    def _slider_to_exposure(self, slider_val: int) -> float:
        """Converts slider position (0-1000) to exposure time (µs) using logarithmic scaling."""
        min_exp = self._exposure_min_us
        max_exp = self._exposure_max_us
        if slider_val <= 0:
            return min_exp
        if slider_val >= 1000:
            return max_exp
        if min_exp <= 0 or max_exp <= 0:  # Ensure log inputs are positive
            logger.warning(
                f"Invalid exposure range for log scaling: min_exp={min_exp}, max_exp={max_exp}"
            )
            # Fallback to linear or return boundary
            if max_exp > min_exp:  # Basic linear scaling
                return min_exp + (slider_val / 1000.0) * (max_exp - min_exp)
            return min_exp  # Default to min_exp

        try:
            log_min = math.log10(min_exp)
            log_max = math.log10(max_exp)
            if log_max == log_min:  # Avoid issues if min_exp == max_exp after log
                return min_exp
            exposure = 10 ** ((slider_val / 1000.0) * (log_max - log_min) + log_min)
            return exposure
        except ValueError:
            logger.warning(
                f"Math error converting slider {slider_val} to exposure (min: {min_exp}, max: {max_exp})."
            )
            # Fallback linear logic
            if max_exp > min_exp:
                return min_exp + (slider_val / 1000.0) * (max_exp - min_exp)
            return min_exp

    def _handle_gamma_slider(self, value: int):
        gamma = value / 100.0
        self.gamma_edit.blockSignals(True)
        self.gamma_edit.setText(f"{gamma:.2f}")
        self.gamma_edit.blockSignals(False)
        self._update_camera_gamma(gamma)

    def _handle_gamma_edit(self):
        try:
            gamma = float(self.gamma_edit.text())
            gamma = max(0.1, min(4.0, gamma))
            self.gamma_edit.setText(f"{gamma:.2f}")
            self.gamma_slider.blockSignals(True)
            self.gamma_slider.setValue(int(gamma * 100))
            self.gamma_slider.blockSignals(False)
            self._update_camera_gamma(gamma)
        except ValueError:
            logger.warning("Invalid gamma value entered.")
            self._revert_gamma_ui()
            self._visual_feedback(self.gamma_edit, success=False)

    def _update_camera_gamma(self, gamma: float):
        success = self.camera.set_gamma(gamma)
        self._visual_feedback(self.gamma_edit, success=success)
        if not success:
            QTimer.singleShot(100, self._revert_gamma_ui)

    def _revert_gamma_ui(self):
        current_gamma = self.camera.get_setting("gamma") or 1.0
        logger.info(f"Reverting gamma UI to camera value: {current_gamma:.2f}")
        self.gamma_edit.blockSignals(True)
        self.gamma_slider.blockSignals(True)
        self.gamma_edit.setText(f"{current_gamma:.2f}")
        self.gamma_slider.setValue(int(current_gamma * 100))
        self.gamma_edit.blockSignals(False)
        self.gamma_slider.blockSignals(False)

    def _handle_exposure_slider(self, value: int):
        exposure_us = self._slider_to_exposure(value)
        self.exposure_edit.blockSignals(True)
        self.exposure_edit.setText(f"{exposure_us:.0f}")
        self.exposure_edit.blockSignals(False)
        self._update_camera_exposure(exposure_us)

    def _handle_exposure_edit(self):
        try:
            exposure_us = float(self.exposure_edit.text())
            exposure_us = max(
                self._exposure_min_us, min(self._exposure_max_us, exposure_us)
            )
            self.exposure_edit.setText(f"{exposure_us:.0f}")
            self.exposure_slider.blockSignals(True)
            self.exposure_slider.setValue(self._exposure_to_slider(exposure_us))
            self.exposure_slider.blockSignals(False)
            self._update_camera_exposure(exposure_us)
        except ValueError:
            logger.warning("Invalid exposure value entered.")
            self._revert_exposure_ui()
            self._visual_feedback(self.exposure_edit, success=False)

    def _update_camera_exposure(self, exposure_us: float):
        success = self.camera.set_exposure(exposure_us)
        self._visual_feedback(self.exposure_edit, success=success)
        if not success:
            QTimer.singleShot(100, self._revert_exposure_ui)

    def _revert_exposure_ui(self):
        current_exposure = self.camera.get_setting("exposure_us") or 10000.0
        logger.info(f"Reverting exposure UI to camera value: {current_exposure:.0f} µs")
        self.exposure_edit.blockSignals(True)
        self.exposure_slider.blockSignals(True)
        self.exposure_edit.setText(f"{current_exposure:.0f}")
        self.exposure_slider.setValue(self._exposure_to_slider(current_exposure))
        self.exposure_edit.blockSignals(False)
        self.exposure_slider.blockSignals(False)

    def _visual_feedback(
        self, widget: QWidget, success: bool = True, duration_ms: int = 400
    ):
        original_style = widget.styleSheet()
        color = "#e0ffe0" if success else "#ffe0e0"
        widget.setStyleSheet(f"background-color: {color};")
        QTimer.singleShot(duration_ms, lambda: widget.setStyleSheet(original_style))

    def _start_auto_op(self, op_type: str):
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
            self.exposure_edit.blockSignals(True)
            self.exposure_slider.blockSignals(True)
            self.exposure_edit.setText(f"{result_value:.0f}")
            self.exposure_slider.setValue(self._exposure_to_slider(result_value))
            self.exposure_edit.blockSignals(False)
            self.exposure_slider.blockSignals(False)
            self.exposure_status.setText("✓")
            self.exposure_status.setStyleSheet("color: green; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.clear_status_indicators("exposure"))
            self.exposure_btn.setEnabled(True)
        elif op_type == "auto_gain":
            self.gain_status.setText("✓")
            self.gain_status.setStyleSheet("color: green; font-weight: bold;")
            QTimer.singleShot(2500, lambda: self.clear_status_indicators("gain"))
            self.gain_btn.setEnabled(True)
            # TODO: Update Gain UI controls when added

    @Slot(str, str)
    def handle_auto_error(self, op_type: str, error_str: str):
        logger.error(f"Auto operation '{op_type}' failed: {error_str}")
        if op_type == "auto_exposure":
            self.exposure_status.setText("✗")
            self.exposure_status.setStyleSheet("color: red; font-weight: bold;")
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("exposure"))
            self.exposure_btn.setEnabled(True)
            self._revert_exposure_ui()
        elif op_type == "auto_gain":
            self.gain_status.setText("✗")
            self.gain_status.setStyleSheet("color: red; font-weight: bold;")
            QTimer.singleShot(3500, lambda: self.clear_status_indicators("gain"))
            self.gain_btn.setEnabled(True)
            # TODO: Revert Gain UI if added

    def clear_status_indicators(self, control: Optional[str] = None):
        if control is None or control == "exposure":
            self.exposure_status.setText("")
        if control is None or control == "gain":
            self.gain_status.setText("")

    @Slot(object)  # Accepts a numpy array object
    def process_new_frame_data(self, frame: Optional[np.ndarray]):
        """
        Slot to receive raw frame data (numpy array) from VimbaCam signal.
        Converts frame to QPixmap and updates the display.
        Handles Mono8 format (possibly HxWx1) directly.
        """
        if frame is None:
            self.set_frame_pixmap(None)
            return

        try:
            is_mono = self.camera.is_mono
            h, w = frame.shape[:2]

            q_img: Optional[QImage] = None  # Initialize q_img

            if is_mono is None:
                # ... (existing fallback logic for unknown mono status) ...
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
                # --- Direct Grayscale Handling ---
                processed_mono_frame = None
                if frame.ndim == 2:  # Already 2D
                    processed_mono_frame = frame
                elif frame.ndim == 3 and frame.shape[2] == 1:  # Shape is (H, W, 1)
                    # logger.debug(
                    #     f"Reshaping mono frame from {frame.shape} to 2D for {self.camera.camera_name}"
                    # )
                    processed_mono_frame = frame.reshape(h, w)  # Or frame.squeeze()

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
                    # This else will be hit if neither frame.ndim == 2 NOR (frame.ndim == 3 and frame.shape[2] == 1) is true
                    logger.error(
                        f"Could not process mono frame for {self.camera.camera_name}, shape {frame.shape} not handled."
                    )
                    self.set_frame_pixmap(None)
                    return

            else:  # Color camera
                # ... (existing color camera logic) ...
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
                pixmap = QPixmap.fromImage(q_img.copy())

                is_first_valid_pixmap_for_ar_setting = False
                if hasattr(self.video_label, "setAspectRatio"):
                    current_label_ar = getattr(self.video_label, "_aspect_ratio", None)
                    if current_label_ar is None and not pixmap.isNull():
                        is_first_valid_pixmap_for_ar_setting = True

                if is_first_valid_pixmap_for_ar_setting:
                    logger.debug(
                        f"Panel {self._panel_title}: Setting aspect ratio on AspectLockedLabel "
                        f"from pixmap W:{pixmap.width()} H:{pixmap.height()}"
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

    @Slot(QPixmap)
    def set_frame_pixmap(self, pixmap: Optional[QPixmap]):
        """Stores the latest displayable QPixmap and triggers throttled update."""
        is_null_or_none = pixmap is None or pixmap.isNull()
        if not is_null_or_none:
            self._latest_pixmap = pixmap
            self._delayed_display_update()
        else:
            self._latest_pixmap = None
            self._delayed_display_update()

    @Slot(float)
    def update_fps(self, fps: float):
        """Stores the latest FPS value."""
        self._current_fps = fps
        # Display update will draw the stored FPS value

    def resizeEvent(self, event: QtGui.QResizeEvent):
        """Handle resize events, throttling updates to the video label."""
        super().resizeEvent(event)
        now = time.monotonic()
        if now - self._last_resize_time > 0.1:  # Basic debounce
            self._display_size_cache = self.video_label.size()  # Cache current size
            self._resize_timer.start(50)  # Schedule update after a short delay
            self._last_resize_time = now
        else:  # If many events come in quick succession, just restart the timer
            self._resize_timer.start(50)

    def _delayed_display_update(self):
        """Update the video display label content (scaling, FPS overlay)."""
        if self._latest_pixmap and not self._latest_pixmap.isNull():
            target_size = self.video_label.size()
            if (
                target_size.isEmpty()
                or target_size.width() <= 0
                or target_size.height() <= 0
            ):
                # logger.warning(
                #     f"Panel {self._panel_title}: Cannot update display, target size invalid: {target_size}."
                # )
                # Try to use cached size if current is invalid, common during rapid layout changes
                if self._display_size_cache and not self._display_size_cache.isEmpty():
                    target_size = self._display_size_cache
                else:  # Still no valid size, skip update
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
            self.video_label.setPixmap(QPixmap())  # Clear any old pixmap

    def closeEvent(self, event: QtGui.QCloseEvent):
        logger.debug(f"Closing CameraPanel for {self.camera.camera_name}")
        self._resize_timer.stop()
        super().closeEvent(event)


# =============================================================================
# Histogram Widget (using PyQtGraph)
# =============================================================================
class HistogramWidget(QtWidgets.QWidget):
    """
    Displays real-time power monitoring data as a histogram using PyQtGraph.
    Updates are throttled for smooth performance. Expects power data as a dictionary.
    """

    def __init__(
        self, control_panel, detector_keys: List[str], parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        if not detector_keys:
            logger.warning("HistogramWidget initialized with no detector keys.")
        logger.info(f"Initializing HistogramWidget for detectors: {detector_keys}")

        # Store control_panel if needed for future interactions, though not used in current example
        # self.control_panel = control_panel

        self.detector_keys = detector_keys
        self.num_bars = len(self.detector_keys)

        # Data storage
        self.current_values = np.zeros(self.num_bars)
        self.max_values = np.full(self.num_bars, -np.inf)  # Initialize max to -infinity

        # Plot configuration (colors, fonts, etc.)
        self.bar_width = 0.6
        self.font_size = 12  # Base font size for labels
        self.title_size = 14  # Title font size
        self.value_text_font_size = 15  # Specific size for value annotations on bars
        self.text_offset = 1.0  # Offset for text from the value line

        self.max_pen = pg.mkPen("#e41a1c", width=1.5, style=QtCore.Qt.PenStyle.DashLine)
        self.bar_brush = pg.mkBrush("#a6cee3")
        self.bar_pen = pg.mkPen("#1f78b4")
        self.max_text_color = pg.mkColor("#e41a1c")
        self.current_text_color = pg.mkColor("#555555")  # Dark grey for current values
        self.text_font = QFont(
            "Segoe UI", self.value_text_font_size
        )  # Font for value annotations

        # UI Elements
        self.layout = QtWidgets.QVBoxLayout(self)
        self.plot_widget = pg.PlotWidget(background="w")
        self.layout.addWidget(self.plot_widget)

        # Plot items
        self.bars: Optional[pg.BarGraphItem] = None
        self.max_lines: List[pg.PlotCurveItem] = []
        # Initialize text item lists (filled in _create_plot_items)
        self.max_texts: List[Optional[pg.TextItem]] = []
        self.current_texts: List[Optional[pg.TextItem]] = []

        self._configure_plot()  # Sets up axes, title, grid
        self._create_plot_items()  # Creates bars, lines, and text items

        self.reset_btn = QtWidgets.QPushButton("Reset Maxima")
        self.reset_btn.clicked.connect(self.reset_maxima)
        self.layout.addWidget(self.reset_btn)

        # Throttling for updates
        self._pending_power_data: Optional[Dict] = None
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(50)  # Update interval for processing data
        self._update_timer.timeout.connect(self._process_pending_update)
        self._is_visible = False  # To control timer activity

        # Pre-calculate bar x-positions for max lines
        self._bar_positions = [
            (i - self.bar_width / 2, i + self.bar_width / 2)
            for i in range(self.num_bars)
        ]

        logger.info("HistogramWidget initialized successfully.")

    def _configure_plot(self):
        label_style = {"color": "k", "font-size": f"{self.font_size}pt"}
        title_style = {"color": "k", "size": f"{self.title_size}pt"}

        x_axis = self.plot_widget.getAxis("bottom")
        x_axis.setLabel(text="Detector", **label_style)
        x_axis.setTickFont(
            QFont("Segoe UI", self.font_size - 1)
        )  # Slightly smaller ticks
        ticks = [[(i, key) for i, key in enumerate(self.detector_keys)]]
        x_axis.setTicks(ticks)

        y_axis = self.plot_widget.getAxis("left")
        y_axis.setLabel(text="Power (dBm)", **label_style)
        y_axis.setTickFont(QFont("Segoe UI", self.font_size - 1))
        y_axis.enableAutoSIPrefix(False)  # Show raw numbers for dBm

        self.plot_widget.setTitle("Real-time Power Monitoring", **title_style)
        self.plot_widget.showGrid(y=True, alpha=0.3)  # Show horizontal grid lines
        self.plot_widget.setYRange(-70, 10)  # Initial Y range
        self.plot_widget.setXRange(-0.5, self.num_bars - 0.5, padding=0)

    def _create_plot_items(self):
        # Create BarGraphItem
        self.bars = pg.BarGraphItem(
            x=np.arange(self.num_bars),
            height=self.current_values,  # Initialized to zeros
            width=self.bar_width,
            brush=self.bar_brush,
            pen=self.bar_pen,
        )
        self.plot_widget.addItem(self.bars)

        # Create PlotCurveItems for max lines and TextItems for annotations
        self.max_lines = []
        self.max_texts = []
        self.current_texts = []

        for i in range(self.num_bars):
            # Max lines
            line = pg.PlotCurveItem(pen=self.max_pen)
            self.plot_widget.addItem(line)
            self.max_lines.append(line)

            # Max texts (initially invisible)
            max_text = pg.TextItem(text="", color=self.max_text_color)
            max_text.setFont(self.text_font)
            max_text.setVisible(False)
            self.plot_widget.addItem(max_text)
            self.max_texts.append(max_text)

            # Current texts (initially invisible)
            curr_text = pg.TextItem(text="", color=self.current_text_color)
            curr_text.setFont(self.text_font)
            curr_text.setVisible(False)
            self.plot_widget.addItem(curr_text)
            self.current_texts.append(curr_text)

    def showEvent(self, event: QtGui.QShowEvent):
        super().showEvent(event)
        self._is_visible = True
        if not self._update_timer.isActive():
            logger.debug("HistogramWidget visible, starting update timer.")
            self._update_timer.start()

    def hideEvent(self, event: QtGui.QHideEvent):
        super().hideEvent(event)
        self._is_visible = False
        if self._update_timer.isActive():
            logger.debug("HistogramWidget hidden, stopping update timer.")
            self._update_timer.stop()

    @Slot()
    def reset_maxima(self):
        t_start = time.perf_counter()
        logger.info(
            "Resetting histogram: current values to 0, max_values to -infinity."
        )

        self.current_values.fill(0.0)
        self.max_values.fill(-np.inf)

        if self.bars:
            self.bars.setOpts(height=self.current_values)
        else:
            logger.warning("Reset Maxima: self.bars is None, cannot set heights.")

        for i in range(self.num_bars):
            x_center = i
            current_val_at_reset = 0.0

            if i < len(self.current_texts) and self.current_texts[i] is not None:
                text_item_current = self.current_texts[i]
                show_text_at_zero = (
                    np.isfinite(current_val_at_reset) and current_val_at_reset > -90
                )

                if show_text_at_zero:
                    text_item_current.setText(f"{current_val_at_reset:.2f}")
                    # Use the new logic for current text (to be UNDER)
                    text_item_current.setAnchor((0.5, 0.0))  # Anchor: Bottom-center
                    text_y_position = (
                        current_val_at_reset + self.text_offset
                    )  # Position bottom of text slightly above value
                    text_item_current.setPos(x_center, text_y_position)
                    text_item_current.setVisible(True)
                else:
                    text_item_current.setVisible(False)

            if i < len(self.max_texts) and self.max_texts[i] is not None:
                self.max_texts[i].setVisible(False)

            if i < len(self.max_lines) and self.max_lines[i] is not None:
                self.max_lines[i].clear()

        self._update_y_axis_scale()
        t_end = time.perf_counter()
        logger.debug(f"Reset Maxima execution took: {(t_end - t_start) * 1000:.3f} ms")

    @Slot(dict)
    def schedule_update(self, power_data: dict):
        if self._is_visible:
            self._pending_power_data = power_data

    @Slot()
    def _process_pending_update(self):
        if self._pending_power_data is None:
            return

        data_to_process = self._pending_power_data
        self._pending_power_data = None

        if not isinstance(data_to_process, dict):
            logger.warning(
                f"HistogramWidget: Invalid power data type: {type(data_to_process)}"
            )
            return

        try:
            low_signal_floor = -100.0
            detector_values_from_signal = data_to_process.get("detectors", {})

            new_values_from_signal = np.array(
                [
                    detector_values_from_signal.get(key, -np.inf)
                    for key in self.detector_keys
                ],
                dtype=float,
            )

            new_values_processed = np.nan_to_num(
                new_values_from_signal,
                nan=low_signal_floor,
                posinf=10.0,
                neginf=low_signal_floor,
            )

            self._update_values(new_values_processed)
            self._update_visual_elements()
            self._update_y_axis_scale()

        except Exception as e:
            logger.exception(f"HistogramWidget: Error processing histogram update: {e}")

    def _update_values(self, new_values_from_processing: np.ndarray):
        self.current_values = np.array(new_values_from_processing, copy=True)
        valid_to_update_max_mask = np.isfinite(self.current_values)
        if np.any(valid_to_update_max_mask):
            self.max_values[valid_to_update_max_mask] = np.maximum(
                self.max_values[valid_to_update_max_mask],
                self.current_values[valid_to_update_max_mask],
            )

    def _update_visual_elements(self):
        if not self.bars:
            logger.warning(
                "HistogramWidget: Bars not initialized in _update_visual_elements."
            )
            return
        self.bars.setOpts(height=self.current_values)
        for i in range(self.num_bars):
            current_val = self.current_values[i]
            max_val = self.max_values[i]
            x_center = i
            x_start_line, x_end_line = self._bar_positions[i]
            self._update_max_line(i, x_start_line, x_end_line, max_val)
            self._update_max_text(i, x_center, max_val)  # Max text should be OVER
            self._update_current_text(
                i, x_center, current_val
            )  # Current text should be UNDER

    def _update_max_line(self, i: int, x_start: float, x_end: float, max_val: float):
        if i < len(self.max_lines) and self.max_lines[i] is not None:
            if np.isfinite(max_val):
                self.max_lines[i].setData(x=[x_start, x_end], y=[max_val, max_val])
            else:
                self.max_lines[i].clear()
        else:
            logger.warning(f"Max line for index {i} not properly initialized.")

    def _update_max_text(self, i: int, x_center: float, max_val: float):
        # Max text: To appear OVER the line
        if i >= len(self.max_texts) or self.max_texts[i] is None:
            return
        text_item = self.max_texts[i]
        show_text = np.isfinite(max_val) and max_val > -90
        if show_text:
            text_item.setText(f"{max_val:.2f}")
            text_item.setAnchor(
                (0.5, 1.0)
            )  # Anchor: Top-center (logic that was making current appear "over")
            text_y_position = (
                max_val - self.text_offset
            )  # Position top of text slightly below max_val
            text_item.setPos(x_center, text_y_position)
            text_item.setVisible(True)
        else:
            text_item.setVisible(False)

    def _update_current_text(self, i: int, x_center: float, current_val: float):
        # Current text: To appear UNDER the line
        if i >= len(self.current_texts) or self.current_texts[i] is None:
            return
        text_item = self.current_texts[i]
        show_text = np.isfinite(current_val) and current_val > -90
        if show_text:
            text_item.setText(f"{current_val:.2f}")
            text_item.setAnchor(
                (0.5, 0.0)
            )  # Anchor: Bottom-center (logic that was making max appear "under")
            text_y_position = (
                current_val + self.text_offset
            )  # Position bottom of text slightly above current_val
            text_item.setPos(x_center, text_y_position)
            text_item.setVisible(True)
        else:
            text_item.setVisible(False)

    def _update_y_axis_scale(self):
        try:
            viewable_current = self.current_values[np.isfinite(self.current_values)]
            viewable_max = self.max_values[np.isfinite(self.max_values)]

            if viewable_current.size == 0 and viewable_max.size == 0:
                self.plot_widget.setYRange(-70, 10, padding=0)
                return

            combined_finite_vals = np.array([])
            if viewable_current.size > 0:
                combined_finite_vals = np.concatenate(
                    (combined_finite_vals, viewable_current)
                )
            if viewable_max.size > 0:
                combined_finite_vals = np.concatenate(
                    (combined_finite_vals, viewable_max)
                )

            if combined_finite_vals.size == 0:
                self.plot_widget.setYRange(-70, 10, padding=0)
                return

            y_min_data = np.min(combined_finite_vals)
            y_max_data = np.max(combined_finite_vals)

            data_range = y_max_data - y_min_data
            padding = max(2.0, data_range * 0.2) if data_range > 1e-6 else 2.0
            y_min_view = y_min_data - padding
            y_max_view = y_max_data + padding
            y_min_view = max(y_min_view, -100.0)
            y_max_view = min(y_max_view, 20.0)

            if y_max_view - y_min_view < 10.0:
                mid_point = (y_max_view + y_min_view) / 2.0
                y_min_view = mid_point - 5.0
                y_max_view = mid_point + 5.0
                y_min_view = max(y_min_view, -100.0)
                y_max_view = min(y_max_view, 20.0)

            self.plot_widget.setYRange(y_min_view, y_max_view, padding=0)

        except Exception as e:
            logger.exception(f"Error updating y-axis scale: {e}")


# =============================================================================
# Plot Widget (using PyQtGraph - MATLAB fig saving restored)
# =============================================================================
class PlotWidget(QWidget):
    """
    Displays scan results using PyQtGraph. Saves data as CSV, MAT, PNG, SVG, and FIG (if MATLAB Engine is available).
    """

    def __init__(self, shared_settings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        if not isinstance(shared_settings, ScanSettings):
            logger.warning("PlotWidget needs a valid ScanSettings object for metadata.")
            self.shared_settings = ScanSettings()  # Dummy settings
        else:
            self.shared_settings = shared_settings

        # Data Storage
        self.current_wavelengths: Optional[np.ndarray] = None
        self.current_powers: Optional[np.ndarray] = None
        self.current_output_power: Optional[float] = None

        # --- UI Setup ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.plot_widget = pg.PlotWidget(background="w")  # PyQtGraph PlotWidget
        self.plot_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.plot_widget)

        # PlotDataItem for the main scan data
        self.plot_data_item = self.plot_widget.plot(
            pen=pg.mkPen(color="#1f78b4", width=1.0),
            # symbol="o",
            # symbolPen=None,  # No outline for symbol
            # symbolBrush=pg.mkBrush("#1f78b4"),
            # symbolSize=4,  # Adjust size as needed
        )

        # Configure axes, title, grid
        tick_font = QFont("Segoe UI", 11)
        label_style = {"color": "black", "font-size": "12pt"}

        self.plot_widget.setLabel("left", "Power (dBm)", **label_style)
        self.plot_widget.getAxis("left").setTickFont(tick_font)
        self.plot_widget.setLabel("bottom", "Wavelength (nm)", **label_style)
        self.plot_widget.getAxis("bottom").setTickFont(tick_font)
        self.plot_widget.setTitle("Wavelength Scan", color="black", size="11pt")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        # PyQtGraph auto-ranges by default, which is often sufficient.

        self.save_btn = QPushButton("Save Scan Data")
        self.save_btn.setIcon(QtGui.QIcon(":/icons/save.svg"))
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_scan_data)
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        button_layout.addWidget(self.save_btn)
        layout.addLayout(button_layout)

        logger.info("PlotWidget (PyQtGraph) initialized")

    @Slot(object, object, object)
    def update_plot(
        self, x_data: Any, y_data: Any, output_power: Optional[float] = None
    ):
        try:
            x_data_np = np.asarray(x_data, dtype=float)
            y_data_np = np.asarray(y_data, dtype=float)
            if (
                x_data_np.ndim != 1
                or y_data_np.ndim != 1
                or len(x_data_np) != len(y_data_np)
            ):
                logger.error(
                    f"Invalid data shape for plotting. X: {x_data_np.shape}, Y: {y_data_np.shape}"
                )
                self.plot_data_item.setData([], [])
                self.plot_widget.setTitle("Invalid Scan Data", color="red", size="11pt")
                self.save_btn.setEnabled(False)
                return

            logger.debug(
                f"Updating plot. Points: {len(x_data_np)}. Pout: {output_power}"
            )
            self.current_wavelengths = x_data_np
            self.current_powers = y_data_np
            self.current_output_power = output_power
            self.plot_data_item.setData(x_data_np, y_data_np)

            if len(x_data_np) > 0:
                title = f"Wavelength Scan ({x_data_np[0]:.1f} - {x_data_np[-1]:.1f} nm)"
                self.plot_widget.setTitle(title, color="black", size="11pt")
            else:
                self.plot_widget.setTitle("Wavelength Scan", color="black", size="11pt")

            self.save_btn.setEnabled(True)
        except Exception as e:
            logger.error(f"Error updating plot: {e}", exc_info=True)
            self.plot_widget.setTitle("Error Updating Plot", color="red", size="11pt")
            self.save_btn.setEnabled(False)

    @Slot()
    def save_scan_data(self):
        if self.current_wavelengths is None or self.current_powers is None:
            QMessageBox.warning(self, "No Data", "No scan data available to save.")
            return
        wavelengths, powers, pout = (
            self.current_wavelengths,
            self.current_powers,
            self.current_output_power,
        )
        logger.info(f"Saving scan data. Points: {len(wavelengths)}. Pout: {pout}")

        if pout is not None:
            data_to_save = np.column_stack(
                (wavelengths, np.full_like(wavelengths, pout), powers)
            )
            column_headers = "WL_[nm], Pout_[dBm], Power_Det1_[dBm]"
        else:
            data_to_save = np.column_stack((wavelengths, powers))
            column_headers = "WL_[nm], Power_Det1_[dBm]"
        try:
            resolution = getattr(self.shared_settings, "resolution", "N/A")
            motor_speed = getattr(self.shared_settings, "motor_speed", "N/A")
            laser_power = getattr(self.shared_settings, "laser_power", "N/A")
            power_unit = getattr(self.shared_settings, "power_unit", "N/A")
            extra_comments = f"# Resolution(pm): {resolution}\n# Speed(nm/s): {motor_speed}\n# LaserPower: {laser_power} {power_unit}\n"
            if pout is not None:
                extra_comments += f"# Pout(dBm): {pout:.3f}\n"
            header_text = extra_comments + "# " + column_headers
        except Exception as e:
            logger.warning(f"Metadata error: {e}")
            header_text = "# " + column_headers

        default_filename = f"scan_{wavelengths[0]:.0f}nm_{wavelengths[-1]:.0f}nm"
        file_filters = "CSV File (*.csv);;MAT File (*.mat);;PNG Image (*.png);;SVG Image (*.svg);;FIG File (*.fig);;All Files (*)"
        selected_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Scan Results As",
            default_filename,
            file_filters,
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if not selected_path:
            logger.info("Save cancelled.")
            return
        base_filename = os.path.splitext(selected_path)[0]

        saved_files, errors = [], []
        try:
            csv_filename = f"{base_filename}.csv"
            np.savetxt(
                csv_filename,
                data_to_save,
                delimiter=",",
                header=header_text,
                comments="",
                fmt="%.6f",
            )
            saved_files.append(csv_filename)
            logger.info(f"Saved CSV: {csv_filename}")
        except Exception as e:
            errors.append(f"CSV: {e}")
            logger.error(f"CSV save failed: {e}", exc_info=True)

        try:
            mat_filename = f"{base_filename}.mat"
            mat_data = {
                "wl_nm": wavelengths,
                "pow_dBm": powers,
                "res_pm": resolution,
                "speed_nms": motor_speed,
                "lp_set": laser_power,
                "lp_unit": power_unit,
            }
            if pout is not None:
                mat_data["pout_dBm"] = pout
            sio.savemat(mat_filename, mat_data, do_compression=True)
            saved_files.append(mat_filename)
            logger.info(f"Saved MAT: {mat_filename}")
        except Exception as e:
            errors.append(f"MAT: {e}")
            logger.error(f"MAT save failed: {e}", exc_info=True)

        if MATLAB_ENGINE_AVAILABLE:
            matlab_eng = None
            try:
                fig_filename = f"{base_filename}.fig"
                logger.info("Attempting to start MATLAB engine for .fig export...")
                matlab_eng = matlab.engine.start_matlab()
                logger.info("MATLAB engine started.")
                wavelengths_mat = matlab.double(wavelengths.tolist())
                powers_mat = matlab.double(powers.tolist())
                matlab_eng.figure(nargout=0)
                matlab_eng.plot(wavelengths_mat, powers_mat, nargout=0)
                matlab_eng.xlabel("Wavelength (nm)", nargout=0)
                matlab_eng.ylabel("Power (dBm)", nargout=0)
                matlab_eng.title(
                    f"Scan {wavelengths[0]:.1f} - {wavelengths[-1]:.1f} nm", nargout=0
                )
                matlab_eng.grid("on", nargout=0)
                matlab_eng.savefig(fig_filename, nargout=0)
                saved_files.append(fig_filename)
                logger.info(f"Saved plot to FIG: {fig_filename}")
            except ImportError:
                error_msg = "FIG: MATLAB Engine not installed."
                errors.append(error_msg)
                logger.error(error_msg)
            except Exception as e:
                error_msg = f"FIG: MATLAB export failed: {e}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
            finally:
                if matlab_eng:
                    try:
                        logger.info("Quitting MATLAB engine...")
                        matlab_eng.quit()
                        logger.info("MATLAB engine quit.")
                    except Exception as e_quit:
                        logger.error(f"Error quitting MATLAB engine: {e_quit}")
        else:
            errors.append("FIG: MATLAB Engine unavailable.")
            logger.warning(
                "Skipping .fig save: MATLAB Engine for Python not available."
            )

        if not errors:
            QMessageBox.information(
                self,
                "Save Successful",
                "Scan data saved successfully to:\n" + "\n".join(saved_files),
            )
        else:
            QMessageBox.warning(
                self,
                "Save Issues",
                "Some files saved:\n"
                + "\n".join(saved_files)
                + "\n\nErrors occurred:\n"
                + "\n".join(errors),
            )


try:
    from control_panel import ScanSettings
except ImportError:
    logger.error(
        "ScanSettings class not found. Ensure it's defined or imported correctly."
    )

    class ScanSettings:
        pass
