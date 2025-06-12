"""Widgets for plotting and data visualization in the IOPanel application.

This module contains:
- MatlabSaveWorker: Worker for saving plots to MATLAB format
- HistogramWidget: Real-time power monitoring display
- PlotWidget: Main plotting widget for scan results
"""

import json
import logging
import os
import time

import numpy as np
import pyqtgraph as pg
import scipy.io as sio
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import (
    Q_ARG,
    QMetaObject,
    QMutex,
    QMutexLocker,
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    import matlab.engine

    MATLAB_ENGINE_AVAILABLE = True
except ImportError:
    logging.getLogger("LabApp.gui_panels").warning(
        "MATLAB Engine for Python not found. Saving to .fig format will be disabled."
    )
    MATLAB_ENGINE_AVAILABLE = False
except Exception as e:
    logging.getLogger("LabApp.gui_panels").error(
        f"Error importing MATLAB Engine: {e}. Saving to .fig format will be disabled."
    )
    MATLAB_ENGINE_AVAILABLE = False

logger = logging.getLogger("LabApp.plot_widgets")


###############################################################################
# MatlabSaveWorker
###############################################################################
class MatlabSaveWorker(QObject):
    """Worker for saving scan data to MATLAB .fig format.

    This worker runs in a separate thread to save plot data to MATLAB .fig files
    without blocking the main UI thread.

    Attributes:
        finished_saving: Signal emitted when save operation completes
        _is_running: Flag indicating if worker is active
        matlab_eng_local_for_quit: Local MATLAB engine instance if needed
    """

    finished_saving = Signal(str, bool, str)  # Emits: filetype, success, message_or_filename

    def __init__(self, parent: QObject | None = None):  # parent is PlotWidget
        super().__init__(parent)
        self._is_running = True
        self.matlab_eng_local_for_quit: matlab.engine.MatlabEngine | None = None

    finished_saving = Signal(str, bool, str)  # Emits: filetype, success, message_or_filename

    def __init__(self, parent: QObject | None = None):  # parent is PlotWidget
        super().__init__(parent)
        self._is_running = True
        self.matlab_eng_local_for_quit: matlab.engine.MatlabEngine | None = None

    # Slot signature changes: last arg is QWidget (or a more specific QObject if PlotWidget is registered)
    @Slot(str, str, str, str, str, str, str, float, "QWidget*")  # Pass PlotWidget as QWidget*
    def save_matlab_fig(
        self,
        wavelengths_json_str: str,
        powers_json_str: str,
        fig_filename: str,
        title_str: str,
        xlabel_str: str,
        ylabel_str: str,
        grid_on_str: str,
        pout_value: float,
        plot_widget_ptr: QWidget | None,  # Technically PlotWidget
    ):
        """Saves scan data to MATLAB .fig format.

        Args:
            wavelengths_json_str: JSON string of wavelengths array
            powers_json_str: JSON string of power values array
            fig_filename: Output filename for .fig file
            title_str: Plot title
            xlabel_str: X-axis label
            ylabel_str: Y-axis label
            grid_on_str: Grid visibility ('on' or 'off')
            pout_value: Output power value
            plot_widget_ptr: Reference to PlotWidget instance

        Emits:
            finished_saving: Signal with save operation result
        """
        if not self._is_running:
            logger.info("MatlabSaveWorker: Save FIG cancelled (worker not running).")
            self.finished_saving.emit("fig", False, "Save cancelled by user.")
            return

        if not MATLAB_ENGINE_AVAILABLE:
            logger.warning("MatlabSaveWorker: MATLAB Engine not available. Cannot save .fig.")
            self.finished_saving.emit("fig", False, "MATLAB Engine not available.")
            return

        wavelengths_list = []
        powers_list = []
        try:
            wavelengths_list = json.loads(wavelengths_json_str)
            powers_list = json.loads(powers_json_str)
            if not isinstance(wavelengths_list, list) or not all(isinstance(x, int | float) for x in wavelengths_list):
                raise ValueError("Decoded wavelengths is not a list of numbers.")
            if not isinstance(powers_list, list) or not all(isinstance(x, int | float) for x in powers_list):
                raise ValueError("Decoded powers is not a list of numbers.")
        except (json.JSONDecodeError, ValueError) as e:
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
        self.matlab_eng_local_for_quit = None

        try:
            if eng_to_use is None:
                if not self._is_running:  # Check before slow operation
                    logger.info("MatlabSaveWorker: Save FIG cancelled before local engine start.")
                    self.finished_saving.emit("fig", False, "Save cancelled by user.")
                    return
                logger.info("MatlabSaveWorker: No shared engine from PlotWidget. Starting MATLAB engine locally...")
                eng_to_use = matlab.engine.start_matlab()
                self.matlab_eng_local_for_quit = eng_to_use
                logger.info("MatlabSaveWorker: MATLAB engine started locally.")
            else:
                logger.info("MatlabSaveWorker: Using shared MATLAB engine instance from PlotWidget.")

            if not self._is_running:
                logger.info("MatlabSaveWorker: Save FIG cancelled after engine consideration.")
                self.finished_saving.emit("fig", False, "Save cancelled by user.")
                if self.matlab_eng_local_for_quit:  # Quit if we started it
                    self.matlab_eng_local_for_quit.quit()
                    self.matlab_eng_local_for_quit = None
                return

            if eng_to_use is None:  # Should not happen if logic above is correct
                raise RuntimeError("MATLAB engine could not be obtained.")

            wavelengths_mat = matlab.double(wavelengths_list)
            powers_mat = matlab.double(powers_list)

            h_fig = eng_to_use.figure(nargout=0)
            eng_to_use.plot(wavelengths_mat, powers_mat, nargout=0)
            eng_to_use.xlabel(xlabel_str, nargout=0)
            eng_to_use.ylabel(ylabel_str, nargout=0)
            eng_to_use.title(title_str, nargout=0)
            eng_to_use.grid(grid_on_str, nargout=0)
            eng_to_use.savefig(fig_filename, nargout=0)

            try:
                logger.debug(f"MatlabSaveWorker: Attempting to close current MATLAB figure (handle: {h_fig})...")
                # Option 1: Close the specific figure using its handle
                eng_to_use.close("all", nargout=0)
                # Option 2: Close the "current" figure (gcf might change if other ops happen)
                # current_fig_handle = eng_to_use.gcf(nargout=1)
                # eng_to_use.close(current_fig_handle, nargout=0)
                # Option 3: Close all figures (broader)
                # eng_to_use.close('all', nargout=0)
                logger.info("MatlabSaveWorker: MATLAB figure closed.")
            except Exception as e_close:
                logger.warning(f"MatlabSaveWorker: Could not close MATLAB figure: {e_close}")

            logger.info(f"MatlabSaveWorker: Saved plot to FIG: {fig_filename}")
            self.finished_saving.emit("fig", True, fig_filename)

        except ImportError:
            error_msg = "FIG: MATLAB Engine for Python not installed or found (runtime check)."
            logger.error(error_msg)
            self.finished_saving.emit("fig", False, error_msg)
        except Exception as e:
            error_msg = f"FIG: MATLAB export failed: {e}"
            logger.error(error_msg, exc_info=True)
            self.finished_saving.emit("fig", False, error_msg)
        finally:
            if self.matlab_eng_local_for_quit:
                try:
                    logger.info("MatlabSaveWorker: Quitting locally started MATLAB engine...")
                    self.matlab_eng_local_for_quit.quit()
                    logger.info("MatlabSaveWorker: Locally started MATLAB engine quit.")
                except Exception as e_quit:
                    logger.error(f"MatlabSaveWorker: Error quitting locally started MATLAB engine: {e_quit}")
                finally:
                    self.matlab_eng_local_for_quit = None

    @Slot()
    def stop_worker(self):
        logger.debug("MatlabSaveWorker: Stop requested.")
        self._is_running = False


# =============================================================================
# Histogram Widget (using PyQtGraph)
# =============================================================================
class HistogramWidget(QtWidgets.QWidget):
    """Real-time power monitoring display as histogram.

    This widget displays detector power levels in real-time using a bar chart
    with max value indicators and text annotations.

    Attributes:
        detector_keys: List of detector identifiers
        current_values: Current power values for each detector
        max_values: Maximum recorded power values
        bars: Bar graph item
        max_lines: Horizontal lines indicating max values
        max_texts: Text annotations for max values
        current_texts: Text annotations for current values
    """

    _UPDATE_INTERVAL_MS = 50
    _DEFAULT_Y_RANGE = (-70, 10)
    _LOW_SIGNAL_FLOOR = -100.0
    _HIGH_SIGNAL_CEILING = 10.0

    _UPDATE_INTERVAL_MS = 50
    _DEFAULT_Y_RANGE = (-70, 10)
    _LOW_SIGNAL_FLOOR = -100.0
    _HIGH_SIGNAL_CEILING = 10.0

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
        self.title_size = 14  # Title font size
        self.value_text_font_size = 15  # Specific size for value annotations on bars
        self.text_offset = 1.0  # Offset for text from the value line

        self.max_pen = pg.mkPen("#e41a1c", width=1.5, style=QtCore.Qt.PenStyle.DashLine)
        self.bar_brush = pg.mkBrush("#a6cee3")
        self.bar_pen = pg.mkPen("#1f78b4")
        self.max_text_color = pg.mkColor("#e41a1c")
        self.current_text_color = pg.mkColor("#555555")  # Dark grey for current values
        self.text_font = QFont("Segoe UI", self.value_text_font_size)  # Font for value annotations

        # UI Elements
        self.layout = QtWidgets.QVBoxLayout(self)
        self.plot_widget = pg.PlotWidget(background="w")
        self.layout.addWidget(self.plot_widget)

        # Plot items
        self.bars: pg.BarGraphItem | None = None
        self.max_lines: list[pg.PlotCurveItem] = []
        # Initialize text item lists (filled in _create_plot_items)
        self.max_texts: list[pg.TextItem | None] = []
        self.current_texts: list[pg.TextItem | None] = []

        self._configure_plot()  # Sets up axes, title, grid
        self._create_plot_items()  # Creates bars, lines, and text items

        self.reset_btn = QtWidgets.QPushButton("Reset Axes")
        self.reset_btn.clicked.connect(self.reset_maxima)
        self.layout.addWidget(self.reset_btn)

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
        label_style = {"color": "k", "font-size": f"{self.font_size}pt"}
        title_style = {"color": "k", "size": f"{self.title_size}pt"}

        x_axis = self.plot_widget.getAxis("bottom")
        x_axis.setLabel(text="Detector", **label_style)
        x_axis.setTickFont(QFont("Segoe UI", self.font_size - 1))  # Slightly smaller ticks
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
        """Resets the maximum recorded values to current values."""
        t_start = time.perf_counter()
        logger.info("Resetting histogram: current values to 0, max_values to -infinity.")

        self.current_values.fill(0.0)
        self.max_values.fill(-np.inf)

        if self.bars:
            self.bars.setOpts(height=self.current_values)
        else:
            logger.warning("Reset Axes: self.bars is None, cannot set heights.")

        for i in range(self.num_bars):
            x_center = i
            current_val_at_reset = 0.0

            if i < len(self.current_texts) and self.current_texts[i] is not None:
                text_item_current = self.current_texts[i]
                show_text_at_zero = np.isfinite(current_val_at_reset) and current_val_at_reset > -90

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
        logger.debug(f"Reset Axes execution took: {(t_end - t_start) * 1000:.3f} ms")

    @Slot(dict)
    def schedule_update(self, power_data: dict):
        """Schedules an update with new power data.

        Args:
            power_data: Dictionary containing detector power values
        """
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
            text_y_position = max_val - self.text_offset  # Position it slightly above
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
            text_y_position = current_val + self.text_offset  # Position it slightly below
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
    """Main plotting widget for scan results.

    Displays wavelength scan data and provides saving functionality in multiple
    formats (CSV, MAT, PNG, SVG, FIG).

    Attributes:
        shared_settings: Application scan settings
        current_wavelengths: Array of wavelength values
        current_powers: Array of power values
        current_output_power: Output power value
        plot_data_item: Main plot line item
        save_btn: Button for saving scan data
        matlab_status_label: Status indicator for MATLAB operations
    """

    _THREAD_WAIT_TIMEOUT_MS = 2000
    _MATLAB_STATUS_TIMEOUT_MS = 2000

    _THREAD_WAIT_TIMEOUT_MS = 2000
    _MATLAB_STATUS_TIMEOUT_MS = 2000

    # Signal to update UI from worker, e.g., re-enable button, show status
    matlab_save_status_update = Signal(str)  # Message for status bar or dialog

    def __init__(self, shared_settings, parent: QWidget | None = None):
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

        # --- Worker Thread Setup for MATLAB Saving ---
        # We'll create the thread and worker on-demand when saving to .fig
        self.matlab_save_thread: QThread | None = None
        self.matlab_save_worker: MatlabSaveWorker | None = None
        # --- End Worker Thread Setup ---

        self.matlab_engine_instance: matlab.engine.MatlabEngine | None = None
        self.matlab_engine_lock = QMutex()
        self.is_matlab_engine_starting: bool = False

        # --- UI Setup ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.plot_widget = pg.PlotWidget(background="w")  # PyQtGraph PlotWidget
        self.plot_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
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

        self.matlab_status_label = QLabel("")  # For showing "Saving .fig..."
        self.matlab_status_label.setAlignment(Qt.AlignmentFlag.AlignRight)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.matlab_status_label)  # Add it to the layout
        button_layout.addStretch(1)
        button_layout.addWidget(self.save_btn)
        layout.addLayout(button_layout)

        logger.info("PlotWidget (PyQtGraph) initialized")

    def _ensure_matlab_engine_started(self) -> bool:
        """
        Ensures the MATLAB engine is started. Returns True if ready, False on error or if unavailable.
        This method will block while the engine starts.
        """
        with QMutexLocker(self.matlab_engine_lock):
            if self.matlab_engine_instance:
                # Ping the engine to check if it's alive
                try:
                    self.matlab_engine_instance.eval("1;", nargout=0)
                    logger.info("Shared MATLAB engine is alive.")
                    return True
                except Exception as e:
                    logger.warning(f"Shared MATLAB engine seems unresponsive ({e}). Attempting to restart.")
                    try:
                        self.matlab_engine_instance.quit()
                    except Exception as quit_e:
                        # It's good practice to log that the quit itself failed.
                        logger.error(f"Failed to cleanly quit the unresponsive MATLAB engine: {quit_e}")
                        # The 'pass' is still appropriate here because the goal is to continue cleanup.
                        pass
                    self.matlab_engine_instance = None

            if not MATLAB_ENGINE_AVAILABLE:
                logger.warning("MATLAB Engine support is not available.")
                self.matlab_status_label.setText("MATLAB N/A")
                return False

            if self.is_matlab_engine_starting:
                logger.info("MATLAB engine is already in the process of starting.")
                # This case should ideally wait or be handled by a callback,
                # but for now, we'll let the caller decide.
                # For save_scan_data, it might mean the button is temporarily disabled.
                return False  # Indicate not ready yet

            self.is_matlab_engine_starting = True
            self.matlab_status_label.setText("Starting MATLAB Engine...")
            QApplication.processEvents()  # Update UI

            try:
                logger.info("PlotWidget: Starting shared MATLAB engine...")
                self.matlab_engine_instance = matlab.engine.start_matlab()
                logger.info("PlotWidget: Shared MATLAB engine started successfully.")
                self.matlab_status_label.setText("MATLAB Ready.")
                QTimer.singleShot(
                    self._MATLAB_STATUS_TIMEOUT_MS,
                    lambda: self.matlab_status_label.setText(""),
                )
                return True
            except Exception as e:
                logger.error(
                    f"PlotWidget: Failed to start shared MATLAB engine: {e}",
                    exc_info=True,
                )
                self.matlab_engine_instance = None
                self.matlab_status_label.setText("MATLAB Start Failed!")
                QMessageBox.critical(self, "MATLAB Error", f"Could not start MATLAB Engine: {e}")
                return False
            finally:
                self.is_matlab_engine_starting = False

    @Slot(np.ndarray, np.ndarray, float)
    def update_plot(self, x_data: np.ndarray, y_data: np.ndarray, output_power: float | None = None):
        """Updates the plot with new scan data.

        Args:
            x_data: Array of wavelength values (x-axis)
            y_data: Array of power values (y-axis)
            output_power: Optional output power value
        """
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
                self.plot_widget.setTitle("Invalid Scan Data", color="red", size="11pt")
                self.save_btn.setEnabled(False)
                return

            logger.debug(f"Updating plot. Points: {len(x_data_np)}. Pout: {output_power}")
            self.current_wavelengths = x_data_np
            self.current_powers = y_data_np
            self.current_output_power = output_power

            # Filter out non-finite points FOR PLOTTING ONLY
            # This prevents PyQtGraph from trying to plot NaNs/Infs which can cause extreme axes
            finite_mask = np.isfinite(y_data_np)
            x_plot_data = x_data_np[finite_mask]
            y_plot_data = y_data_np[finite_mask]

            if not np.all(finite_mask):
                logger.info(
                    f"PlotWidget: Plotting {len(y_plot_data)} finite points out of {len(y_data_np)} original y_data points."
                )

            self.plot_data_item.setData(x_plot_data, y_plot_data)

            if len(x_data_np) > 0:
                title_text = f"Wavelength Scan ({x_data_np[0]:.1f} - {x_data_np[-1]:.1f} nm)"
                if not np.all(finite_mask):  # If any points were filtered
                    title_text += " (Non-finite data filtered for display)"
                self.plot_widget.setTitle(title_text, color="black", size="11pt")
            else:
                self.plot_widget.setTitle("Wavelength Scan", color="black", size="11pt")

            self.save_btn.setEnabled(True)
        except Exception as e:
            logger.error(f"Error updating plot: {e}", exc_info=True)
            self.plot_widget.setTitle("Error Updating Plot", color="red", size="11pt")
            self.save_btn.setEnabled(False)

    @Slot()
    def save_scan_data(self):
        """Saves scan data to multiple formats based on user selection."""
        if self.current_wavelengths is None or self.current_powers is None:
            QMessageBox.warning(self, "No Data", "No scan data available to save.")
            return

        # Disable button during save to prevent multiple clicks
        self.save_btn.setEnabled(False)  # Disable button early
        self.matlab_status_label.setText("")  # Clear previous status

        # Retrieve data (already stored in self.current_wavelengths etc.)
        wavelengths, powers, pout = (
            self.current_wavelengths,
            self.current_powers,
            self.current_output_power,
        )
        logger.info(f"Saving scan data. Points: {len(wavelengths)}. Pout: {pout}")

        if pout is not None:
            data_to_save = np.column_stack((wavelengths, np.full_like(wavelengths, pout), powers))
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
        # Added .fig to default filters if MATLAB is available
        file_filters_list = [
            "CSV File (*.csv)",
            "MAT File (*.mat)",
            "PNG Image (*.png)",
            "SVG Image (*.svg)",
        ]
        if MATLAB_ENGINE_AVAILABLE:
            file_filters_list.append("FIG File (*.fig)")
        file_filters_list.append("All Files (*)")
        file_filters = ";;".join(file_filters_list)

        selected_path_with_ext, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Scan Results As (Specify Base Name)",
            default_filename,
            file_filters,
            # options=QFileDialog.Option.DontUseNativeDialog # Keep or remove based on preference
        )

        if not selected_path_with_ext:
            logger.info("Save cancelled by user.")
            self.save_btn.setEnabled(True)
            return

        # Get the base filename without any extension
        base_filename = os.path.splitext(selected_path_with_ext)[0]

        self.saved_files_list: list[str] = []
        self.error_list: list[str] = []
        self.pending_saves = 0  # Counter for async operations

        # --- Save CSV (Synchronous) ---
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
            self.saved_files_list.append(csv_filename)
            logger.info(f"Saved CSV: {csv_filename}")
        except Exception as e:
            self.error_list.append(f"CSV: {e}")
            logger.error(f"CSV save failed: {e}", exc_info=True)

        # --- Save MAT (Synchronous) ---
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
            self.saved_files_list.append(mat_filename)
            logger.info(f"Saved MAT: {mat_filename}")
        except Exception as e:
            self.error_list.append(f"MAT: {e}")
            logger.error(f"MAT save failed: {e}", exc_info=True)

        # --- Save FIG ---
        if MATLAB_ENGINE_AVAILABLE:
            if not self._ensure_matlab_engine_started():
                # ... (handle engine start failure) ...
                if not any("FIG:" in err for err in self.error_list):
                    self.error_list.append("FIG: Save skipped (MATLAB Engine failed to start/unavailable).")
            else:
                self.pending_saves += 1
                fig_filename = f"{base_filename}.fig"
                self.matlab_status_label.setText(f"Queueing {os.path.basename(fig_filename)} save...")

                # --- Manage previous thread/worker instance ---
                # If a thread object exists, we assume it's from a previous operation.
                # We can't safely interact with it if deleteLater might have been called.
                # The connections made previously (finished -> deleteLater) should handle its cleanup.
                # We just need to ensure we are creating NEW ones for this operation.
                # Setting them to None here helps make it clear we are done with the old Python vars.
                if self.matlab_save_thread is not None:
                    # We don't need to explicitly quit/wait here if finished->deleteLater is robust.
                    # The main issue is accessing a potentially deleted C++ object.
                    # By creating new ones, we avoid this.
                    logger.debug("Previous matlab_save_thread detected. Assuming it will self-clean via deleteLater.")
                # ---

                self.matlab_save_thread = QThread(self)  # QThread can have a parent
                self.matlab_save_worker = MatlabSaveWorker()  # NO PARENT before moveToThread
                self.matlab_save_worker.moveToThread(self.matlab_save_thread)

                # Connect signals for the NEW worker and thread
                self.matlab_save_worker.finished_saving.connect(self._handle_matlab_save_finished)
                self.matlab_save_thread.started.connect(
                    lambda: logger.info("MATLAB save worker thread started for FIG.")
                )
                # Ensure proper cleanup when the thread finishes
                self.matlab_save_thread.finished.connect(self.matlab_save_thread.deleteLater)
                self.matlab_save_thread.finished.connect(self.matlab_save_worker.deleteLater)
                # Optional: Disconnect old signals if you were reusing worker/thread objects,
                # but since we are creating new ones, this is not strictly necessary.

                self.matlab_save_thread.start()

                title_str_matlab = f"Scan {wavelengths[0]:.1f} - {wavelengths[-1]:.1f} nm"
                if pout is not None:
                    title_str_matlab += f" (Pout: {pout:.2f} dBm)"
                wavelengths_json = json.dumps(wavelengths.tolist())
                powers_json = json.dumps(powers.tolist())
                pout_for_arg = pout if pout is not None else float("nan")

                QMetaObject.invokeMethod(
                    self.matlab_save_worker,
                    "save_matlab_fig",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, wavelengths_json),
                    Q_ARG(str, powers_json),
                    Q_ARG(str, fig_filename),
                    Q_ARG(str, title_str_matlab),
                    Q_ARG(str, "Wavelength (nm)"),
                    Q_ARG(str, "Power (dBm)"),
                    Q_ARG(str, "on"),
                    Q_ARG(float, pout_for_arg),
                    Q_ARG(QWidget, self),
                )

        else:  # MATLAB_ENGINE_AVAILABLE is False (compile-time check)
            logger.info("Skipping .fig save: MATLAB Engine support not compiled in or available.")
            # No error_list addition here, it's a known unavailability

        # If no asynchronous saves were started, finalize now.
        if self.pending_saves == 0:
            self._check_all_saves_done()

    @Slot(str, bool, str)
    def _handle_matlab_save_finished(self, filetype: str, success: bool, message_or_filename: str):
        self.pending_saves -= 1
        if success:
            # ... (append to saved_files_list, update status_label) ...
            logger.info(f"Successfully saved {filetype}: {message_or_filename}")
            self.saved_files_list.append(message_or_filename)
            self.matlab_status_label.setText(f"{os.path.basename(message_or_filename)} saved.")
        else:
            # ... (append to error_list, update status_label) ...
            logger.error(f"Failed to save {filetype}: {message_or_filename}")
            self.error_list.append(f"{filetype.upper()}: {message_or_filename}")
            self.matlab_status_label.setText(f"Error saving {filetype}.")

        # The thread and worker are already connected to deleteLater via their finished signals.
        # We don't need to explicitly quit/delete them here again.
        # Setting the Python attributes to None after they are "done" for this operation
        # can be a good practice to signal they shouldn't be reused directly.
        # However, the next call to save_scan_data will overwrite them anyway.

        self._check_all_saves_done()

    def _check_all_saves_done(self):
        if self.pending_saves == 0:
            self.save_btn.setEnabled(True)
            # Keep status label from MATLAB save if it was the last one, or clear if only sync saves.
            if not self.matlab_status_label.text() or "Saving" not in self.matlab_status_label.text():
                QTimer.singleShot(3000, lambda: self.matlab_status_label.setText(""))

            if not self.error_list and self.saved_files_list:  # Only show success if something was saved
                QMessageBox.information(
                    self,
                    "Save Successful",
                    "Scan data saved successfully to:\n" + "\n".join(self.saved_files_list),
                )
            elif self.error_list:
                QMessageBox.warning(
                    self,
                    "Save Issues",
                    "Some files may have saved:\n"
                    + "\n".join(self.saved_files_list)
                    + "\n\nErrors occurred:\n"
                    + "\n".join(self.error_list),
                )

            self.saved_files_list = []
            self.error_list = []

    def get_matlab_engine(self) -> matlab.engine.MatlabEngine | None:
        with QMutexLocker(self.matlab_engine_lock):  # Protect access
            return self.matlab_engine_instance

    def cleanup(self):
        """Cleans up resources and stops background threads."""
        logger.debug("PlotWidget cleanup: Cleaning up resources.")
        # Stop any ongoing save worker thread
        if self.matlab_save_thread and self.matlab_save_thread.isRunning():
            logger.info("PlotWidget close: Stopping active MATLAB save worker thread.")
            if self.matlab_save_worker:
                QMetaObject.invokeMethod(
                    self.matlab_save_worker,
                    "stop_worker",
                    Qt.ConnectionType.QueuedConnection,
                )
            self.matlab_save_thread.quit()
            if not self.matlab_save_thread.wait(self._THREAD_WAIT_TIMEOUT_MS):  # Wait for graceful exit
                logger.warning("MATLAB save thread did not quit gracefully on PlotWidget close. Terminating.")
                self.matlab_save_thread.terminate()
                self.matlab_save_thread.wait()  # Wait for termination

        # Shut down the shared MATLAB engine instance
        if self.matlab_engine_instance:
            logger.info("PlotWidget close: Quitting shared MATLAB engine instance.")
            try:
                with QMutexLocker(self.matlab_engine_lock):  # Protect access
                    if self.matlab_engine_instance:
                        self.matlab_engine_instance.quit()
                        self.matlab_engine_instance = None
                        logger.info("PlotWidget: Shared MATLAB engine quit successfully.")
            except Exception as e:
                logger.error(
                    f"PlotWidget: Error quitting shared MATLAB engine: {e}",
                    exc_info=True,
                )

    def closeEvent(self, event: QtGui.QCloseEvent):
        self.cleanup()
        super().closeEvent(event)


try:
    from ui.control_panel import ScanSettings
except ImportError:
    logger.error("ScanSettings class not found. Ensure it's defined or imported correctly.")

    class ScanSettings:
        pass
