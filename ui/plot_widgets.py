from __future__ import annotations

import io
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from weakref import ReferenceType, ref

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
import shiboken6
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app_settings import AppSettings
from hardware.ct400_types import Detector
from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState
from logic.power_monitor_display import DEFAULT_DISPLAY_POINT_LIMIT, PowerMonitorDisplayHistory
from logic.power_monitor_export import (
    build_power_monitor_export_v1,
    default_power_monitor_export_name,
    derive_power_monitor_export_targets,
)
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingSample,
)
from logic.scan_export import build_scan_export_v2
from logic.scan_import import ImportedScan
from logic.scan_measurement import ScanMeasurement
from ui.power_monitor_export_dialog import PowerMonitorExportDialog
from ui.scan_export_dialog import ScanExportDialog
from ui.typography import make_font, plot_title_font, pyqtgraph_title_style

try:
    import matlab.engine

    MATLAB_ENGINE_AVAILABLE = True
except ImportError:
    logging.getLogger("LabApp.gui_panels").warning(
        "MATLAB Engine for Python not found. Saving to .fig format will be disabled."
    )
    MATLAB_ENGINE_AVAILABLE = False
except Exception as e:  # noqa: BLE001  # Preserve optional MATLAB and export fallbacks.
    logging.getLogger("LabApp.gui_panels").error(
        f"Error importing MATLAB Engine: {e}. Saving to .fig format will be disabled."
    )
    MATLAB_ENGINE_AVAILABLE = False

logger = logging.getLogger("LabApp.plot_widgets")

_GENERIC_SCAN_Y_LABEL = "Power (dB)"
_MEASUREMENT_SCAN_Y_LABEL = "Transfer function (dB)"
MAX_OVERLAY_SCANS = 8
_OVERLAY_LINE_STYLES = (Qt.PenStyle.DashLine, Qt.PenStyle.DotLine, Qt.PenStyle.DashDotLine, Qt.PenStyle.DashDotDotLine)
_OVERLAY_CUSTOM_DASH_PATTERNS = ((8, 3), (2, 2, 1, 2), (10, 2, 1, 2, 1, 2), (1, 2, 1, 2, 5, 2, 1, 2))
_SCAN_AUTO_DOWNSAMPLE_FACTOR = 1.0

_POWER_MONITOR_DETECTOR_BORDER_COLOR_BY_ID = {
    Detector.DE_1: "#1b9e77",
    Detector.DE_2: "#d95f02",
    Detector.DE_3: "#7570b3",
    Detector.DE_4: "#e7298a",
}
_POWER_MONITOR_DETECTOR_FILL_COLOR_BY_ID = {
    Detector.DE_1: "#76C4AD",
    Detector.DE_2: "#e89f67",
    Detector.DE_3: "#ACA9D1",
    Detector.DE_4: "#F07EB8",
}
_DETECTOR_COLOR_BY_ID = {
    **_POWER_MONITOR_DETECTOR_BORDER_COLOR_BY_ID,
    Detector.DE_5: "#6a3d9a",
}
_DETECTOR_REFERENCE_COLOR_BY_ID = {
    **_POWER_MONITOR_DETECTOR_FILL_COLOR_BY_ID,
    Detector.DE_5: "#A58BBF",
}
_POWER_MONITOR_MAX_TEXT_COLOR = "#E40000"
_POWER_MONITOR_AXIS_LABEL_POINT_SIZE = 12
_POWER_MONITOR_TICK_POINT_SIZE = 11
_POWER_MONITOR_GRID_ALPHA = 0.30
_PLOT_LEGEND_POINT_SIZE = 11
_PLOT_LEGEND_TEXT_COLOR = "#171717"
_PLOT_LEGEND_BACKGROUND = (255, 255, 255, 224)
_PLOT_LEGEND_BORDER = (140, 140, 140, 160)
_POWER_MONITOR_AXIS_LABEL_STYLE = {
    "color": "black",
    "font-size": f"{_POWER_MONITOR_AXIS_LABEL_POINT_SIZE}pt",
    "font-family": "Geist",
    "font-weight": "normal",
}


@dataclass(frozen=True)
class _WavelengthLookup:
    finite_indices: np.ndarray
    finite_wavelengths: np.ndarray
    direction: int
    minimum: float | None
    maximum: float | None


@dataclass
class ScanOverlay:
    """One imported historical scan and its plot-owned presentation state."""

    identity: str
    scan: ImportedScan
    source_path: Path
    label: str
    visible: bool
    style_index: int
    items: dict[Detector, pg.PlotDataItem]
    wavelength_lookup: _WavelengthLookup


def _build_wavelength_lookup(wavelengths: np.ndarray | None) -> _WavelengthLookup:
    if wavelengths is None or wavelengths.size == 0:
        return _WavelengthLookup(np.array([], dtype=int), np.array([], dtype=float), 0, None, None)

    indices = np.flatnonzero(np.isfinite(wavelengths))
    finite = wavelengths[indices]
    if finite.size < 2:
        direction = 1
    else:
        differences = np.diff(finite)
        if np.all(differences >= 0):
            direction = 1
        elif np.all(differences <= 0):
            direction = -1
        else:
            direction = 0
    if finite.size == 0:
        minimum = maximum = None
    else:
        minimum, maximum = float(np.min(finite)), float(np.max(finite))
    return _WavelengthLookup(indices, finite, direction, minimum, maximum)


def _nearest_wavelength_index(lookup: _WavelengthLookup, wavelength: float) -> int | None:
    """Return the original row index nearest to wavelength."""
    values = lookup.finite_wavelengths
    indices = lookup.finite_indices
    if values.size == 0:
        return None
    if lookup.direction == 0:
        return int(indices[np.abs(values - wavelength).argmin()])

    ordered = values if lookup.direction > 0 else -values
    target = wavelength if lookup.direction > 0 else -wavelength
    position = int(np.searchsorted(ordered, target, side="left"))
    candidates: list[int] = []
    for candidate_position in (position - 1, position):
        if 0 <= candidate_position < len(ordered):
            value = ordered[candidate_position]
            first_position = int(np.searchsorted(ordered, value, side="left"))
            candidates.append(first_position)
    best_position = min(candidates, key=lambda i: (abs(float(values[i]) - wavelength), int(indices[i])))
    return int(indices[best_position])


def _configure_power_monitor_axes(
    plot_widget: pg.PlotWidget,
    *,
    bottom_label: str,
    bottom_ticks: list[list[tuple[int, str]]] | None = None,
    show_x_grid: bool,
) -> None:
    """Apply the shared Power Monitor axis typography and grid conventions."""
    tick_font = make_font("sans", _POWER_MONITOR_TICK_POINT_SIZE)
    bottom_axis = plot_widget.getAxis("bottom")
    bottom_axis.setLabel(text=bottom_label, **_POWER_MONITOR_AXIS_LABEL_STYLE)
    bottom_axis.setTickFont(tick_font)
    if bottom_ticks is not None:
        bottom_axis.setTicks(bottom_ticks)

    left_axis = plot_widget.getAxis("left")
    left_axis.setLabel(text="Power (dBm)", **_POWER_MONITOR_AXIS_LABEL_STYLE)
    left_axis.setTickFont(tick_font)
    left_axis.enableAutoSIPrefix(False)

    plot_widget.showGrid(x=show_x_grid, y=True, alpha=_POWER_MONITOR_GRID_ALPHA)


def _configure_plot_legend(legend: pg.LegendItem) -> pg.LegendItem:
    """Apply the shared plot legend style and native sample click behavior."""
    legend.setPen(pg.mkPen(_PLOT_LEGEND_BORDER, width=1))
    legend.setBrush(pg.mkBrush(_PLOT_LEGEND_BACKGROUND))
    legend.opts["labelTextColor"] = _PLOT_LEGEND_TEXT_COLOR
    legend.opts["labelTextSize"] = f"{_PLOT_LEGEND_POINT_SIZE}pt"
    legend.layout.setVerticalSpacing(2)

    family = make_font("sans", _PLOT_LEGEND_POINT_SIZE).family()
    for sample, label in legend.items:
        _configure_plot_legend_entry(sample, label, family)
    return legend


def _configure_plot_legend_entry(sample, label, family: str | None = None) -> None:
    """Style one new legend row without relaying out all existing rows."""
    if family is None:
        family = make_font("sans", _PLOT_LEGEND_POINT_SIZE).family()
    label_style: dict[str, str] = {
        "color": _PLOT_LEGEND_TEXT_COLOR,
        "size": f"{_PLOT_LEGEND_POINT_SIZE}pt",
    }
    if family:
        label_style["family"] = family
    label.setText(label.text, **label_style)
    label.setToolTip("Click the colored sample to hide or show this trace")
    sample.setCursor(Qt.CursorShape.PointingHandCursor)
    sample.setToolTip("Click to hide or show this trace")
    label.setOpacity(1.0 if sample.item.isVisible() else 0.45)


def _overlay_pen(color: str, style_index: int) -> QtGui.QPen:
    """Keep all eight same-color scan traces visually distinct at one pixel."""
    if style_index < len(_OVERLAY_LINE_STYLES):
        return pg.mkPen(color, width=1.0, style=_OVERLAY_LINE_STYLES[style_index], cosmetic=True)
    pen = pg.mkPen(color, width=1.0, cosmetic=True)
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern(
        list(
            _OVERLAY_CUSTOM_DASH_PATTERNS[
                (style_index - len(_OVERLAY_LINE_STYLES)) % len(_OVERLAY_CUSTOM_DASH_PATTERNS)
            ]
        )
    )
    return pen


def _update_plot_legend_hidden_state(legend: pg.LegendItem, item: pg.PlotDataItem) -> None:
    for sample, label in legend.items:
        if sample.item is item:
            label.setOpacity(1.0 if item.isVisible() else 0.45)
            break


def _power_monitor_detector_for_label(label: str, index: int) -> Detector:
    """Resolve a histogram channel label to its optical detector identity."""
    suffix = label.strip().rsplit(" ", 1)[-1]
    try:
        detector = Detector(int(suffix))
    except ValueError:
        detector = Detector(index + 1)
    return detector if detector in _POWER_MONITOR_DETECTOR_BORDER_COLOR_BY_ID else Detector(index + 1)


def derive_scan_export_targets(
    selected_path: str | Path,
    *,
    include_csv: bool,
    include_mat: bool,
    include_fig: bool,
) -> dict[str, Path]:
    """Return only explicitly selected targets for a logical base filename."""
    path = Path(selected_path)
    stem = path.with_suffix("") if path.suffix.lower() in {".csv", ".mat", ".fig"} else path
    targets: dict[str, Path] = {}
    if include_csv:
        targets["CSV"] = stem.with_name(stem.name + ".csv")
    if include_mat:
        targets["MAT"] = stem.with_name(stem.name + ".mat")
    if include_fig:
        targets["FIG"] = stem.with_name(stem.name + ".fig")
    return targets


def build_matlab_fig_payload(measurement: ScanMeasurement) -> str:
    """Serialize acquired detector rows with their identity for the MATLAB worker."""
    traces = []
    for row, detector in enumerate(measurement.detectors):
        if detector not in (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4):
            raise ValueError(f"MATLAB FIG export does not support {detector.name}.")
        traces.append(
            {
                "detector_id": int(detector),
                "label": f"Det {int(detector)}",
                "values": measurement.detector_data[row].tolist(),
                "color": _DETECTOR_COLOR_BY_ID[detector],
            }
        )
    return json.dumps({"wavelengths_nm": measurement.wavelengths_nm.tolist(), "traces": traces})


def _is_numeric_list(values: list[object]) -> bool:
    return all(isinstance(value, int | float) and not isinstance(value, bool) for value in values)


class PowerMonitorTraceWidget(QWidget):
    """Live selected-detector trace driven only by accepted M1 recording samples."""

    def __init__(self, settings: AppSettings | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.app_settings = settings
        self.settings: PowerMonitorAcquisitionSettings | None = None
        self.current_recording: PowerMonitorRecording | None = None
        self.recording_active = False
        self._user_hidden = False
        self.elapsed_values: list[float] = []
        self.detector_values: dict[Detector, list[float]] = {}
        self.display_history: PowerMonitorDisplayHistory | None = None
        self.curve_items: dict[Detector, pg.PlotDataItem] = {}
        self.legend: pg.LegendItem | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.save_recording_button = QToolButton()
        self.save_recording_button.setIcon(QIcon(":/icons/save.svg"))
        self.save_recording_button.setToolTip("Save Power Monitor recording")
        self.save_recording_button.setAccessibleName("Save Power Monitor recording")
        self.save_recording_button.setObjectName("powerMonitorTraceSaveButton")
        self.save_recording_button.setAutoRaise(True)
        self.save_recording_button.setFixedSize(28, 28)
        self.save_recording_button.setEnabled(False)
        self.save_recording_button.clicked.connect(self.save_recording)
        self.close_trace_button = QToolButton()
        self.close_trace_button.setText("×")
        self.close_trace_button.setToolTip("Close Power Monitor trace")
        self.close_trace_button.setAccessibleName("Close Power Monitor trace")
        self.close_trace_button.setObjectName("powerMonitorTraceCloseButton")
        self.close_trace_button.setAutoRaise(True)
        self.close_trace_button.setFixedSize(28, 28)
        self.close_trace_button.clicked.connect(self._hide_by_user)
        self.plot_container = QWidget(self)
        plot_layout = QtWidgets.QGridLayout(self.plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setRowStretch(0, 1)
        plot_layout.setColumnStretch(0, 1)
        self.plot_widget = pg.PlotWidget(background="w", parent=self.plot_container)
        _configure_power_monitor_axes(self.plot_widget, bottom_label="Elapsed time (s)", show_x_grid=True)
        self.plot_widget.setTitle("Power Monitor Recording", **pyqtgraph_title_style())
        plot_layout.addWidget(self.plot_widget, 0, 0)
        self.overlay_controls = QWidget(self.plot_container)
        self.overlay_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout = QHBoxLayout(self.overlay_controls)
        controls_layout.setContentsMargins(4, 4, 4, 4)
        controls_layout.setSpacing(2)
        controls_layout.addWidget(self.save_recording_button)
        controls_layout.addWidget(self.close_trace_button)
        plot_layout.addWidget(
            self.overlay_controls,
            0,
            0,
            1,
            1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        plot_layout.addWidget(
            self.status_label,
            0,
            0,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
        )
        self.overlay_controls.raise_()
        layout.addWidget(self.plot_container, stretch=1)
        self._set_status("")

    @property
    def raw_elapsed_values(self) -> list[float]:
        """Full-resolution sample timestamps mirrored from the recorder."""
        return self.elapsed_values

    @property
    def raw_detector_values(self) -> dict[Detector, list[float]]:
        """Full-resolution detector samples mirrored from the recorder."""
        return self.detector_values

    def start_recording(self, settings: PowerMonitorAcquisitionSettings) -> None:
        self._user_hidden = False
        self._reset_trace(settings)
        self.show()

    def _reset_trace(self, settings: PowerMonitorAcquisitionSettings) -> None:
        self.current_recording = None
        self.save_recording_button.setEnabled(False)
        self.settings = settings
        self.recording_active = True
        self.elapsed_values = []
        self.detector_values = {detector: [] for detector in settings.detectors}
        self.display_history = PowerMonitorDisplayHistory(len(settings.detectors), DEFAULT_DISPLAY_POINT_LIMIT)
        self.display_history.bind_raw_values([self.detector_values[detector] for detector in settings.detectors])
        self.plot_widget.clear()
        self.legend = self.plot_widget.addLegend(
            pen=pg.mkPen(_PLOT_LEGEND_BORDER, width=1),
            brush=pg.mkBrush(_PLOT_LEGEND_BACKGROUND),
            labelTextColor=_PLOT_LEGEND_TEXT_COLOR,
            labelTextSize=f"{_PLOT_LEGEND_POINT_SIZE}pt",
            verSpacing=2,
        )
        self.legend.sigSampleClicked.connect(
            lambda item, legend=self.legend: _update_plot_legend_hidden_state(legend, item)
        )
        self.curve_items = {}
        for detector in settings.detectors:
            curve = self.plot_widget.plot(
                [],
                [],
                pen=pg.mkPen(_POWER_MONITOR_DETECTOR_BORDER_COLOR_BY_ID[detector], width=2),
                name=f"Det {detector.value}",
                connect="finite",
            )
            self.curve_items[detector] = curve
        _configure_plot_legend(self.legend)
        self._set_status("No detector channels selected" if not settings.detectors else "Waiting for recorded samples")

    def append_sample(self, sample: PowerMonitorRecordingSample) -> None:
        if self.settings is None:
            return
        if sample.detectors != self.settings.detectors:
            raise ValueError("recording sample detector identities must match the active trace settings")
        self.elapsed_values.append(sample.elapsed_s)
        for detector, value in zip(sample.detectors, sample.detector_values, strict=True):
            self.detector_values[detector].append(value)
        if self.display_history is not None:
            self.display_history.append(sample.detector_values)
        if self.isVisible():
            self._render_display_history()
        self._set_status("No detector channels selected" if not sample.detectors else "")

    def _render_display_history(self) -> None:
        display = self.display_history
        if display is None:
            return
        for channel, (detector, curve) in enumerate(self.curve_items.items()):
            indices = display.indices(channel)
            curve.setData(
                [self.elapsed_values[int(index)] for index in indices],
                [self.detector_values[detector][int(index)] for index in indices],
                connect="finite",
            )

    def set_completed_recording(self, recording: PowerMonitorRecording) -> None:
        self._reset_trace(recording.settings)
        self.recording_active = False
        self.current_recording = recording
        self.save_recording_button.setEnabled(len(recording.elapsed_s) > 0)
        self.elapsed_values = recording.elapsed_s.tolist()
        self.detector_values = {
            detector: recording.detector_data[row].tolist() for row, detector in enumerate(recording.detectors)
        }
        self.display_history = PowerMonitorDisplayHistory(len(recording.detectors), DEFAULT_DISPLAY_POINT_LIMIT)
        self.display_history.bind_raw_values([self.detector_values[detector] for detector in recording.detectors])
        self.display_history.rebuild_from_raw()
        if self.isVisible():
            self._render_display_history()
        if not recording.detectors:
            self._set_status("No detector channels selected")
        elif not len(recording.elapsed_s):
            self._set_status("No samples captured")
        else:
            self._set_status("")

    def discard_recording(self) -> None:
        self.settings = None
        self.current_recording = None
        self.save_recording_button.setEnabled(False)
        self.recording_active = False
        self.elapsed_values = []
        self.detector_values = {}
        self.display_history = None
        self.curve_items = {}
        self.legend = None
        self.plot_widget.clear()
        self._set_status("")
        self.hide()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._render_display_history()

    def _hide_by_user(self) -> None:
        self._user_hidden = True
        self.hide()

    def save_recording(self) -> None:
        recording = self.current_recording
        if recording is None:
            QMessageBox.warning(self, "No Recording", "There is no completed recording to save.")
            return
        if len(recording.elapsed_s) == 0:
            QMessageBox.warning(
                self,
                "No Samples",
                "This recording contains no captured samples and cannot be exported.",
            )
            return
        if self.app_settings is None:
            QMessageBox.warning(self, "Save Recording", "Power Monitor export preferences are unavailable.")
            return

        directory = self.app_settings.power_monitor_export_directory()
        formats = self.app_settings.power_monitor_export_formats()
        default_name = default_power_monitor_export_name(recording)
        dialog = PowerMonitorExportDialog(directory, default_name, formats, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        request = dialog.export_request
        if request is None or not (request.csv or request.mat):
            return

        self.app_settings.set_power_monitor_export_directory(request.directory)
        self.app_settings.set_power_monitor_export_formats(csv=request.csv, mat=request.mat)
        self.app_settings.sync()

        targets = derive_power_monitor_export_targets(
            request.directory / request.base_name,
            include_csv=request.csv,
            include_mat=request.mat,
        )
        conflicts = [path for path in targets.values() if path.exists()]
        if conflicts:
            conflict_list = "\n".join(str(path) for path in conflicts)
            answer = QMessageBox.question(
                self,
                "Overwrite Existing Files?",
                f"The following export targets already exist:\n{conflict_list}\n\nOverwrite all listed files?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            payload = build_power_monitor_export_v1(recording, comment=request.comment)
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "Power Monitor Export Not Supported", str(error))
            return

        saved: list[Path] = []
        errors: list[str] = []
        if request.csv:
            csv_path = targets["CSV"]
            try:
                with csv_path.resolve().open("w", encoding="utf-8", newline="") as output:
                    np.savetxt(
                        output, payload.csv_data, delimiter=",", header=payload.csv_header, comments="", fmt="%.17g"
                    )
                saved.append(csv_path)
            except Exception as error:
                logger.exception("Power Monitor CSV save failed")
                errors.append(f"CSV: {error}")

        if request.mat:
            mat_path = targets["MAT"]
            try:
                from scipy.io import savemat

                savemat(str(mat_path.resolve()), payload.mat_data, do_compression=True)
                saved.append(mat_path)
            except Exception as error:
                logger.exception("Power Monitor MAT save failed")
                errors.append(f"MAT: {error}")

        saved_paths = "\n".join(str(path) for path in saved)
        if errors:
            QMessageBox.warning(
                self,
                "Save Issues",
                "Some files may have saved:\n" + saved_paths + "\n\nErrors occurred:\n" + "\n".join(errors),
            )
        elif saved:
            QMessageBox.information(
                self, "Save Successful", "Power Monitor data saved successfully to:\n" + saved_paths
            )

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))


class ColorBarWidget(QWidget):
    """A custom widget to display a color bar with min/max labels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(100)  # Keep it narrow
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 10)

        self.max_label = QLabel("Max")
        self.max_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.gradient_widget = pg.GradientWidget(orientation="right")

        self.min_label = QLabel("Min")
        self.min_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.max_label)
        layout.addWidget(self.gradient_widget)
        layout.addWidget(self.min_label)

    @Slot(object, float, float)
    def update_colormap(self, cmap, min_val, max_val):
        """Updates the gradient and labels."""
        if cmap:
            self.gradient_widget.setColorMap(cmap)
            self.gradient_widget.setVisible(True)
        else:
            # If no colormap (flat surface), hide the gradient
            self.gradient_widget.setVisible(False)

        self.max_label.setText(f"{max_val:.3f} mW")
        self.min_label.setText(f"{min_val:.3f} mW")


class Plot3DWidget(QWidget):
    """
    A widget for displaying a 3D surface plot, including a title, axes,
    and hooks for an external color bar.
    """

    # Signal to update the color bar: cmap object, min value, max value
    colormap_updated = Signal(object, float, float)

    # This factor scales the Z-axis to make height variations visible.
    # Tune this value to adjust the "peakiness" of the plot.
    Z_SCALE_FACTOR = 200.0

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("Power Map")
        self.title_label.setFont(plot_title_font())
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.view = gl.GLViewWidget()
        self.view.opts["distance"] = 40
        self.view.opts["elevation"] = 30
        self.view.opts["azimuth"] = -30
        layout.addWidget(self.view, stretch=1)

        # --- NEW: Initialize plot items that will be updated later ---
        self.surface_plot = None
        self.grid_item = gl.GLGridItem()
        self.x_axis = gl.GLLinePlotItem()
        self.y_axis = gl.GLLinePlotItem()
        self.z_axis = gl.GLLinePlotItem()
        self.x_label = gl.GLTextItem()
        self.y_label = gl.GLTextItem()
        self.z_label = gl.GLTextItem()

        # Add the items to the view once
        self.view.addItem(self.grid_item)
        self.view.addItem(self.x_axis)
        self.view.addItem(self.y_axis)
        self.view.addItem(self.z_axis)
        self.view.addItem(self.x_label)
        self.view.addItem(self.y_label)
        self.view.addItem(self.z_label)

    @Slot(np.ndarray, np.ndarray, np.ndarray)
    def update_plot(self, x: np.ndarray, y: np.ndarray, z: np.ndarray):
        """
        Updates the plot with new data, including scaled axes and labels.
        x, y: 1D arrays of coordinates in micrometers.
        z: 2D array of power values in milliwatts.
        """
        if self.surface_plot:
            self.view.removeItem(self.surface_plot)

        if x is None or y is None or z is None or z.size == 0:
            self.title_label.setText("Power Map (No Data)")
            return

        expected_z_shape = (len(x), len(y))
        if z.ndim != 2 or z.shape != expected_z_shape:
            raise ValueError(f"Mapping z shape {z.shape} does not match expected {expected_z_shape} (len(x), len(y))")

        self.title_label.setText("Power Map (mW)")

        x_min, x_max = x.min(), x.max()
        y_min, y_max = y.min(), y.max()
        z_min, z_max = z.min(), z.max()

        # --- 1. DYNAMIC Z-AXIS AUTO-SCALING ---
        xy_span = max(x_max - x_min, y_max - y_min)
        if xy_span == 0:
            xy_span = 1.0  # Avoid division by zero

        # If z is flat, don't scale it, otherwise compute a dynamic scale factor
        z_span = z_max - z_min
        if z_span > 1e-9:
            # We want the visual height of the peak to be a fraction of the plot's width
            # 0.3 is a good starting point, tune as needed.
            desired_z_height = xy_span * 0.3
            z_scale_factor = desired_z_height / z_span
            z_scaled = z * z_scale_factor
        else:
            # If the surface is flat, no scaling is needed.
            z_scaled = z.copy()

        # Center the plot visually by shifting the data
        x_shifted = x - (x_max + x_min) / 2
        y_shifted = y - (y_max + y_min) / 2
        z_scaled_shifted = z_scaled - z_scaled.min()

        # --- 2. UPDATE THE PLOT SURFACE ---
        if z_span < 1e-9:  # Flat surface
            color = (0.8, 0.8, 0.8, 1.0)
            self.surface_plot = gl.GLSurfacePlotItem(
                x=x_shifted, y=y_shifted, z=z_scaled_shifted, color=color, shader="shaded"
            )
        else:
            cmap = pg.colormap.get("viridis")
            normalized_z = (z - z_min) / z_span
            colors = cmap.map(normalized_z, "float")
            self.surface_plot = gl.GLSurfacePlotItem(
                x=x_shifted, y=y_shifted, z=z_scaled_shifted, colors=colors, shader="shaded"
            )

        self.surface_plot.setGLOptions("opaque")
        self.view.addItem(self.surface_plot)

        # --- 3. CONFIGURE AXES AND GRID TO MATCH DATA ---
        # Configure the grid to match the shifted data ranges
        self.grid_item.resetTransform()
        self.grid_item.setSize(x=max(abs(x_shifted)), y=max(abs(y_shifted)))
        self.grid_item.setSpacing(x=max(abs(x_shifted)) / 5, y=max(abs(y_shifted)) / 5)

        # Configure the axis lines
        axis_len_x = x_max - x_min
        axis_len_y = y_max - y_min
        axis_len_z = z_scaled.max() - z_scaled.min()

        # X-Axis Line and Label
        x_axis_points = np.array([[x_shifted.min(), y_shifted.min(), 0], [x_shifted.max(), y_shifted.min(), 0]])
        self.x_axis.setData(pos=x_axis_points, color=(1, 1, 1, 1), width=2)
        self.x_label.setData(pos=(0, y_shifted.min() - axis_len_y * 0.1, 0), text="X (µm)")

        # Y-Axis Line and Label
        y_axis_points = np.array([[x_shifted.min(), y_shifted.min(), 0], [x_shifted.min(), y_shifted.max(), 0]])
        self.y_axis.setData(pos=y_axis_points, color=(1, 1, 1, 1), width=2)
        self.y_label.setData(pos=(x_shifted.min() - axis_len_x * 0.1, 0, 0), text="Y (µm)")

        # Z-Axis Line and Label
        z_axis_points = np.array(
            [[x_shifted.min(), y_shifted.min(), 0], [x_shifted.min(), y_shifted.min(), axis_len_z]]
        )
        self.z_axis.setData(pos=z_axis_points, color=(1, 1, 1, 1), width=2)
        self.z_label.setData(pos=(x_shifted.min(), y_shifted.min(), axis_len_z * 1.05), text="Power (a.u.)")

        # --- 4. UPDATE THE COLOR BAR ---
        self.colormap_updated.emit(cmap if z_span > 1e-9 else None, z_min, z_max)


###############################################################################
# MatlabSaveWorker
###############################################################################
class MatlabSaveWorker(QObject):
    """
    Worker QObject to save scan data to a .fig file using MATLAB Engine
    in a separate thread.
    """

    finished_saving = Signal(str, bool, str)  # Emits: filetype, success, message_or_filename
    engine_unhealthy = Signal(str)

    def __init__(self, parent: QObject | None = None):  # parent is PlotWidget
        super().__init__(parent)
        self._is_running = True

    @Slot(str, str, str, str, str, "QWidget*")
    def save_matlab_fig(
        self,
        payload_json_str: str,
        fig_filename: str,
        title_str: str,
        xlabel_str: str,
        ylabel_str: str,
        plot_widget_ptr: QWidget | None,
    ):
        if not self._is_running:
            logger.info("MatlabSaveWorker: Save FIG cancelled (worker not running).")
            self.finished_saving.emit("fig", False, "Save cancelled by user.")
            return

        if not MATLAB_ENGINE_AVAILABLE:
            logger.warning("MatlabSaveWorker: MATLAB Engine not available. Cannot save .fig.")
            self.finished_saving.emit("fig", False, "MATLAB Engine not available.")
            return

        wavelengths_list: list[int | float] = []
        traces: list[tuple[str, list[int | float], list[float]]] = []
        try:
            payload = json.loads(payload_json_str)
            if not isinstance(payload, dict):
                raise ValueError("Payload must be an object.")  # noqa: TRY004 — invalid JSON payload keeps ValueError API
            wavelengths = payload.get("wavelengths_nm")
            raw_traces = payload.get("traces")
            if not isinstance(wavelengths, list) or not wavelengths or not _is_numeric_list(wavelengths):
                raise ValueError("wavelengths_nm must be a non-empty numeric list.")
            if not isinstance(raw_traces, list) or not raw_traces:
                raise ValueError("traces must be a non-empty list.")
            detector_ids: set[int] = set()
            for index, raw_trace in enumerate(raw_traces):
                if not isinstance(raw_trace, dict):
                    raise ValueError(f"Trace {index} must be an object.")  # noqa: TRY004 — invalid JSON payload keeps ValueError API
                detector_id = raw_trace.get("detector_id")
                label = raw_trace.get("label")
                values = raw_trace.get("values")
                color = raw_trace.get("color")
                if isinstance(detector_id, bool) or not isinstance(detector_id, int) or detector_id not in range(1, 5):
                    raise ValueError(f"Trace {index} detector_id must identify DE1-DE4.")
                if detector_id in detector_ids:
                    raise ValueError(f"Duplicate detector_id {detector_id}.")
                detector_ids.add(detector_id)
                if not isinstance(label, str) or not label:
                    raise ValueError(f"Trace {index} label must be a non-empty string.")
                if not isinstance(values, list) or not _is_numeric_list(values):
                    raise ValueError(f"Trace {index} values must be a numeric list.")
                if len(values) != len(wavelengths):
                    raise ValueError(f"Trace {index} values length must match wavelengths_nm.")
                if not isinstance(color, str) or len(color) != 7 or not color.startswith("#"):
                    raise ValueError(f"Trace {index} color must be a #RRGGBB value.")
                try:
                    rgb = [int(color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5)]
                except ValueError as error:
                    raise ValueError(f"Trace {index} color must be a #RRGGBB value.") from error
                traces.append((label, values, rgb))
            wavelengths_list = wavelengths
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            error_msg = f"FIG: Error decoding or validating JSON data: {e}"
            logger.error(error_msg)
            self.finished_saving.emit("fig", False, error_msg)
            return

        shared_matlab_engine: matlab.engine.MatlabEngine | None = None
        if plot_widget_ptr is not None and isinstance(plot_widget_ptr, PlotWidget):  # Type check
            # Call the getter method on the PlotWidget instance
            # This call happens in the worker's thread.
            # The get_matlab_engine method in PlotWidget needs to be thread-safe.
            shared_matlab_engine = plot_widget_ptr.get_matlab_engine()
        else:
            logger.error("MatlabSaveWorker: PlotWidget instance not provided correctly.")
            self.finished_saving.emit("fig", False, "Internal error: PlotWidget reference missing.")
            return

        eng_to_use: matlab.engine.MatlabEngine | None = shared_matlab_engine

        try:
            if eng_to_use is None:
                raise RuntimeError("Shared MATLAB Engine is not ready; refusing to start a second Engine.")

            if not self._is_running:
                logger.info("MatlabSaveWorker: Save FIG cancelled before MATLAB operation.")
                self.finished_saving.emit("fig", False, "Save cancelled by user.")
                return

            try:
                eng_to_use.eval("1;", nargout=0)
            except Exception as error:  # noqa: BLE001 - MATLAB Engine reports health failures through varied exceptions.
                self.engine_unhealthy.emit(str(error))
                return

            wavelengths_mat = matlab.double(wavelengths_list)

            h_fig = None
            try:
                h_fig = eng_to_use.figure(nargout=1)
                if not self._is_running:
                    self.finished_saving.emit("fig", False, "Save cancelled by user.")
                    return
                eng_to_use.hold("on", nargout=0)
                for label, values, rgb in traces:
                    rgb_mat = matlab.double(rgb)
                    eng_to_use.plot(
                        wavelengths_mat,
                        matlab.double(values),
                        "Color",
                        rgb_mat,
                        "DisplayName",
                        label,
                        nargout=0,
                    )
                eng_to_use.hold("off", nargout=0)
                eng_to_use.xlabel(xlabel_str, nargout=0)
                eng_to_use.ylabel(ylabel_str, nargout=0)
                eng_to_use.title(title_str, nargout=0)
                eng_to_use.grid("on", nargout=0)
                eng_to_use.legend(nargout=0)
                eng_to_use.savefig(h_fig, fig_filename, nargout=0)
            finally:
                if h_fig is not None:
                    try:
                        eng_to_use.close(h_fig, nargout=0)
                        logger.info("MatlabSaveWorker: MATLAB figure closed.")
                    except Exception as e_close:  # noqa: BLE001  # Preserve optional MATLAB and export fallbacks.
                        logger.warning(f"MatlabSaveWorker: Could not close MATLAB figure: {e_close}")

            logger.info(f"MatlabSaveWorker: Saved plot to FIG: {fig_filename}")
            self.finished_saving.emit("fig", True, fig_filename)

        except ImportError:
            error_msg = "FIG: MATLAB Engine for Python not installed or found (runtime check)."
            logger.error(error_msg)
            self.finished_saving.emit("fig", False, error_msg)
        except Exception as e:
            error_msg = f"FIG: MATLAB export failed: {e}"
            logger.exception(error_msg)
            self.finished_saving.emit("fig", False, error_msg)

    @Slot()
    def stop_worker(self):
        logger.debug("MatlabSaveWorker: Stop requested.")
        self._is_running = False


# =============================================================================
# Histogram Widget (using PyQtGraph)
# =============================================================================
class HistogramWidget(QtWidgets.QWidget):
    """
    Displays real-time power monitoring data as a histogram using PyQtGraph.
    Updates are throttled for smooth performance. Expects power data as a dictionary.
    """

    _UPDATE_INTERVAL_MS = 50
    _DEFAULT_Y_RANGE = (-70, 10)
    _LOW_SIGNAL_FLOOR = -100.0
    _HIGH_SIGNAL_CEILING = 10.0
    _VALUE_LABEL_GAP_PX = 0
    _VALUE_LABEL_EDGE_MARGIN_PX = 4

    def __init__(self, control_panel, detector_keys: list[str], parent: QWidget | None = None):
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
        self.value_text_font_size = 15  # Specific size for value annotations on bars

        self.max_pen = pg.mkPen("#e41a1c", width=1.5, style=QtCore.Qt.PenStyle.DashLine)
        self.detector_ids = tuple(_power_monitor_detector_for_label(key, i) for i, key in enumerate(self.detector_keys))
        self.bar_brushes = [
            pg.mkBrush(_POWER_MONITOR_DETECTOR_FILL_COLOR_BY_ID[detector]) for detector in self.detector_ids
        ]
        self.bar_pens = [
            pg.mkPen(_POWER_MONITOR_DETECTOR_BORDER_COLOR_BY_ID[detector]) for detector in self.detector_ids
        ]
        self.current_text_color = pg.mkColor("#555555")  # Dark grey for current values
        self.text_font = make_font("mono", self.value_text_font_size)  # Numeric Power Monitor readouts.

        # UI Elements
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.plot_container = QWidget(self)
        plot_layout = QtWidgets.QGridLayout(self.plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setRowStretch(0, 1)
        plot_layout.setColumnStretch(0, 1)
        self.plot_widget = pg.PlotWidget(background="w", parent=self.plot_container)
        plot_layout.addWidget(self.plot_widget, 0, 0)
        self.overlay_controls = QWidget(self.plot_container)
        self.overlay_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout = QHBoxLayout(self.overlay_controls)
        controls_layout.setContentsMargins(4, 4, 4, 4)
        self.reset_btn = QToolButton()
        self.reset_btn.setIcon(QIcon(":/icons/eraser.svg"))
        self.reset_btn.setToolTip("Reset current and maximum values")
        self.reset_btn.setAccessibleName("Reset Power Monitor values")
        self.reset_btn.setObjectName("powerMonitorResetButton")
        self.reset_btn.setAutoRaise(True)
        self.reset_btn.setFixedSize(28, 28)
        controls_layout.addWidget(self.reset_btn)
        plot_layout.addWidget(
            self.overlay_controls,
            0,
            0,
            1,
            1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        self.overlay_controls.raise_()
        self.layout.addWidget(self.plot_container, stretch=1)

        # Plot items
        self.bars: pg.BarGraphItem | None = None
        self.max_lines: list[pg.PlotCurveItem] = []
        # Initialize text item lists (filled in _create_plot_items)
        self.max_texts: list[pg.TextItem | None] = []
        self.current_texts: list[pg.TextItem | None] = []

        self._configure_plot()  # Sets up axes, title, grid
        self._create_plot_items()  # Creates bars, lines, and text items

        view_box = self.plot_widget.getViewBox()
        view_box.sigResized.connect(self._handle_view_box_resized)
        view_box.sigYRangeChanged.connect(self._reposition_value_texts)

        self.reset_btn.clicked.connect(self.reset_maxima)

        # Throttling for updates
        self._pending_power_data: dict | None = None
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(self._UPDATE_INTERVAL_MS)
        self._update_timer.timeout.connect(self._process_pending_update)
        self._is_visible = False  # To control timer activity

        # Pre-calculate bar x-positions for max lines
        self._bar_positions = [(i - self.bar_width / 2, i + self.bar_width / 2) for i in range(self.num_bars)]

        self.plot_widget.setYRange(*self._DEFAULT_Y_RANGE)

        logger.info("HistogramWidget initialized successfully.")

    def _configure_plot(self):
        ticks = [[(i, key) for i, key in enumerate(self.detector_keys)]]
        _configure_power_monitor_axes(
            self.plot_widget,
            bottom_label="Detector",
            bottom_ticks=ticks,
            show_x_grid=False,
        )

        self.plot_widget.setTitle("Real-time Power Monitoring", **pyqtgraph_title_style())
        self.plot_widget.setYRange(-70, 10)  # Initial Y range
        self.plot_widget.setXRange(-0.5, self.num_bars - 0.5, padding=0)

    def _create_plot_items(self):
        # Create BarGraphItem
        self.bars = pg.BarGraphItem(
            x=np.arange(self.num_bars),
            height=self.current_values,  # Initialized to zeros
            width=self.bar_width,
            brushes=self.bar_brushes,
            pens=self.bar_pens,
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
            max_text = pg.TextItem(text="", color=_POWER_MONITOR_MAX_TEXT_COLOR)
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
        logger.info("Resetting histogram: current values to 0, max_values to -infinity.")

        self.current_values.fill(0.0)
        self.max_values.fill(-np.inf)

        self._update_y_axis_scale()
        self._update_visual_elements()
        t_end = time.perf_counter()
        logger.debug(f"Reset Axes execution took: {(t_end - t_start) * 1000:.3f} ms")

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
            logger.warning(f"HistogramWidget: Invalid power data type: {type(data_to_process)}")
            return

        try:
            detector_values_from_signal = data_to_process.get("detectors", {})

            new_values_from_signal = np.array(
                [detector_values_from_signal.get(key, -np.inf) for key in self.detector_keys],
                dtype=float,
            )

            new_values_processed = np.nan_to_num(
                new_values_from_signal,
                nan=self._LOW_SIGNAL_FLOOR,
                posinf=self._HIGH_SIGNAL_CEILING,
                neginf=self._LOW_SIGNAL_FLOOR,
            )

            self._update_values(new_values_processed)
            self._update_y_axis_scale()
            self._update_visual_elements()

        except Exception:
            logger.exception("HistogramWidget: Error processing histogram update")

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
            logger.warning("HistogramWidget: Bars not initialized in _update_visual_elements.")
            return
        self.bars.setOpts(height=self.current_values)
        for i in range(self.num_bars):
            current_val = self.current_values[i]
            max_val = self.max_values[i]
            x_center = i
            x_start_line, x_end_line = self._bar_positions[i]
            self._update_max_line(i, x_start_line, x_end_line, max_val)
            self._update_max_text(i, x_center, max_val)  # Max text should be OVER
            self._update_current_text(i, x_center, current_val)  # Current text should be UNDER
        self._update_y_axis_scale()

    def _update_max_line(self, i: int, x_start: float, x_end: float, max_val: float):
        if i < len(self.max_lines) and self.max_lines[i] is not None:
            if np.isfinite(max_val):
                self.max_lines[i].setData(x=[x_start, x_end], y=[max_val, max_val])
            else:
                self.max_lines[i].clear()
        else:
            logger.warning(f"Max line for index {i} not properly initialized.")

    def _update_max_text(self, i: int, x_center: float, max_val: float):
        # Max text: To appear ABOVE the line
        if i >= len(self.max_texts) or self.max_texts[i] is None:
            return
        text_item = self.max_texts[i]
        show_text = np.isfinite(max_val) and max_val > -90
        if show_text:
            text_item.setText(f"{max_val:.2f}")
            text_item.setAnchor((0.5, 1.0))  # Anchor bottom-center
            text_y_position = max_val + self._value_label_gap_y()
            text_item.setPos(x_center, text_y_position)
            text_item.setVisible(True)
        else:
            text_item.setVisible(False)

    def _update_current_text(self, i: int, x_center: float, current_val: float):
        # Current text: To appear BELOW the line
        if i >= len(self.current_texts) or self.current_texts[i] is None:
            return
        text_item = self.current_texts[i]
        show_text = np.isfinite(current_val) and current_val > -90
        if show_text:
            text_item.setText(f"{current_val:.2f}")
            text_item.setAnchor((0.5, 0.0))  # Anchor top-center
            text_y_position = current_val - self._value_label_gap_y()
            text_item.setPos(x_center, text_y_position)
            text_item.setVisible(True)
        else:
            text_item.setVisible(False)

    def _value_label_gap_y(self) -> float:
        _pixel_width, pixel_height = self.plot_widget.getViewBox().viewPixelSize()
        if not np.isfinite(pixel_height) or pixel_height == 0:
            return 0.0
        return self._VALUE_LABEL_GAP_PX * abs(pixel_height)

    def _reposition_value_texts(self, *_args) -> None:
        gap_y = self._value_label_gap_y()
        for i, (max_text, current_text) in enumerate(zip(self.max_texts, self.current_texts)):
            if max_text is not None and max_text.isVisible() and np.isfinite(self.max_values[i]):
                max_text.setPos(i, self.max_values[i] + gap_y)
            if current_text is not None and current_text.isVisible() and np.isfinite(self.current_values[i]):
                current_text.setPos(i, self.current_values[i] - gap_y)

    def _handle_view_box_resized(self, *_args) -> None:
        self._update_y_axis_scale()
        self._reposition_value_texts()

    def _update_y_axis_scale(self):
        try:
            viewable_current = self.current_values[np.isfinite(self.current_values)]
            viewable_max = self.max_values[np.isfinite(self.max_values)]

            if viewable_current.size == 0 and viewable_max.size == 0:
                self.plot_widget.setYRange(-70, 10, padding=0)
                return

            combined_finite_vals = np.array([])
            if viewable_current.size > 0:
                combined_finite_vals = np.concatenate((combined_finite_vals, viewable_current))
            if viewable_max.size > 0:
                combined_finite_vals = np.concatenate((combined_finite_vals, viewable_max))

            if combined_finite_vals.size == 0:
                self.plot_widget.setYRange(-70, 10, padding=0)
                return

            y_min_data = np.min(combined_finite_vals)
            y_max_data = np.max(combined_finite_vals)

            data_range = y_max_data - y_min_data
            padding = max(2.0, data_range * 0.2) if data_range > 1e-6 else 2.0
            y_min_view = y_min_data - padding
            y_max_view = y_max_data + padding
            y_min_view = max(y_min_view, self._LOW_SIGNAL_FLOOR)
            y_max_view = min(y_max_view, self._HIGH_SIGNAL_CEILING)

            if y_max_view - y_min_view < 10.0:
                mid_point = (y_max_view + y_min_view) / 2.0
                y_min_view = mid_point - 5.0
                y_max_view = mid_point + 5.0
                y_min_view = max(y_min_view, self._LOW_SIGNAL_FLOOR)
                y_max_view = min(y_max_view, self._HIGH_SIGNAL_CEILING)

            # Reserve the annotation's rendered height at both plot edges. The
            # initial range is data-driven and constrained above; expanding it
            # here preserves that scaling while making the pixel clearance stable
            # as the ViewBox becomes shorter during recording.
            view_height_px = self.plot_widget.getViewBox().sceneBoundingRect().height()
            rendered_label_height = max(
                (item.boundingRect().height() for item in (*self.max_texts, *self.current_texts) if item is not None),
                default=0.0,
            )
            label_height_px = max(QtGui.QFontMetricsF(self.text_font).height(), rendered_label_height)
            label_reserve_px = label_height_px + self._VALUE_LABEL_EDGE_MARGIN_PX
            if np.isfinite(view_height_px) and view_height_px > 2 * label_reserve_px:
                base_span = y_max_view - y_min_view
                final_span = base_span / (1.0 - 2.0 * label_reserve_px / view_height_px)
                extra_span = final_span - base_span
                y_min_view -= extra_span / 2.0
                y_max_view += extra_span / 2.0

            self.plot_widget.setYRange(y_min_view, y_max_view, padding=0)

        except Exception:
            logger.exception("Error updating y-axis scale")


# =============================================================================
# Plot Widget (using PyQtGraph - MATLAB fig saving restored)
# =============================================================================
class PlotWidget(QWidget):
    """
    Displays scan results using PyQtGraph. Saves data as CSV, MAT, PNG, SVG, and FIG (if MATLAB Engine is available).
    """

    _THREAD_WAIT_TIMEOUT_MS = 2000
    _MATLAB_STATUS_TIMEOUT_MS = 2000
    _STATUS_CLEAR_TIMEOUT_MS = 3000
    _DETECTOR_COLORS = _DETECTOR_COLOR_BY_ID

    # Signal to update UI from worker, e.g., re-enable button, show status
    matlab_save_status_update = Signal(str)  # Message for status bar or dialog
    _matlab_save_thread_finished = Signal(object, object)
    open_scan_requested = Signal()
    add_scan_requested = Signal()

    def __init__(self, shared_settings, parent: QWidget | None = None, settings: AppSettings | None = None):
        super().__init__(parent)
        if not isinstance(shared_settings, ScanSettings):
            logger.warning("PlotWidget needs a valid ScanSettings object for metadata.")
            self.shared_settings = ScanSettings()  # Dummy settings
        else:
            self.shared_settings = shared_settings

        # Data Storage
        self.current_wavelengths: np.ndarray | None = None
        self.current_powers: np.ndarray | None = None
        self.current_output_power: float | None = None
        self.current_measurement: ScanMeasurement | ImportedScan | None = None
        self.current_imported_scan: ImportedScan | None = None
        self.reference_measurement: ScanMeasurement | ImportedScan | None = None
        self._current_wavelength_lookup = _build_wavelength_lookup(None)
        self._reference_wavelength_lookup = _build_wavelength_lookup(None)
        self.detector_plot_items: dict[Detector, pg.PlotDataItem] = {}
        self.reference_detector_plot_items: dict[Detector, pg.PlotDataItem] = {}
        self.scan_overlays: list[ScanOverlay] = []
        self.focused_overlay_id: str | None = None
        self._latest_mouse_position: QtCore.QPointF | None = None
        self._last_crosshair_text: str | None = None
        self._user_adjusted_view = False
        self.detector_legend: pg.LegendItem | None = None
        self._saved_measurement_ref: ReferenceType[ScanMeasurement] | None = None
        self._export_measurement: ScanMeasurement | None = None
        self.pending_saves = 0

        # --- Worker Thread Setup for MATLAB Saving ---
        # We'll create the thread and worker on-demand when saving to .fig
        self.matlab_save_thread: QThread | None = None
        self.matlab_save_worker: MatlabSaveWorker | None = None
        # --- End Worker Thread Setup ---

        self.matlab_engine_manager = MatlabEngineManager(
            available=MATLAB_ENGINE_AVAILABLE,
            start_engine=(lambda **kwargs: matlab.engine.start_matlab(**kwargs)) if MATLAB_ENGINE_AVAILABLE else None,
            parent=self,
        )
        self.matlab_engine_manager.ready.connect(self._on_matlab_engine_ready)
        self.matlab_engine_manager.startup_failed.connect(self._on_matlab_engine_startup_failed)
        self.matlab_engine_manager.state_changed.connect(self._on_matlab_engine_state_changed)
        self._pending_matlab_fig: tuple[str, str, str, str, str] | None = None
        self._matlab_fig_retry_count = 0
        self._cleaned_up = False
        self._matlab_save_thread_finished.connect(
            self._handle_matlab_save_thread_finished, Qt.ConnectionType.QueuedConnection
        )

        # --- NEW: Remember last save directory ---
        # Start with the current working directory
        self.settings = settings
        self.last_scan_save_dir = settings.scan_export_directory() if settings is not None else Path.cwd()
        self.last_plot_image_dir = settings.plot_image_directory() if settings is not None else Path.cwd()

        # --- UI Setup ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.plot_container = QWidget(self)
        plot_layout = QtWidgets.QGridLayout(self.plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setRowStretch(0, 1)
        plot_layout.setColumnStretch(0, 1)
        self.plot_widget = pg.PlotWidget(background="w", parent=self.plot_container)
        self.plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        plot_layout.addWidget(self.plot_widget, 0, 0)
        layout.addWidget(self.plot_container, stretch=1)

        # --- NEW: Reference Plot Item (The "Frozen" Trace) ---
        # We add this BEFORE the live item so it renders behind it implicitly,
        # but we will also set ZValue to be sure.
        self.reference_plot_item = self.plot_widget.plot(
            pen=pg.mkPen(color="#A0A0A0", width=1.5),
            name="Reference",
            skipFiniteCheck=True,
        )
        self.reference_plot_item.setZValue(0)  # Send to back
        # -----------------------------------------------------

        # PlotDataItem for the main scan data
        self.plot_data_item = self.plot_widget.plot(
            pen=pg.mkPen(color="#1f78b4", width=2.0),
            autoDownsampleFactor=_SCAN_AUTO_DOWNSAMPLE_FACTOR,
            # symbol="o",
            # symbolPen=None,  # No outline for symbol
            # symbolBrush=pg.mkBrush("#1f78b4"),
            # symbolSize=4,  # Adjust size as needed
        )
        self.plot_data_item.setZValue(10)  # Ensure live data is always on top

        self.clear_btn = QToolButton()
        self.clear_btn.setIcon(QIcon(":/icons/eraser.svg"))
        self.clear_btn.setToolTip("Clear all live and frozen traces")
        self.clear_btn.setAccessibleName("Clear wavelength scan plot")
        self.clear_btn.setObjectName("scanPlotClearButton")
        self.clear_btn.setAutoRaise(True)
        self.clear_btn.setFixedSize(28, 28)
        self.clear_btn.clicked.connect(self.clear_plot)

        self.freeze_btn = QToolButton()
        self.freeze_btn.setIcon(QIcon(":/icons/snowflake.svg"))
        self.freeze_btn.setToolTip("Snapshot the current trace to the background for comparison")
        self.freeze_btn.setAccessibleName("Freeze wavelength scan trace")
        self.freeze_btn.setObjectName("scanPlotFreezeButton")
        self.freeze_btn.setAutoRaise(True)
        self.freeze_btn.setFixedSize(28, 28)
        self.freeze_btn.clicked.connect(self.freeze_current_trace)
        self.freeze_btn.setEnabled(False)

        self.load_btn = QToolButton()
        self.load_btn.setIcon(QIcon(":/icons/folder-open.svg"))
        self.load_btn.setToolTip("Open saved scan (CSV/MAT)")
        self.load_btn.setAccessibleName("Open wavelength scan data")
        self.load_btn.setObjectName("scanPlotLoadButton")
        self.load_btn.setAutoRaise(True)
        self.load_btn.setFixedSize(28, 28)
        self.load_btn.clicked.connect(lambda: self.open_scan_requested.emit())
        self.load_menu = QtWidgets.QMenu(self.load_btn)
        self.load_menu.addAction("Open / Replace Scan...", self.open_scan_requested.emit)
        self.load_menu.addAction("Add Scan to Plot...", self.add_scan_requested.emit)
        self.load_menu.addAction("Manage Overlays...", self.manage_overlays)
        self.load_btn.setMenu(self.load_menu)
        self.load_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

        # Configure axes, title, grid
        tick_font = make_font("sans", 11)
        label_style = {"color": "black", "font-size": "12pt"}

        self.plot_widget.setLabel("left", _GENERIC_SCAN_Y_LABEL, **label_style)
        self.plot_widget.getAxis("left").setTickFont(tick_font)
        self.plot_widget.setLabel("bottom", "Wavelength (nm)", **label_style)
        self.plot_widget.getAxis("bottom").setTickFont(tick_font)
        self.set_plot_title("Wavelength Scan")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        # PyQtGraph auto-ranges by default, which is often sufficient.

        # --- NEW: Crosshair Setup ---
        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#aaa", style=Qt.PenStyle.DashLine))
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#aaa", style=Qt.PenStyle.DashLine))
        self.v_line.setVisible(False)
        self.h_line.setVisible(False)
        self.plot_widget.addItem(self.v_line, ignoreBounds=True)
        self.plot_widget.addItem(self.h_line, ignoreBounds=True)

        # Label to display coordinates
        self.cursor_label = pg.TextItem(anchor=(0, 1), color="k")  # 'k' is black, use 'w' for dark mode
        self.plot_widget.addItem(self.cursor_label)

        # Connect mouse move event
        self._crosshair_refresh_timer = QTimer(self)
        self._crosshair_refresh_timer.setSingleShot(True)
        self._crosshair_refresh_timer.setInterval(40)
        self._crosshair_refresh_timer.timeout.connect(self._process_latest_mouse_position)
        self.plot_widget.scene().sigMouseMoved.connect(self._queue_mouse_moved)
        self.plot_widget.plotItem.vb.sigRangeChangedManually.connect(self._on_manual_view_range_changed)
        # ----------------------------

        self.save_btn = QToolButton()
        self.save_btn.setIcon(QtGui.QIcon(":/icons/save.svg"))
        self.save_btn.setToolTip("Save scan data")
        self.save_btn.setAccessibleName("Save wavelength scan data")
        self.save_btn.setObjectName("scanPlotSaveButton")
        self.save_btn.setAutoRaise(True)
        self.save_btn.setFixedSize(28, 28)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_scan_data)

        self.matlab_status_label = QLabel("", self.plot_container)
        self.matlab_status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.matlab_status_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.matlab_status_label.setObjectName("scanPlotStatusLabel")
        self.matlab_status_label.setVisible(False)
        self._status_clear_timer = QTimer(self)
        self._status_clear_timer.setSingleShot(True)
        self._status_clear_timer.setInterval(self._STATUS_CLEAR_TIMEOUT_MS)
        self._status_clear_timer.timeout.connect(self._clear_matlab_status)

        self.screenshot_btn = QToolButton()
        self.screenshot_btn.setIcon(QIcon(":/icons/camera.svg"))
        self.screenshot_btn.setToolTip("Export plot image")
        self.screenshot_btn.setAccessibleName("Export wavelength scan plot image")
        self.screenshot_btn.setObjectName("scanPlotExportImageButton")
        self.screenshot_btn.setAutoRaise(True)
        self.screenshot_btn.setFixedSize(28, 28)
        self.screenshot_btn.clicked.connect(self.export_plot_image)

        self.overlay_controls = QWidget(self.plot_container)
        self.overlay_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        controls_layout = QHBoxLayout(self.overlay_controls)
        controls_layout.setContentsMargins(4, 4, 4, 4)
        controls_layout.setSpacing(2)
        controls_layout.addWidget(self.clear_btn)
        controls_layout.addWidget(self.freeze_btn)
        controls_layout.addWidget(self.load_btn)
        controls_layout.addWidget(self.save_btn)
        controls_layout.addWidget(self.screenshot_btn)
        plot_layout.addWidget(
            self.overlay_controls,
            0,
            0,
            1,
            1,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        plot_layout.addWidget(
            self.matlab_status_label,
            0,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        self.overlay_controls.raise_()
        self.matlab_status_label.raise_()

        logger.info("PlotWidget (PyQtGraph) initialized")

    def set_plot_title(self, text: str, *, color: str = "black") -> None:
        """Set a scan title using the shared 12 pt bold Geist policy."""
        self.plot_widget.setTitle(text, **pyqtgraph_title_style(color))

    def _set_matlab_status(self, message: str, *, persistent: bool = False) -> None:
        if persistent:
            self._status_clear_timer.stop()
        self.matlab_status_label.setText(message)
        self.matlab_status_label.setVisible(bool(message))

    @Slot()
    def export_plot_image(self):
        # 1. Define filename
        # Use last_save_dir logic
        default_name = self.last_plot_image_dir / "plot_capture.png"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Plot Image", str(default_name), "PNG Image (*.png);;JPG Image (*.jpg)"
        )

        if file_path:
            try:
                # 2. Export
                exporter = pg.exporters.ImageExporter(self.plot_widget.plotItem)

                # Optional: Force a specific high resolution width (e.g. 1920px)
                # exporter.parameters()['width'] = 1920

                # If background is transparent/black, ensure it saves correctly
                # (PyQtGraph usually handles this based on current view settings)

                exporter.export(file_path)
                self.last_plot_image_dir = Path(file_path).parent
                if self.settings is not None:
                    self.settings.set_plot_image_directory(self.last_plot_image_dir)
                logger.info(f"Plot image saved to: {file_path}")
            except Exception as e:  # noqa: BLE001  # Preserve optional MATLAB and export fallbacks.
                logger.error(f"Failed to export image: {e}")
                QMessageBox.warning(self, "Export Error", f"Could not save image:\n{e}")

    @Slot(object)
    def _queue_mouse_moved(self, pos) -> None:
        """Coalesce a burst of scene mouse events into at most 25 cursor refreshes/s."""
        self._latest_mouse_position = QtCore.QPointF(pos)
        if not self._crosshair_refresh_timer.isActive():
            self._crosshair_refresh_timer.start()

    @Slot()
    def _process_latest_mouse_position(self) -> None:
        pos = self._latest_mouse_position
        if pos is not None and not self._cleaned_up:
            self._on_mouse_moved(pos)

    @Slot(object)
    def _on_manual_view_range_changed(self, _axes) -> None:
        self._user_adjusted_view = True

    def set_focused_overlay(self, identity: str | None) -> None:
        """Choose which one imported overlay contributes to cursor inspection."""
        if identity is None or any(overlay.identity == identity for overlay in self.scan_overlays):
            self.focused_overlay_id = identity
            self._last_crosshair_text = None

    def _overlay_readout_lines(self, wavelength: float) -> list[str]:
        overlay = next(
            (entry for entry in self.scan_overlays if entry.identity == self.focused_overlay_id and entry.visible), None
        )
        if overlay is None:
            return []
        lookup = overlay.wavelength_lookup
        if (
            lookup.minimum is None
            or lookup.maximum is None
            or wavelength < lookup.minimum
            or wavelength > lookup.maximum
        ):
            return [f"{overlay.label}: out of range"]
        index = _nearest_wavelength_index(lookup, wavelength)
        if index is None:
            return [f"{overlay.label}: n/a"]
        lines = [f"{overlay.label} @ {overlay.scan.wavelengths_nm[index]:.3f} nm:"]
        for detector, value in zip(overlay.scan.detectors, overlay.scan.detector_data[:, index], strict=True):
            lines.append(
                f"Det {detector.value}: {float(value):.2f} dB" if np.isfinite(value) else f"Det {detector.value}: n/a"
            )
        return lines

    def _set_cursor_text(self, text: str) -> None:
        if text != self._last_crosshair_text:
            self.cursor_label.setText(text)
            self._last_crosshair_text = text

    def _on_mouse_moved(self, pos):
        """Updates the crosshair position and label text."""
        if self.plot_widget.sceneBoundingRect().contains(pos):
            mouse_point = self.plot_widget.plotItem.vb.mapSceneToView(pos)
            mouse_x, mouse_y = mouse_point.x(), mouse_point.y()
            wavelengths = self.current_wavelengths
            if wavelengths is not None and len(wavelengths) > 0:
                idx = _nearest_wavelength_index(self._current_wavelength_lookup, mouse_x)
                if idx is None:
                    self._hide_crosshair()
                    return
                x = float(wavelengths[idx])
            else:
                idx = None
                x = mouse_x

            measurement = self.current_measurement
            if measurement is not None:
                if wavelengths is None or len(wavelengths) == 0 or idx is None or not measurement.detectors:
                    self._hide_crosshair()
                    return

                values = measurement.detector_data[:, idx]
                label_lines = [f"λ: {x:.3f} nm"]
                reference = self.reference_measurement
                if reference is not None:
                    label_lines.append("Imported:" if isinstance(measurement, ImportedScan) else "Live:")
                finite_values = []
                for detector, value in zip(measurement.detectors, values, strict=True):
                    if np.isfinite(value):
                        value = float(value)
                        finite_values.append(value)
                        label_lines.append(f"Det {detector.value}: {value:.2f} dB")
                    else:
                        label_lines.append(f"Det {detector.value}: n/a")

                if reference is not None:
                    reference_lookup = self._reference_wavelength_lookup
                    if reference_lookup.finite_indices.size == 0:
                        label_lines.append("Reference: n/a")
                    else:
                        if x < reference_lookup.minimum or x > reference_lookup.maximum:
                            label_lines.append("Reference: out of range")
                        else:
                            reference_idx = _nearest_wavelength_index(reference_lookup, x)
                            assert reference_idx is not None
                            reference_x = float(reference.wavelengths_nm[reference_idx])
                            label_lines.append(f"Reference @ {reference_x:.3f} nm:")
                            for detector, value in zip(
                                reference.detectors,
                                reference.detector_data[:, reference_idx],
                                strict=True,
                            ):
                                if np.isfinite(value):
                                    label_lines.append(f"Det {detector.value}: {float(value):.2f} dB")
                                else:
                                    label_lines.append(f"Det {detector.value}: n/a")

                label_lines.extend(self._overlay_readout_lines(x))

                self.v_line.setPos(x)
                self.v_line.setVisible(True)
                if len(measurement.detectors) == 1 and finite_values:
                    y = finite_values[0]
                    self.h_line.setPos(y)
                    self.h_line.setVisible(True)
                    label_y = y
                else:
                    self.h_line.setVisible(False)
                    label_y = mouse_y
                self._set_cursor_text("\n".join(label_lines))
                self.cursor_label.setPos(x, label_y)
                self.cursor_label.setVisible(True)
                return

            y = mouse_y
            if idx is not None and self.current_powers is not None:
                y = float(self.current_powers[idx])

            self.v_line.setPos(x)
            self.v_line.setVisible(True)
            self.h_line.setVisible(bool(np.isfinite(y)))
            if np.isfinite(y):
                self.h_line.setPos(y)
            cursor_lines = [f"\u03bb: {x:.3f} nm", f"P: {y:.2f} dBm"]
            cursor_lines.extend(self._overlay_readout_lines(x))
            self._set_cursor_text("\n".join(cursor_lines))
            self.cursor_label.setPos(x, y if np.isfinite(y) else mouse_y)
            self.cursor_label.setVisible(True)
        else:
            self._hide_crosshair()

    def _hide_crosshair(self) -> None:
        self.v_line.setVisible(False)
        self.h_line.setVisible(False)
        self.cursor_label.setVisible(False)

    def schedule_matlab_prewarm(self) -> None:
        self.matlab_engine_manager.schedule_prewarm()

    @Slot(object)
    def _on_matlab_engine_state_changed(self, state: MatlabEngineState) -> None:
        if state is MatlabEngineState.STARTING and self._pending_matlab_fig is None:
            self._set_matlab_status("MATLAB Starting…", persistent=True)

    @Slot()
    def _on_matlab_engine_ready(self) -> None:
        self._set_matlab_status("MATLAB Ready")
        self._status_clear_timer.start(self._MATLAB_STATUS_TIMEOUT_MS)
        if self._pending_matlab_fig is not None:
            self._start_pending_matlab_fig()

    @Slot(str)
    def _on_matlab_engine_startup_failed(self, error: str) -> None:
        logger.warning("MATLAB background startup failed: %s", error)
        if self._pending_matlab_fig is None:
            self._set_matlab_status("MATLAB Start Failed")
            self._status_clear_timer.start(self._MATLAB_STATUS_TIMEOUT_MS)
            return
        self._finish_pending_matlab_fig_failure(f"FIG: Could not start MATLAB Engine: {error}")

    def _start_pending_matlab_fig(self) -> None:
        request = self._pending_matlab_fig
        if request is None or self.matlab_engine_manager.state is not MatlabEngineState.READY:
            return

        payload, fig_path, title, xlabel, ylabel = request
        previous_thread = self.matlab_save_thread
        if previous_thread is not None and shiboken6.isValid(previous_thread) and previous_thread.isRunning():
            return

        self._set_matlab_status(f"Saving {Path(fig_path).name}…")
        thread = QThread(self)
        worker = MatlabSaveWorker()
        self.matlab_save_thread = thread
        self.matlab_save_worker = worker
        worker.moveToThread(thread)
        worker.finished_saving.connect(self._handle_matlab_save_finished)
        worker.engine_unhealthy.connect(self._handle_matlab_engine_unhealthy)
        worker.finished_saving.connect(worker.deleteLater)
        worker.engine_unhealthy.connect(worker.deleteLater)
        worker.finished_saving.connect(thread.quit)
        worker.engine_unhealthy.connect(thread.quit)
        thread.started.connect(lambda: logger.info("MATLAB save worker thread started for FIG."))
        thread.finished.connect(
            lambda completed_thread=thread, completed_worker=worker: self._matlab_save_thread_finished.emit(
                completed_thread, completed_worker
            )
        )
        thread.finished.connect(thread.deleteLater)
        thread.start()
        QMetaObject.invokeMethod(
            worker,
            "save_matlab_fig",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, payload),
            Q_ARG(str, fig_path),
            Q_ARG(str, title),
            Q_ARG(str, xlabel),
            Q_ARG(str, ylabel),
            Q_ARG(QWidget, self),
        )

    @Slot(object, object)
    def _handle_matlab_save_thread_finished(self, thread: QThread, worker: MatlabSaveWorker) -> None:
        if self.matlab_save_thread is thread:
            self.matlab_save_thread = None
        if self.matlab_save_worker is worker:
            self.matlab_save_worker = None
        if (
            not self._cleaned_up
            and self._pending_matlab_fig is not None
            and self.matlab_engine_manager.state is MatlabEngineState.READY
        ):
            self._start_pending_matlab_fig()

    @Slot(str)
    def _handle_matlab_engine_unhealthy(self, error: str) -> None:
        self.matlab_engine_manager.engine_unresponsive(RuntimeError(error))
        if self._matlab_fig_retry_count >= 1:
            self._finish_pending_matlab_fig_failure(f"FIG: MATLAB Engine remained unresponsive: {error}")
            return
        self._matlab_fig_retry_count += 1
        logger.warning("Retrying FIG save after MATLAB Engine health check failed: %s", error)
        self._set_matlab_status("Restarting MATLAB…", persistent=True)
        self.matlab_engine_manager.request_engine()

    def _finish_pending_matlab_fig_failure(self, error: str) -> None:
        if self._pending_matlab_fig is None:
            return
        self._pending_matlab_fig = None
        self.error_list.append(error)
        self.pending_saves = max(0, self.pending_saves - 1)
        self._set_matlab_status("Error saving FIG")
        self._check_all_saves_done()

    def _queue_matlab_fig(self, request: tuple[str, str, str, str, str]) -> None:
        if not self.matlab_engine_manager.available:
            self.error_list.append("FIG: MATLAB Engine support is not available.")
            return
        if self._pending_matlab_fig is not None:
            self.error_list.append("FIG: Another figure save is already pending.")
            return

        self._pending_matlab_fig = request
        self._matlab_fig_retry_count = int(self.matlab_engine_manager.state is MatlabEngineState.FAILED)
        self.pending_saves += 1
        state = self.matlab_engine_manager.request_engine()
        if state is MatlabEngineState.READY:
            self._start_pending_matlab_fig()
        elif state is MatlabEngineState.STARTING and self._pending_matlab_fig is not None:
            self._set_matlab_status("Waiting for MATLAB…", persistent=True)
        elif state is MatlabEngineState.UNAVAILABLE:
            self._finish_pending_matlab_fig_failure("FIG: MATLAB Engine support is not available.")

    @Slot(np.ndarray, np.ndarray, float)
    def update_plot(self, x_data: np.ndarray, y_data: np.ndarray, output_power: float | None = None) -> bool:
        # Array-only updates are useful for previews and older callers, but are
        # not exportable as a completed acquisition without its snapshot.
        self.plot_widget.setLabel("left", _GENERIC_SCAN_Y_LABEL)
        self.current_measurement = None
        self.current_imported_scan = None
        self._clear_detector_live_items()
        if self.detector_legend is not None:
            for item in self.detector_plot_items.values():
                self.detector_legend.removeItem(item)
            self.detector_legend.setVisible(bool(self.detector_legend.items))
        try:
            x_data_np = x_data
            y_data_np = y_data

            # --- DETAILED LOGGING AND CHECKING ---
            logger.info(f"PlotWidget.update_plot: Received {len(y_data_np)} y_data points.")
            # LOG MORE POINTS
            log_tail_count = min(100, len(y_data_np))
            if log_tail_count > 0:
                logger.info(
                    f"  PlotWidget y_data (first {min(10, log_tail_count)} of {log_tail_count}):\n{y_data_np[: min(10, log_tail_count)]}"
                )  # Keep first 10 concise
                logger.info(f"  PlotWidget y_data (last {log_tail_count}):\n{y_data_np[-log_tail_count:]}")

            nan_count = np.count_nonzero(np.isnan(y_data_np))
            inf_count = np.count_nonzero(np.isinf(y_data_np))

            if nan_count > 0:
                logger.warning(f"PlotWidget: Full y_data array contains {nan_count} NaN values!")
                nan_indices = np.where(np.isnan(y_data_np))[0]
                logger.warning(f"  NaN indices (first 5): {nan_indices[: min(5, len(nan_indices))]}")
                # Option: Replace NaNs for plotting if desired, e.g.:
                # y_data_np = np.nan_to_num(y_data_np, nan=-100.0) # Replace with a very low dBm value

            if inf_count > 0:
                logger.warning(f"PlotWidget: Full y_data array contains {inf_count} Inf values!")
                inf_indices = np.where(np.isinf(y_data_np))[0]
                logger.warning(f"  Inf indices (first 5): {inf_indices[: min(5, len(inf_indices))]}")
                # Option: Replace Infs for plotting, e.g.:
                # y_data_np = np.nan_to_num(y_data_np, posinf=10.0, neginf=-100.0) # Cap at plausible values
            # --- END DETAILED LOGGING AND CHECKING ---

            if x_data_np.ndim != 1 or y_data_np.ndim != 1 or len(x_data_np) != len(y_data_np):
                logger.error(f"Invalid data shape for plotting. X: {x_data_np.shape}, Y: {y_data_np.shape}")
                self.plot_data_item.setData([], [])
                self.set_plot_title("Invalid Scan Data", color="red")
                self.save_btn.setEnabled(False)
                return False

            logger.debug(f"Updating plot. Points: {len(x_data_np)}. Pout: {output_power}")
            self.current_wavelengths = x_data_np
            self._current_wavelength_lookup = _build_wavelength_lookup(x_data_np)
            self.current_powers = y_data_np
            self.current_output_power = output_power

            # Filter out non-finite points FOR PLOTTING ONLY
            # This prevents PyQtGraph from trying to plot NaNs/Infs which can cause extreme axes
            finite_mask = np.isfinite(x_data_np) & np.isfinite(y_data_np)
            x_plot_data = x_data_np[finite_mask]
            y_plot_data = y_data_np[finite_mask]

            if not np.all(finite_mask):
                logger.info(
                    f"PlotWidget: Plotting {len(y_plot_data)} finite points out of {len(y_data_np)} original y_data points."
                )

            self.plot_data_item.setData(x_plot_data, y_plot_data)

            # Fit the new live trace once. Passing the live item explicitly keeps
            # a frozen reference trace from changing the scan's visible range.
            # Unlike enableAutoRange(), autoRange() does not stay active, so users
            # can zoom and pan normally after each scan update.
            if len(x_plot_data) > 0:
                self.plot_widget.plotItem.vb.autoRange(items=[self.plot_data_item])

            if len(x_data_np) > 0:
                title_text = f"Wavelength Scan ({x_data_np[0]:.1f} - {x_data_np[-1]:.1f} nm)"
                if not np.all(finite_mask):  # If any points were filtered
                    title_text += " (Non-finite data filtered for display)"
                self.set_plot_title(title_text)
            else:
                self.set_plot_title("Wavelength Scan")

            self.save_btn.setEnabled(self.current_measurement is not None)
            self.freeze_btn.setEnabled(True)
            return True
        except Exception:
            logger.exception("Error updating plot")
            self.set_plot_title("Error Updating Plot", color="red")
            self.save_btn.setEnabled(False)
            self.freeze_btn.setEnabled(False)
            return False

    def _clear_detector_live_items(self) -> None:
        for item in self.detector_plot_items.values():
            item.setData([], [])
            item.setVisible(False)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._enable_detector_display_optimizations()

    def _configure_detector_display_item(self, item: pg.PlotDataItem) -> None:
        if self.plot_widget.isVisible() and self.plot_widget.width() > 1:
            item.setClipToView(True)
            item.setDownsampling(auto=True, method="peak")

    def _enable_detector_display_optimizations(self) -> None:
        overlay_items = [item for overlay in self.scan_overlays for item in overlay.items.values()]
        for item in (
            self.plot_data_item,
            *self.detector_plot_items.values(),
            *self.reference_detector_plot_items.values(),
            *overlay_items,
        ):
            self._configure_detector_display_item(item)

    def _detector_item(self, detector: Detector) -> pg.PlotDataItem:
        item = self.detector_plot_items.get(detector)
        if item is None:
            item = self.plot_widget.plot(
                pen=pg.mkPen(self._DETECTOR_COLORS[detector], width=2.0),
                autoDownsampleFactor=_SCAN_AUTO_DOWNSAMPLE_FACTOR,
                skipFiniteCheck=True,
            )
            self._configure_detector_display_item(item)
            item.setZValue(10)
            self.detector_plot_items[detector] = item
        return item

    def _reference_detector_item(self, detector: Detector) -> pg.PlotDataItem:
        item = self.reference_detector_plot_items.get(detector)
        if item is None:
            item = self.plot_widget.plot(
                pen=pg.mkPen(_DETECTOR_REFERENCE_COLOR_BY_ID[detector], width=1.25),
                autoDownsampleFactor=_SCAN_AUTO_DOWNSAMPLE_FACTOR,
                skipFiniteCheck=True,
            )
            self._configure_detector_display_item(item)
            item.setZValue(0)
            self.reference_detector_plot_items[detector] = item
        return item

    def _ensure_detector_legend(self) -> pg.LegendItem:
        if self.detector_legend is None:
            self.detector_legend = self.plot_widget.addLegend(
                pen=pg.mkPen(_PLOT_LEGEND_BORDER, width=1),
                brush=pg.mkBrush(_PLOT_LEGEND_BACKGROUND),
                labelTextColor=_PLOT_LEGEND_TEXT_COLOR,
                labelTextSize=f"{_PLOT_LEGEND_POINT_SIZE}pt",
                verSpacing=2,
            )
            self.detector_legend.sigSampleClicked.connect(
                lambda item, legend=self.detector_legend: _update_plot_legend_hidden_state(legend, item)
            )
        self.detector_legend.setVisible(True)
        return self.detector_legend

    def _refresh_scan_legend(self) -> None:
        legend = self._ensure_detector_legend()
        legend.clear()
        measurement = self.current_measurement
        if measurement is not None:
            prefix = "Imported · " if isinstance(measurement, ImportedScan) else ""
            for detector in measurement.detectors:
                item = self.detector_plot_items.get(detector)
                if item is not None and item.isVisible():
                    legend.addItem(item, f"{prefix}Det {detector.value}")
        for overlay in self.scan_overlays:
            for detector, item in overlay.items.items():
                legend.addItem(item, f"{overlay.label} · Det {detector.value}")
        _configure_plot_legend(legend)
        legend.setVisible(bool(legend.items))

    def add_imported_overlay(self, scan: ImportedScan) -> bool:
        """Add an imported scan as a bounded, independently managed overlay."""
        canonical = scan.source_path.resolve()
        for overlay in self.scan_overlays:
            if overlay.source_path == canonical:
                self.set_overlay_visible(overlay.identity, True)
                return False
        if len(self.scan_overlays) >= MAX_OVERLAY_SCANS:
            answer = QMessageBox.question(
                self,
                "Overlay Limit Reached",
                f"The plot supports up to {MAX_OVERLAY_SCANS} imported overlays. Manage existing overlays now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.manage_overlays()
            return False
        style_index = len(self.scan_overlays)
        items: dict[Detector, pg.PlotDataItem] = {}
        label = scan.source_path.name
        duplicate_label = any(overlay.label == label for overlay in self.scan_overlays)
        if duplicate_label:
            label = f"{scan.source_path.parent.name}/{label}"
            for overlay in self.scan_overlays:
                if overlay.label == scan.source_path.name:
                    overlay.label = f"{overlay.source_path.parent.name}/{overlay.label}"
        for row_index, detector in enumerate(scan.detectors):
            color = self._DETECTOR_COLORS[detector]
            item = self.plot_widget.plot(
                scan.wavelengths_nm,
                scan.detector_data[row_index],
                pen=_overlay_pen(color, style_index),
                autoDownsampleFactor=_SCAN_AUTO_DOWNSAMPLE_FACTOR,
                connect="auto",
            )
            item.setClipToView(True)
            item.setDownsampling(auto=True, method="peak")
            item.setZValue(2 + style_index)
            item.setToolTip(f"{label} · Det {detector.value}")
            items[detector] = item
        overlay = ScanOverlay(
            uuid.uuid4().hex,
            scan,
            canonical,
            label,
            True,
            style_index,
            items,
            _build_wavelength_lookup(scan.wavelengths_nm),
        )
        self.scan_overlays.append(overlay)
        self.focused_overlay_id = overlay.identity
        legend = self._ensure_detector_legend()
        if duplicate_label:
            self._refresh_scan_legend()
        else:
            for detector, item in items.items():
                legend.addItem(item, f"{label} · Det {detector.value}")
                _configure_plot_legend_entry(*legend.items[-1])
            legend.setVisible(bool(legend.items))
        visible_items = [item for entry in self.scan_overlays if entry.visible for item in entry.items.values()]
        visible_items.extend(item for item in self.detector_plot_items.values() if item.isVisible())
        if self.current_measurement is None and self.plot_data_item.isVisible():
            visible_items.append(self.plot_data_item)
        if visible_items and not self._user_adjusted_view:
            self.plot_widget.plotItem.vb.autoRange(items=visible_items)
        return True

    def set_overlay_visible(self, identity: str, visible: bool) -> None:
        for overlay in self.scan_overlays:
            if overlay.identity == identity:
                overlay.visible = visible
                for item in overlay.items.values():
                    item.setVisible(visible)
                    if self.detector_legend is not None:
                        _update_plot_legend_hidden_state(self.detector_legend, item)
                if not visible and self.focused_overlay_id == identity:
                    self.focused_overlay_id = next(
                        (entry.identity for entry in reversed(self.scan_overlays) if entry.visible), None
                    )
                self._last_crosshair_text = None
                return

    def remove_overlay(self, identity: str) -> None:
        for index, overlay in enumerate(self.scan_overlays):
            if overlay.identity == identity:
                for item in overlay.items.values():
                    if self.detector_legend is not None:
                        self.detector_legend.removeItem(item)
                    self.plot_widget.removeItem(item)
                del self.scan_overlays[index]
                if self.focused_overlay_id == identity:
                    self.focused_overlay_id = next(
                        (entry.identity for entry in reversed(self.scan_overlays) if entry.visible), None
                    )
                if self.detector_legend is not None:
                    self.detector_legend.setVisible(bool(self.detector_legend.items))
                return

    def clear_overlays(self) -> None:
        for overlay in self.scan_overlays:
            for item in overlay.items.values():
                self.plot_widget.removeItem(item)
        self.scan_overlays.clear()
        self.focused_overlay_id = None
        self._refresh_scan_legend()

    def manage_overlays(self) -> None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Manage Scan Overlays")
        dialog.setMinimumWidth(440)
        layout = QVBoxLayout(dialog)
        table = QtWidgets.QTableWidget(len(self.scan_overlays), 4, dialog)
        table.setHorizontalHeaderLabels(["Show", "Inspect", "Saved scan", "Provenance"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        focus_group = QtWidgets.QButtonGroup(table)
        focus_group.setExclusive(True)
        for row, overlay in enumerate(self.scan_overlays):
            checkbox = QtWidgets.QCheckBox(table)
            checkbox.setChecked(overlay.visible)
            checkbox.toggled.connect(lambda shown, identity=overlay.identity: self.set_overlay_visible(identity, shown))
            table.setCellWidget(row, 0, checkbox)
            focus_button = QtWidgets.QRadioButton(table)
            focus_button.setChecked(overlay.identity == self.focused_overlay_id)
            focus_button.toggled.connect(
                lambda focused, identity=overlay.identity: self.set_focused_overlay(identity) if focused else None
            )
            focus_group.addButton(focus_button)
            table.setCellWidget(row, 1, focus_button)
            label_item = QtWidgets.QTableWidgetItem(overlay.label)
            label_item.setData(Qt.ItemDataRole.UserRole, overlay.identity)
            table.setItem(row, 2, label_item)
            table.item(row, 2).setToolTip(str(overlay.source_path))
            provenance = "Simulated" if overlay.scan.simulated else "Saved acquisition"
            table.setItem(row, 3, QtWidgets.QTableWidgetItem(provenance))
        layout.addWidget(table)
        buttons = QHBoxLayout()
        remove = QtWidgets.QPushButton("Remove Selected", dialog)
        clear = QtWidgets.QPushButton("Clear Overlays", dialog)
        close = QtWidgets.QPushButton("Close", dialog)
        buttons.addWidget(remove)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        def remove_selected() -> None:
            row = table.currentRow()
            item = table.item(row, 2) if row >= 0 else None
            if item is None:
                return
            self.remove_overlay(item.data(Qt.ItemDataRole.UserRole))
            table.removeRow(row)

        remove.clicked.connect(remove_selected)
        clear.clicked.connect(self.clear_overlays)
        close.clicked.connect(dialog.accept)
        dialog.exec()

    def set_measurement(self, measurement: ScanMeasurement | ImportedScan) -> bool:
        """Display every detector row and retain the completed acquisition."""
        self.plot_widget.setLabel("left", _MEASUREMENT_SCAN_Y_LABEL)
        self.current_measurement = measurement
        self.current_imported_scan = None
        self.current_wavelengths = measurement.wavelengths_nm
        self._current_wavelength_lookup = _build_wavelength_lookup(measurement.wavelengths_nm)
        self.current_powers = measurement.detector_data[0] if measurement.detectors else np.array([])
        self.current_output_power = measurement.final_pout
        self.plot_data_item.setData([], [])
        self._clear_detector_live_items()
        self.save_btn.setEnabled(False)

        finite_live_items: list[pg.PlotDataItem] = []
        wavelengths = measurement.wavelengths_nm
        for row_index, detector in enumerate(measurement.detectors):
            row = measurement.detector_data[row_index]
            finite_mask = np.isfinite(wavelengths) & np.isfinite(row)
            item = self._detector_item(detector)
            item.setData(wavelengths[finite_mask], row[finite_mask])
            item.setVisible(bool(np.any(finite_mask)))
            if np.any(finite_mask):
                finite_live_items.append(item)

        self._refresh_scan_legend()

        if finite_live_items:
            self.plot_widget.plotItem.vb.autoRange(items=finite_live_items)

        if len(wavelengths) > 0:
            title_text = f"Wavelength Scan ({wavelengths[0]:.1f} - {wavelengths[-1]:.1f} nm)"
            if any(not np.all(np.isfinite(row)) for row in measurement.detector_data):
                title_text += " (Non-finite data filtered for display)"
            self.set_plot_title(title_text)
        else:
            self.set_plot_title("Wavelength Scan")

        self.save_btn.setEnabled(isinstance(measurement, ScanMeasurement) and self.pending_saves == 0)
        self.freeze_btn.setEnabled(bool(measurement.detectors))
        return True

    def has_unsaved_acquisition(self) -> bool:
        """Whether the displayed acquisition lacks a successful export."""
        measurement = self.current_measurement
        saved = self._saved_measurement_ref() if self._saved_measurement_ref is not None else None
        return isinstance(measurement, ScanMeasurement) and saved is not measurement

    def _mark_measurement_saved(self, measurement: ScanMeasurement) -> None:
        self._saved_measurement_ref = ref(measurement)

    def _update_save_button_state(self) -> None:
        self.save_btn.setEnabled(isinstance(self.current_measurement, ScanMeasurement) and self.pending_saves == 0)

    def set_imported_scan(self, scan: ImportedScan) -> bool:
        """Display an offline scan through the shared detector plotting path."""
        result = self.set_measurement(scan)
        if result:
            self.current_imported_scan = scan
            provenance = "simulated acquisition" if scan.simulated else "saved acquisition"
            title = f"Imported Scan — {scan.source_path.name} ({provenance})"
            self.set_plot_title(title)
            self.plot_widget.setToolTip(
                f"Imported from: {scan.source_path}\n"
                f"Completed: {scan.completed_at_utc.isoformat()}\n"
                f"Backend: {scan.backend}; {provenance}\n"
                f"Comment: {scan.comment}"
            )
            self.save_btn.setEnabled(False)
        else:
            self.current_imported_scan = None
        return result

    @Slot()
    def clear_plot(self):
        """Clears all traces and resets internal data."""
        self._hide_crosshair()
        self.clear_overlays()
        self.plot_widget.setLabel("left", _GENERIC_SCAN_Y_LABEL)
        # 1. Clear the visual plot items
        self.plot_data_item.setData([], [])
        self.reference_plot_item.setData([], [])
        for item in self.detector_plot_items.values():
            item.setData([], [])
            item.setVisible(False)
        for item in self.reference_detector_plot_items.values():
            item.setData([], [])
            item.setVisible(False)
        if self.detector_legend is not None:
            self.detector_legend.clear()
            self.detector_legend.setVisible(False)

        # 2. Clear internal data storage
        self.current_wavelengths = None
        self.current_powers = None
        self.current_output_power = None
        self.current_measurement = None
        self.current_imported_scan = None
        self.reference_measurement = None
        self._current_wavelength_lookup = _build_wavelength_lookup(None)
        self._reference_wavelength_lookup = _build_wavelength_lookup(None)

        # 3. Reset UI state
        self.set_plot_title("Wavelength Scan (Cleared)")
        self.save_btn.setEnabled(False)
        self.freeze_btn.setEnabled(False)
        self.freeze_btn.setToolTip("Snapshot the current trace to the background for comparison")

        logger.info("Plot cleared.")

    @Slot()
    def freeze_current_trace(self):
        """Copies the current live preview or detector set as one reference snapshot."""
        if self.current_wavelengths is None or self.current_powers is None:
            return

        measurement = self.current_measurement
        if measurement is None:
            self.reference_measurement = None
            self._reference_wavelength_lookup = _build_wavelength_lookup(None)
            self._clear_detector_reference_items()
            self.reference_plot_item.setData(self.current_wavelengths, self.current_powers)
        else:
            self.reference_measurement = measurement
            self._reference_wavelength_lookup = _build_wavelength_lookup(measurement.wavelengths_nm)
            self.reference_plot_item.setData([], [])
            self._clear_detector_reference_items()
            for row_index, detector in enumerate(measurement.detectors):
                row = measurement.detector_data[row_index]
                finite_mask = np.isfinite(measurement.wavelengths_nm) & np.isfinite(row)
                item = self._reference_detector_item(detector)
                item.setData(measurement.wavelengths_nm[finite_mask], row[finite_mask])
                item.setVisible(bool(np.any(finite_mask)))

        # Visual feedback (Optional but nice)
        logger.info("Current trace frozen as reference.")

        # Optional: Change button text to indicate a reference is set?
        self.freeze_btn.setToolTip("Update the frozen reference from the current trace")

    def _clear_detector_reference_items(self) -> None:
        for item in self.reference_detector_plot_items.values():
            item.setData([], [])
            item.setVisible(False)

    @Slot()
    def save_scan_data(self):
        measurement = self.current_measurement
        if self.pending_saves:
            return
        if isinstance(measurement, ImportedScan):
            QMessageBox.information(
                self,
                "Imported Scan",
                "Imported files do not contain all acquisition fields needed to export a scan measurement.",
            )
            return
        if measurement is None or self.current_wavelengths is None or self.current_powers is None:
            QMessageBox.warning(self, "No Data", "No scan data available to save.")
            return
        if Detector.DE_5 in measurement.detectors:
            QMessageBox.warning(
                self,
                "External Detector Export Not Supported",
                "Exporting external/BNC detector data is not yet supported because its unit and SetBNC "
                "configuration are not preserved in the measurement snapshot.",
            )
            return
        if not measurement.detectors:
            QMessageBox.warning(
                self, "No Detector Data", "Cannot export a scan with no wavelength-resolved detector data."
            )
            return

        self.save_btn.setEnabled(False)
        self._set_matlab_status("")
        wavelengths = measurement.wavelengths_nm
        pout = measurement.final_pout
        logger.info(f"Saving scan data. Points: {len(wavelengths)}. Pout: {pout}")
        default_filename = f"scan_{wavelengths[0]:.0f}nm_{wavelengths[-1]:.0f}nm"
        formats = self.settings.scan_export_formats() if self.settings is not None else (True, True, False)
        dialog = ScanExportDialog(self.last_scan_save_dir, default_filename, formats, MATLAB_ENGINE_AVAILABLE, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            logger.info("Save Scan dialog cancelled by user.")
            self._update_save_button_state()
            return
        request = dialog.export_request
        if request is None or not (request.csv or request.mat or request.fig):
            self._update_save_button_state()
            return

        targets = derive_scan_export_targets(
            request.directory / request.base_name,
            include_csv=request.csv,
            include_mat=request.mat,
            include_fig=request.fig,
        )
        if not targets:
            self._update_save_button_state()
            return

        # Accepted valid requests become the next preferences, even if the
        # operator later declines an overwrite prompt.
        self.last_scan_save_dir = request.directory
        if self.settings is not None:
            self.settings.set_scan_export_directory(request.directory)
            self.settings.set_scan_export_formats(csv=request.csv, mat=request.mat, fig=request.fig_preference)
            self.settings.sync()

        conflicts = [path for path in targets.values() if path.exists()]
        if conflicts:
            conflict_list = "\n".join(str(path) for path in conflicts)
            answer = QMessageBox.question(
                self,
                "Overwrite Existing Files?",
                f"The following export targets already exist:\n{conflict_list}\n\nOverwrite all listed files?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                logger.info("Scan export cancelled because overwrite was declined.")
                self._update_save_button_state()
                return

        try:
            export_payload = build_scan_export_v2(measurement, comment=request.comment)
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, "Scan Export Not Supported", str(error))
            self._update_save_button_state()
            return

        self.saved_files_list: list[Path] = []
        self.error_list: list[str] = []
        self.pending_saves = 0
        self._export_measurement = measurement
        self._completion_reported = False

        if request.csv:
            try:
                csv_path = targets["CSV"]
                csv_buffer = io.StringIO()
                np.savetxt(
                    csv_buffer,
                    export_payload.csv_data,
                    delimiter=",",
                    header=export_payload.csv_header,
                    comments="",
                    fmt="%.6f",
                )
                with csv_path.resolve().open("w", encoding="utf-8", newline="") as csv_file:
                    csv_file.write(csv_buffer.getvalue())
                self.saved_files_list.append(csv_path)
                self._mark_measurement_saved(measurement)
                logger.info(f"Saved CSV: {csv_path}")
            except Exception as e:
                self.error_list.append(f"CSV: {e}")
                logger.exception("CSV save failed")

        if request.mat:
            try:
                import scipy.io as sio

                mat_path = targets["MAT"]
                sio.savemat(str(mat_path.resolve()), export_payload.mat_data, do_compression=True)
                self.saved_files_list.append(mat_path)
                self._mark_measurement_saved(measurement)
                logger.info(f"Saved MAT: {mat_path}")
            except Exception as e:
                self.error_list.append(f"MAT: {e}")
                logger.exception("MAT save failed")

        if request.fig:
            fig_path = targets["FIG"]
            title_str_matlab = f"Scan {wavelengths[0]:.1f} - {wavelengths[-1]:.1f} nm"
            if pout is not None:
                title_str_matlab += f" (Pout: {pout:.2f} dBm)"
            self._queue_matlab_fig(
                (
                    build_matlab_fig_payload(measurement),
                    str(fig_path.resolve()),
                    title_str_matlab,
                    "Wavelength (nm)",
                    "Transfer function (dB)",
                )
            )

        if self.pending_saves == 0:
            self._check_all_saves_done()

    @Slot(str, bool, str)
    def _handle_matlab_save_finished(self, filetype: str, success: bool, message_or_filename: str):
        self._pending_matlab_fig = None
        self.pending_saves -= 1
        if success:
            # ... (append to saved_files_list, update status_label) ...
            logger.info(f"Successfully saved {filetype}: {message_or_filename}")
            self.saved_files_list.append(message_or_filename)
            # FIG is a useful MATLAB plot, but it does not preserve the
            # schema-v2 measurement needed to count the acquisition as saved.
            self._set_matlab_status(f"{Path(message_or_filename).name} saved.")
        else:
            # ... (append to error_list, update status_label) ...
            logger.error(f"Failed to save {filetype}: {message_or_filename}")
            self.error_list.append(f"{filetype.upper()}: {message_or_filename}")
            self._set_matlab_status(f"Error saving {filetype}.")

        # QThread.finished clears the Python references before the thread wrapper is deleted.

        self._check_all_saves_done()

    def _check_all_saves_done(self):
        if self.pending_saves != 0:
            return
        self._update_save_button_state()
        self._export_measurement = None
        if not self.matlab_status_label.text() or "Saving" not in self.matlab_status_label.text():
            self._status_clear_timer.start(self._STATUS_CLEAR_TIMEOUT_MS)
        if getattr(self, "_completion_reported", False):
            return
        self._completion_reported = True

        saved_files_str_list = [str(path) for path in self.saved_files_list]
        if not self.error_list and saved_files_str_list:
            QMessageBox.information(
                self,
                "Save Successful",
                "Scan data saved successfully to:\n" + "\n".join(saved_files_str_list),
            )
        elif self.error_list:
            QMessageBox.warning(
                self,
                "Save Issues",
                "Some files may have saved:\n"
                + "\n".join(saved_files_str_list)
                + "\n\nErrors occurred:\n"
                + "\n".join(self.error_list),
            )
        self.saved_files_list = []
        self.error_list = []

    @Slot()
    def _clear_matlab_status(self) -> None:
        self._set_matlab_status("")

    def get_matlab_engine(self) -> matlab.engine.MatlabEngine | None:
        return self.matlab_engine_manager.engine

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._crosshair_refresh_timer.stop()
        self._latest_mouse_position = None
        logger.debug("PlotWidget cleanup: Cleaning up resources.")
        self._status_clear_timer.stop()
        thread = self.matlab_save_thread
        worker = self.matlab_save_worker
        try:
            if thread is not None and shiboken6.isValid(thread) and thread.isRunning():
                logger.info("PlotWidget close: Stopping active MATLAB save worker thread.")
                if worker is not None and shiboken6.isValid(worker):
                    QMetaObject.invokeMethod(
                        worker,
                        "stop_worker",
                        Qt.ConnectionType.QueuedConnection,
                    )
                thread.quit()
                if not thread.wait(self._THREAD_WAIT_TIMEOUT_MS) and shiboken6.isValid(thread) and thread.isRunning():
                    logger.warning("MATLAB save thread did not quit gracefully on PlotWidget close. Terminating.")
                    thread.terminate()
                    thread.wait()
        except Exception:
            logger.exception("Could not fully stop the MATLAB save thread during PlotWidget cleanup.")
        finally:
            if self.matlab_save_thread is thread:
                self.matlab_save_thread = None
            if self.matlab_save_worker is worker:
                self.matlab_save_worker = None
            self.matlab_engine_manager.shutdown()

    def closeEvent(self, event: QtGui.QCloseEvent):
        self.cleanup()
        super().closeEvent(event)


try:
    from ui.control_panel import ScanSettings
except ImportError:
    logger.error("ScanSettings class not found. Ensure it's defined or imported correctly.")

    class ScanSettings:
        pass
