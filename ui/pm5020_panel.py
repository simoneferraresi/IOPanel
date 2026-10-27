"""Standalone, independently operated PM5020 monitoring panel."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import override

import pyqtgraph as pg
from PySide6.QtCore import QLocale, QSignalBlocker, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDoubleValidator, QFont, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app_settings import AppSettings
from hardware.pm5020_types import ChannelConfig, PM5020State, PowerChannel, PowerReading
from logic.device_lease_registry import DeviceLeaseRegistry
from logic.pm5020_monitor import DISPLAY_UNITS, ConfigurationRequest, PM5020MonitorWorker, convert_power, format_power
from logic.task_runner import TaskRunner
from ui.typography import make_font, pyqtgraph_title_style, style_group_box_title

logger = logging.getLogger("LabApp.pm5020_panel")

HISTORY_SECONDS = (10, 30, 60, 300, 600)
MAX_HISTORY_POINTS = 2400
POWER_RANGE_SCALES = {"W": 1.0, "mW": 1e-3, "µW": 1e-6, "nW": 1e-9}


# Follow the existing CT400 monitor (blue) and Stop Alignment (red) controls.
PM5020_ACTION_BUTTON_STYLE = """
QPushButton#pm5020StartButton { background-color: #1976d2; color: white; }
QPushButton#pm5020StartButton:disabled { background-color: #e0e0e0; color: #888888; }
QPushButton#pm5020StopButton { background-color: #c62828; color: white; }
QPushButton#pm5020StopButton:disabled { background-color: #e0e0e0; color: #888888; }
"""


class PM5020ChannelControls(QGroupBox):
    """Channel settings and display; raw powers are always stored in watts."""

    def __init__(self, channel: PowerChannel, *, default_wavelength_nm: float, default_unit: str):
        super().__init__(f"CH{channel.value} — Optical Power")
        style_group_box_title(self)
        self.setTitle("")
        self.channel = channel
        self._rows: deque[tuple[float, float]] = deque(maxlen=MAX_HISTORY_POINTS)
        layout = QVBoxLayout(self)
        layout.setSpacing(5)

        self.enabled = QCheckBox(f"CH{channel.value}")
        self.enabled.setObjectName(f"pm5020Ch{channel.value}Enabled")
        self.enabled.setChecked(True)
        self.enabled.setFont(make_font("sans", 20, QFont.Weight.Bold))
        self.enabled.setToolTip("Include this sensor channel in configuration and monitoring")

        # A single compact row: [enabled CH1]  [reading] [unit].
        # The channel name uses Geist Sans and the measurement Geist Mono.
        reading_row = QHBoxLayout()
        self._reading_row = reading_row
        reading_row.setSpacing(6)
        reading_row.addWidget(self.enabled, alignment=Qt.AlignmentFlag.AlignVCenter)
        reading_row.addStretch(1)
        self.reading_group = QWidget(self)
        self.reading_group.setObjectName(f"pm5020Ch{channel.value}ReadingGroup")
        reading_group_layout = QHBoxLayout(self.reading_group)
        reading_group_layout.setContentsMargins(0, 0, 0, 0)
        reading_group_layout.setSpacing(6)
        self.reading_label = QLabel("—")
        self.reading_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.reading_label.setFont(make_font("mono", 20, QFont.Weight.Bold))
        self.reading_label.setMinimumHeight(44)
        reading_group_layout.addWidget(self.reading_label)
        self.unit = QComboBox()
        self.unit.setObjectName(f"pm5020Ch{channel.value}DisplayUnit")
        self.unit.addItems(DISPLAY_UNITS)
        self.unit.setCurrentText(default_unit)
        self.unit.setToolTip("Display only; also selects this channel's plot Y-axis units")
        reading_group_layout.addWidget(self.unit)
        reading_row.addWidget(self.reading_group, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(reading_row)

        form = QGridLayout()
        form.setHorizontalSpacing(5)
        form.addWidget(QLabel("Wavelength (nm):"), 0, 0)
        self.wavelength = QLineEdit(f"{default_wavelength_nm:.1f}")
        self.wavelength.setObjectName(f"pm5020Ch{channel.value}Wavelength")
        wavelength_validator = QDoubleValidator(1.0, 100000.0, 17, self.wavelength)
        wavelength_validator.setLocale(QLocale.c())
        self.wavelength.setValidator(wavelength_validator)
        self.wavelength.setToolTip("Sensor-specific wavelength support is checked by the Thorlabs driver")
        self.wavelength.editingFinished.connect(self._format_wavelength_display)
        form.addWidget(self.wavelength, 0, 1, 1, 3)

        form.addWidget(QLabel("Power range:"), 1, 0)
        self.auto_range = QCheckBox("Auto")
        self.auto_range.setChecked(True)
        form.addWidget(self.auto_range, 1, 1)
        self.manual_range = QLineEdit("1")
        self.manual_range.setObjectName(f"pm5020Ch{channel.value}ManualRange")
        self.manual_range.setMinimumWidth(56)
        self._set_range_validator("mW")
        self.manual_range.setToolTip("Requested manual range. Physical SDK validates supported sensor ranges.")
        form.addWidget(self.manual_range, 1, 2)
        self.range_unit = QComboBox()
        self.range_unit.addItems(list(POWER_RANGE_SCALES))
        self.range_unit.setCurrentText("mW")
        self._previous_range_unit = "mW"
        form.addWidget(self.range_unit, 1, 3)
        self.auto_range.toggled.connect(self.manual_range.setDisabled)
        self.auto_range.toggled.connect(self.range_unit.setDisabled)
        self.range_unit.currentTextChanged.connect(self._change_range_unit)
        self.manual_range.setEnabled(False)
        self.range_unit.setEnabled(False)

        self.input_filter = QToolButton()
        self.input_filter.setObjectName(f"pm5020Ch{channel.value}InputFilterButton")
        self.input_filter.setCheckable(True)
        self.input_filter.setAutoRaise(True)
        self.input_filter.setIcon(QIcon(":/icons/filter-lowpass.svg"))
        self.input_filter.setToolTip(f"Toggle the PM5020 input filter / smoothing for CH{channel.value}")
        self.input_filter.setAccessibleName(f"Toggle PM5020 input filter for channel {channel.value}")
        self.input_filter.setIconSize(QSize(18, 18))
        self.input_filter.setFixedSize(28, 28)
        self.input_filter.setStyleSheet(
            "QToolButton:checked { background-color: #e3f2fd; border: 1px solid #1976d2; border-radius: 4px; }"
        )
        layout.addLayout(form)

        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("bottom", "Elapsed time", units="s")
        self.plot.setLabel("left", "Optical power", units=default_unit)
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMinimumHeight(155)
        self.plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.plot.setTitle(f"CH{channel.value} Power vs Time", **pyqtgraph_title_style())
        self.curve = self.plot.plot(pen=pg.mkPen("#1976d2" if channel is PowerChannel.CH1 else "#d95f02", width=2))
        self.plot_host = QWidget()
        plot_layout = QGridLayout(self.plot_host)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.addWidget(self.plot, 0, 0)
        self.plot_actions = QWidget(self.plot_host)
        actions_layout = QHBoxLayout(self.plot_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(2)
        actions_layout.addWidget(self.input_filter)
        self.clear_button = QToolButton(self.plot_host)
        self.clear_button.setObjectName(f"pm5020Ch{channel.value}ClearPlotButton")
        self.clear_button.setIcon(QIcon(":/icons/eraser.svg"))
        self.clear_button.setToolTip(f"Clear CH{channel.value} history and latest reading only")
        self.clear_button.setAccessibleName(f"Clear PM5020 channel {channel.value} plot")
        self.clear_button.setAutoRaise(True)
        self.clear_button.setFixedSize(28, 28)
        actions_layout.addWidget(self.clear_button)
        plot_layout.addWidget(self.plot_actions, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.clear_button.raise_()
        self.unit.currentTextChanged.connect(self.refresh_reading)
        self._latest_power_w: float | None = None
        self._latest_timestamp: datetime | None = None

    @Slot(str)
    def _change_range_unit(self, unit: str) -> None:
        """Change manual-range presentation without changing the physical range."""
        watts = (
            float(self.manual_range.text()) * POWER_RANGE_SCALES[self._previous_range_unit]
            if self.manual_range.hasAcceptableInput()
            else 1e-3
        )
        with QSignalBlocker(self.manual_range):
            self.manual_range.setText(repr(watts / POWER_RANGE_SCALES[unit]))
        self._set_range_validator(unit)
        self._previous_range_unit = unit

    def _set_range_validator(self, unit: str) -> None:
        scale = POWER_RANGE_SCALES[unit]
        validator = QDoubleValidator(1e-15 / scale, 1e3 / scale, 17, self.manual_range)
        validator.setLocale(QLocale.c())
        self.manual_range.setValidator(validator)

    def _manual_range_w(self) -> float:
        if not self.manual_range.hasAcceptableInput():
            raise ValueError(f"CH{self.channel.value}: enter a valid manual power range")
        value = float(self.manual_range.text())
        if value <= 0:
            raise ValueError(f"CH{self.channel.value}: manual power range must be positive")
        return value * POWER_RANGE_SCALES[self.range_unit.currentText()]

    @Slot()
    def _format_wavelength_display(self) -> None:
        """Show at least one decimal, without silently rounding sensor settings."""
        if not self.wavelength.hasAcceptableInput():
            return
        value = float(self.wavelength.text())
        if value == round(value, 1):
            formatted = f"{value:.1f}"
            if self.wavelength.text() != formatted:
                self.wavelength.setText(formatted)

    def channel_config(self) -> ChannelConfig:
        if not self.wavelength.hasAcceptableInput():
            raise ValueError(f"CH{self.channel.value}: enter a valid wavelength in nm")
        manual_range = None
        if not self.auto_range.isChecked():
            manual_range = self._manual_range_w()
        return ChannelConfig(
            wavelength_nm=float(self.wavelength.text()),
            auto_range=self.auto_range.isChecked(),
            power_range_w=manual_range,
            input_filter_enabled=self.input_filter.isChecked(),
        )

    def set_reading(self, reading: PowerReading, elapsed_s: float) -> None:
        self._latest_power_w = reading.power_w
        self._latest_timestamp = reading.acquired_at_utc
        self._rows.append((elapsed_s, reading.power_w))
        self.refresh_reading()

    @Slot()
    def refresh_reading(self) -> None:
        if self._latest_power_w is not None:
            formatted = format_power(self._latest_power_w, self.unit.currentText())
            self.reading_label.setText(formatted.rsplit(" ", 1)[0] if formatted != "—" else formatted)

    def redraw(self, elapsed_now: float, window_s: float) -> None:
        visible = [(x, y) for x, y in self._rows if x >= elapsed_now - window_s]
        unit = self.unit.currentText()
        self.plot.setLabel("left", "Optical power", units=unit)
        if visible:
            times, raw_powers = zip(*visible, strict=True)
            powers = [convert_power(power, unit) for power in raw_powers]
            self.curve.setData(times, [float("nan") if power is None else power for power in powers])
        else:
            self.curve.setData([], [])
        # Keep both channel plots on the same elapsed-time window even if one is empty.
        self.plot.setXRange(max(0.0, elapsed_now - window_s), max(window_s, elapsed_now))

    def clear_history(self) -> None:
        self._rows.clear()
        self.curve.setData([], [])
        self._latest_power_w = None
        self._latest_timestamp = None
        self.reading_label.setText("—")


class PM5020Panel(QWidget):
    """PM5020 configuration, measurement, and plotting UI owned by one worker."""

    connection_status_changed = Signal(object, str)
    connect_requested = Signal(str, str, int)
    configure_requested = Signal(object)
    channels_requested = Signal(object)
    start_requested = Signal(int)
    pause_requested = Signal()
    resume_requested = Signal(int)
    interval_update_requested = Signal(int, int, int, int)
    stop_requested = Signal()
    disconnect_requested = Signal(int)
    shutdown_requested = Signal()
    shutdown_complete = Signal()
    shutdown_timeout = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        worker: PM5020MonitorWorker | None = None,
        backend_mode: str = "simulation",
        resource_name: str = "",
        wrapper_path: str = "",
        dll_path: str = "",
        settings: AppSettings | None = None,
        lease_registry: DeviceLeaseRegistry | None = None,
    ):
        super().__init__(parent)
        if backend_mode not in {"physical", "simulation"}:
            raise ValueError(f"Unsupported PM5020 backend mode: {backend_mode!r}")
        self.backend_mode = backend_mode
        self.settings = settings
        self.resource_name = resource_name
        self._connection_request_pending = False
        self._connection_operation_started = False
        self._backend_state = PM5020State.DISCONNECTED
        self._activity = "Disconnected"
        self._configuration_applied = False
        self._configuration_dirty = True
        self._settings_revision = 0
        self._connection_generation = 0
        self._configuration_request_id = 0
        self._pending_configuration: tuple[int, int, int] | None = None
        self._monitor_session_id = 0
        self._interval_request_id = 0
        self._pending_interval_request: tuple[int, int, int] | None = None
        self._requested_interval_ms = 1000
        self._acknowledged_interval_ms = 1000
        self._starting_interval_ms = 1000
        self._monitor_session = False
        self._paused = False
        self._last_elapsed = 0.0
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._thread_started = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.setInterval(5000)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)
        self._configuration_timer = QTimer(self)
        self._configuration_timer.setSingleShot(True)
        self._configuration_timer.setInterval(120)
        self._configuration_timer.timeout.connect(self._request_automatic_configuration)

        self.worker = (
            worker
            if worker is not None
            else PM5020MonitorWorker(wrapper_path=wrapper_path or None, dll_path=dll_path or None)
        )
        if lease_registry is not None:
            self.worker.bind_lease_registry(lease_registry)
        self.task = TaskRunner(self.worker, auto_start_run=False)
        self.task.worker_thread.setObjectName("PM5020-monitor")
        self.task.worker_thread.started.connect(self.worker.initialize)
        self.worker.instrument_state.connect(self._on_instrument_state)
        self.worker.activity_state.connect(self._on_activity_state)
        self.worker.reading_ready.connect(self._on_reading)
        self.worker.error_occurred.connect(self._show_error)
        self.task.worker_thread.finished.connect(self._on_thread_finished, Qt.ConnectionType.QueuedConnection)
        self.worker.configuration_result.connect(self._on_configuration_result)
        self.worker.monitoring_started.connect(self._on_monitoring_started)
        self.worker.monitor_interval_result.connect(self._on_monitor_interval_result)
        self._set_request_connections()

        # Match the Wavelength Scan and Power Monitor tabs: settings on the left,
        # visualization on the right. Only settings scroll in a shallow splitter.
        root = QHBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(10)
        # Match CT400ControlPanel's plain QWidget + QVBoxLayout structure.
        # No scroll container or custom background palette: Fusion draws the
        # same native group surfaces and label backgrounds as Wavelength Scan.
        self.controls_panel = QWidget(self)
        self.controls_panel.setObjectName("pm5020ControlsContent")
        self.controls_panel.setMinimumWidth(320)
        self.controls_panel.setMaximumWidth(400)
        layout = QVBoxLayout(self.controls_panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Connection ownership and backend mode live in the Instruments menu
        # and permanent status bar.
        mode_text = "Simulation — readings are simulated" if backend_mode == "simulation" else "Physical PM5020"
        self.mode_label = QLabel(mode_text)
        self.mode_label.setObjectName("pm5020BackendModeLabel")
        self.mode_label.hide()
        self.error_label = QLabel("")
        self.error_label.setObjectName("pm5020ErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b71c1c;")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.ch1 = PM5020ChannelControls(PowerChannel.CH1, default_wavelength_nm=1550.0, default_unit="mW")
        self.ch2 = PM5020ChannelControls(PowerChannel.CH2, default_wavelength_nm=775.0, default_unit="nW")
        layout.addWidget(self.ch1)
        layout.addWidget(self.ch2)

        controls = QGroupBox("Operation")
        controls.setObjectName("pm5020OperationGroup")
        style_group_box_title(controls)
        controls_layout = QGridLayout(controls)
        self.interval = QLineEdit("1")
        self.interval.setObjectName("pm5020UpdateInterval")
        self.interval.setMinimumWidth(56)
        interval_validator = QDoubleValidator(0.25, 5.0, 3, self.interval)
        interval_validator.setLocale(QLocale.c())
        self.interval.setValidator(interval_validator)
        self.interval.setToolTip(
            "Requested polling interval; actual acquisition rate depends on USB/instrument latency"
        )
        self.interval_unit = QComboBox()
        self.interval_unit.addItems(("ms", "s"))
        self.interval_unit.setCurrentText("s")
        self._previous_interval_unit = "s"
        self.interval_unit.setFixedWidth(58)
        self.interval_unit.currentTextChanged.connect(self._change_interval_unit)
        self.interval.editingFinished.connect(self._interval_edit_finished)
        self.history = QComboBox()
        for seconds in HISTORY_SECONDS:
            label = f"{seconds} s" if seconds < 60 else f"{seconds // 60} min"
            self.history.addItem(label, seconds)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("pm5020StartButton")
        self.start_button.setStyleSheet(PM5020_ACTION_BUTTON_STYLE)
        self.start_button.setIcon(QIcon(":/icons/play.svg"))
        self.start_button.setMinimumHeight(35)
        self.pause_resume_button = QPushButton("Pause")
        self.pause_resume_button.setObjectName("pm5020PauseResumeButton")
        self.pause_resume_button.setIcon(QIcon(":/icons/pause.svg"))
        self.pause_resume_button.setMinimumHeight(35)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("pm5020StopButton")
        self.stop_button.setStyleSheet(PM5020_ACTION_BUTTON_STYLE)
        self.stop_button.setIcon(QIcon(":/icons/stop.svg"))
        self.stop_button.setMinimumHeight(35)
        for button in (self.start_button, self.pause_resume_button, self.stop_button):
            button.setEnabled(False)
        controls_layout.addWidget(QLabel("Update every:"), 0, 0)
        update_row = QHBoxLayout()
        update_row.addWidget(self.interval, stretch=1)
        update_row.addWidget(self.interval_unit)
        controls_layout.addLayout(update_row, 0, 1)
        controls_layout.addWidget(QLabel("Plot history:"), 1, 0)
        controls_layout.addWidget(self.history, 1, 1)
        actions_layout = QHBoxLayout()
        actions_layout.addWidget(self.start_button, stretch=1)
        actions_layout.addWidget(self.pause_resume_button, stretch=1)
        actions_layout.addWidget(self.stop_button, stretch=1)
        controls_layout.addLayout(actions_layout, 2, 0, 1, 2)
        layout.addWidget(controls)
        layout.addStretch(1)
        root.addWidget(self.controls_panel, stretch=0)

        self.plot_container = QWidget()
        self.plot_container.setObjectName("pm5020PlotsContainer")
        plots = QVBoxLayout(self.plot_container)
        plots.setContentsMargins(0, 0, 0, 0)
        plots.setSpacing(6)
        plots.addWidget(self.ch1.plot_host, stretch=1)
        plots.addWidget(self.ch2.plot_host, stretch=1)
        root.addWidget(self.plot_container, stretch=1)

        self.start_button.clicked.connect(self._start)
        self.pause_resume_button.clicked.connect(self._toggle_pause_resume)
        self.stop_button.clicked.connect(self.stop_requested)
        self.ch1.clear_button.clicked.connect(lambda: self._clear_channel(self.ch1))
        self.ch2.clear_button.clicked.connect(lambda: self._clear_channel(self.ch2))
        self.history.currentIndexChanged.connect(self._redraw)
        self.interval.textChanged.connect(self._interval_text_changed)
        self.interval_unit.currentTextChanged.connect(self._update_controls)
        for channel in (self.ch1, self.ch2):
            channel.wavelength.textChanged.connect(self._configuration_input_changed)
            channel.wavelength.editingFinished.connect(self._configuration_edited)
            channel.auto_range.toggled.connect(self._configuration_edited)
            channel.manual_range.textChanged.connect(self._configuration_input_changed)
            channel.manual_range.editingFinished.connect(self._configuration_edited)
            channel.input_filter.toggled.connect(self._configuration_edited)
            channel.enabled.toggled.connect(self._configuration_edited)
            channel.range_unit.currentTextChanged.connect(self._configuration_edited)
            channel.unit.currentTextChanged.connect(lambda _text: self._redraw())
        if settings is not None:
            self._restore_preferences(settings)
        self._redraw()
        self._update_controls()

    def _restore_preferences(self, settings: AppSettings) -> None:
        """Restore presentation controls without queuing hardware configuration."""
        channels = [self.ch1, self.ch2]
        values = [settings.pm5020_channel_preferences(1), settings.pm5020_channel_preferences(2)]
        if not any(item["enabled"] for item in values):
            # The application requires one selected channel before connection.
            # Recover predictably from stale or manually corrupted preferences.
            values[0]["enabled"] = True
        for controls, prefs in zip(channels, values, strict=True):
            blockers = [
                QSignalBlocker(widget)
                for widget in (
                    controls.enabled,
                    controls.wavelength,
                    controls.auto_range,
                    controls.manual_range,
                    controls.range_unit,
                    controls.unit,
                    controls.input_filter,
                )
            ]
            controls.enabled.setChecked(prefs["enabled"])
            controls.wavelength.setText(repr(prefs["wavelength_nm"]))
            controls.auto_range.setChecked(prefs["auto_range"])
            range_unit = prefs["range_unit"]
            controls.range_unit.setCurrentText(range_unit)
            controls._previous_range_unit = range_unit
            controls._set_range_validator(range_unit)
            controls.manual_range.setText(repr(prefs["power_range_w"] / POWER_RANGE_SCALES[range_unit]))
            controls.manual_range.setEnabled(not prefs["auto_range"])
            controls.range_unit.setEnabled(not prefs["auto_range"])
            controls.unit.setCurrentText(prefs["display_unit"])
            controls.input_filter.setChecked(prefs["input_filter_enabled"])
            del blockers

        shared = settings.pm5020_preferences()
        blocker = QSignalBlocker(self.interval_unit)
        self.interval_unit.setCurrentText(shared["interval_unit"])
        del blocker
        self._previous_interval_unit = shared["interval_unit"]
        if self._previous_interval_unit == "s":
            validator = QDoubleValidator(0.25, 5.0, 3, self.interval)
        else:
            validator = QDoubleValidator(250, 5000, 0, self.interval)
        validator.setLocale(QLocale.c())
        self.interval.setValidator(validator)
        factor = 1000 if self._previous_interval_unit == "s" else 1
        self.interval.setText(f"{shared['monitor_interval_ms'] / factor:g}")
        self._requested_interval_ms = shared["monitor_interval_ms"]
        self.history.setCurrentIndex(self.history.findData(shared["history_seconds"]))

    def save_preferences(self, settings: AppSettings | None = None) -> None:
        """Capture valid UI preferences without recording live instrument state."""
        target = settings or self.settings
        if target is None:
            return
        for controls in (self.ch1, self.ch2):
            try:
                config = controls.channel_config()
            except ValueError:
                config = None
            # Preserve independently valid controls when another field is being edited.
            wavelength = None
            if controls.wavelength.hasAcceptableInput():
                try:
                    candidate = float(controls.wavelength.text())
                    if 1.0 <= candidate <= 100000.0:
                        wavelength = candidate
                except (ValueError, OverflowError):
                    pass
            manual_range_w = config.power_range_w if config is not None else None
            if manual_range_w is None and controls.manual_range.hasAcceptableInput():
                try:
                    candidate = controls._manual_range_w()
                    if 1e-15 <= candidate <= 1e3:
                        manual_range_w = candidate
                except (ValueError, OverflowError):
                    pass
            target.set_pm5020_channel_preferences(
                controls.channel.value,
                {
                    "enabled": controls.enabled.isChecked(),
                    "wavelength_nm": wavelength,
                    "auto_range": controls.auto_range.isChecked(),
                    "power_range_w": manual_range_w,
                    "range_unit": controls.range_unit.currentText(),
                    "display_unit": controls.unit.currentText(),
                    "input_filter_enabled": controls.input_filter.isChecked(),
                },
            )
        try:
            interval_ms = self._interval_ms()
        except ValueError:
            interval_ms = None
        target.set_pm5020_preferences(
            monitor_interval_ms=interval_ms,
            interval_unit=self.interval_unit.currentText(),
            history_seconds=int(self.history.currentData()),
        )

    @Slot(str)
    def _change_interval_unit(self, unit: str) -> None:
        """Keep the requested interval constant when the display unit changes."""
        milliseconds: float | None = None
        if self.interval.hasAcceptableInput():
            old_factor = 1000 if self._previous_interval_unit == "s" else 1
            candidate = round(float(self.interval.text()) * old_factor)
            if 250 <= candidate <= 5000:
                milliseconds = float(candidate)
        with QSignalBlocker(self.interval):
            if unit == "s":
                validator = QDoubleValidator(0.25, 5.0, 3, self.interval)
                validator.setLocale(QLocale.c())
                self.interval.setValidator(validator)
            else:
                validator = QDoubleValidator(250, 5000, 0, self.interval)
                validator.setLocale(QLocale.c())
                self.interval.setValidator(validator)
            if milliseconds is not None:
                self.interval.setText(f"{milliseconds / 1000:g}" if unit == "s" else f"{milliseconds:g}")
        self._previous_interval_unit = unit
        self._update_controls()
        if milliseconds is not None:
            self._interval_edit_finished()
        else:
            self._show_error("Enter an update interval between 250 and 5000 ms.")

    def _interval_ms(self) -> int:
        factor = 1000 if self.interval_unit.currentText() == "s" else 1
        if not self.interval.hasAcceptableInput():
            raise ValueError("Enter an update interval between 250 and 5000 ms.")
        milliseconds = round(float(self.interval.text()) * factor)
        if not 250 <= milliseconds <= 5000:
            raise ValueError("Enter an update interval between 250 and 5000 ms.")
        return milliseconds

    def _set_request_connections(self) -> None:
        self.connect_requested.connect(self.worker.connect_instrument)
        self.configure_requested.connect(self.worker.configure_channels)
        self.channels_requested.connect(self.worker.set_channels)
        self.start_requested.connect(self.worker.start_monitoring)
        self.pause_requested.connect(self.worker.pause_monitoring)
        self.resume_requested.connect(self.worker.resume_monitoring)
        self.interval_update_requested.connect(self.worker.update_monitor_interval)
        self.stop_requested.connect(self.worker.stop_monitoring)
        self.disconnect_requested.connect(self.worker.disconnect_instrument)
        self.shutdown_requested.connect(self.worker.shutdown)

    def _ensure_worker_started(self) -> None:
        if not self.task.worker_thread.isRunning() and not self._shutdown_requested:
            self._thread_started = True
            self.task.start()

    @Slot()
    def _connect(self) -> None:
        self.request_connect()

    def request_connect(self) -> None:
        if self._shutdown_requested or self._connection_request_pending:
            return
        if self._backend_state is not PM5020State.DISCONNECTED or self._activity in {"Connecting", "Disconnecting"}:
            return
        self.error_label.clear()
        self.error_label.hide()
        self._ensure_worker_started()
        self._connection_generation += 1
        self._invalidate_configuration()
        mode = "Simulator" if self.backend_mode == "simulation" else "Physical PM5020"
        self.connect_requested.emit(mode, self.resource_name, self._connection_generation)
        self._connection_request_pending = True
        self._connection_operation_started = False
        self._activity = "Connecting"
        self._update_controls()
        self.connection_status_changed.emit(self._backend_state, self._activity)

    @Slot()
    def _disconnect(self) -> None:
        self.request_disconnect()

    def request_disconnect(self) -> None:
        if self._shutdown_requested or self._connection_request_pending:
            return
        if self._backend_state is PM5020State.DISCONNECTED or self._activity in {"Connecting", "Disconnecting"}:
            return
        self._connection_generation += 1
        self._monitor_session_id = 0
        self._pending_interval_request = None
        self._invalidate_configuration()
        self.disconnect_requested.emit(self._connection_generation)
        self._connection_request_pending = True
        self._connection_operation_started = False
        self._activity = "Disconnecting"
        self._update_controls()
        self.connection_status_changed.emit(self._backend_state, self._activity)

    @Slot()
    def _request_automatic_configuration(self) -> None:
        if (
            self._shutdown_requested
            or not self._configuration_dirty
            or self._pending_configuration is not None
            or self._backend_state is not PM5020State.IDLE
            or self._monitor_session
            or self._paused
        ):
            return
        channels = [channel for channel in (self.ch1, self.ch2) if channel.enabled.isChecked()]
        if not channels:
            self._show_error("Enable at least one channel before starting monitoring.")
            return
        try:
            settings = [(channel.channel, channel.channel_config()) for channel in channels]
        except ValueError as exc:
            self._show_error(f"Invalid channel settings: {exc}")
            return
        self.channels_requested.emit(tuple(channel.channel for channel in channels))
        self._configuration_request_id += 1
        request = ConfigurationRequest(
            self._connection_generation,
            self._configuration_request_id,
            self._settings_revision,
            tuple(settings),
        )
        self._pending_configuration = (request.generation, request.request_id, request.settings_revision)
        self._configuration_applied = False
        self.configure_requested.emit(request)
        self._update_controls()

    @Slot()
    def _interval_text_changed(self, *_args: object) -> None:
        self._update_controls()

    @Slot()
    def _interval_edit_finished(self) -> None:
        try:
            value = self._interval_ms()
        except ValueError as exc:
            self._show_error(str(exc))
            self._update_controls()
            return
        self._requested_interval_ms = value
        self._update_controls()
        if self._monitor_session and not self._paused and value != self._acknowledged_interval_ms:
            self._send_interval_update()

    def _send_interval_update(self) -> None:
        if (
            self._backend_state is not PM5020State.IDLE
            or not self._monitor_session
            or self._paused
            or not self._monitor_session_id
            or self._shutdown_requested
        ):
            return
        self._interval_request_id += 1
        request_id = self._interval_request_id
        self._pending_interval_request = (self._connection_generation, self._monitor_session_id, request_id)
        self.interval_update_requested.emit(
            self._connection_generation, self._monitor_session_id, request_id, self._requested_interval_ms
        )

    @Slot(int, int, int, bool, str)
    def _on_monitor_interval_result(
        self, generation: int, session_id: int, request_id: int, success: bool, message: str
    ) -> None:
        key = (generation, session_id, request_id)
        if self._pending_interval_request != key:
            return
        self._pending_interval_request = None
        if success and generation == self._connection_generation and session_id == self._monitor_session_id:
            self._acknowledged_interval_ms = self._requested_interval_ms
            self.interval.setToolTip(
                f"Active interval confirmed at {self._acknowledged_interval_ms} ms; "
                "actual acquisition rate depends on USB/instrument latency"
            )
        elif message and self._monitor_session and not self._paused:
            self._show_error(f"Could not update monitoring interval: {message}")

    @Slot()
    def _start(self) -> None:
        if not self.start_button.isEnabled():
            return
        enabled = tuple(channel.channel for channel in (self.ch1, self.ch2) if channel.enabled.isChecked())
        if not enabled:
            self._show_error("Enable at least one channel before monitoring.")
            return
        try:
            interval_ms = self._interval_ms()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.channels_requested.emit(enabled)
        self._requested_interval_ms = interval_ms
        self._starting_interval_ms = interval_ms
        self.start_requested.emit(interval_ms)

    @Slot()
    def _toggle_pause_resume(self) -> None:
        if not self.pause_resume_button.isEnabled():
            return
        if self._paused:
            self.resume_requested.emit(self._requested_interval_ms)
        else:
            self.pause_requested.emit()

    @Slot()
    def _configuration_input_changed(self, *_args: object) -> None:
        self._settings_revision += 1
        self._configuration_applied = False
        self._configuration_dirty = True
        self._pending_configuration = None
        self._configuration_timer.stop()
        self._update_controls()

    @Slot()
    def _configuration_edited(self, *_args: object) -> None:
        self._settings_revision += 1
        self._configuration_applied = False
        self._configuration_dirty = True
        self._pending_configuration = None
        self._configuration_timer.stop()
        if self._backend_state is PM5020State.IDLE and not self._monitor_session and not self._paused:
            self._configuration_timer.start()
        for channel in (self.ch1, self.ch2):
            selected = "enabled" if channel.input_filter.isChecked() else "disabled"
            channel.input_filter.setToolTip(
                f"PM5020 input filter requested {selected}; awaiting configuration acknowledgement"
            )
        self._update_controls()

    def _invalidate_configuration(self) -> None:
        self._configuration_applied = False
        self._configuration_dirty = True
        self._pending_configuration = None
        self._configuration_timer.stop()

    @Slot(object)
    def _on_instrument_state(self, state: PM5020State) -> None:
        if (
            state is PM5020State.DISCONNECTED
            and self._connection_request_pending
            and not self._connection_operation_started
        ):
            return
        self._backend_state = state
        if state in {PM5020State.IDLE, PM5020State.ERROR} or (
            state is PM5020State.DISCONNECTED and self._connection_operation_started
        ):
            self._connection_request_pending = False
            self._connection_operation_started = False
        if state is PM5020State.DISCONNECTED or state is PM5020State.ERROR:
            self._invalidate_configuration()
        if state is PM5020State.ERROR:
            self._activity = "Recovery required"
            self._monitor_session = False
            self._paused = False
            self._show_error("Recovery required. Stop monitoring, disconnect, and reconnect deliberately.")
        self._update_controls()
        if state is PM5020State.IDLE and self._configuration_dirty and self._pending_configuration is None:
            self._configuration_timer.start()
        self.connection_status_changed.emit(self._backend_state, self._activity)

    @Slot(str)
    def _on_activity_state(self, activity: str) -> None:
        if activity in {"Connecting", "Disconnecting"}:
            self._connection_operation_started = True
        elif activity == "Disconnected" and self._connection_request_pending and not self._connection_operation_started:
            return
        if self._backend_state is PM5020State.ERROR and activity != "Recovery required":
            activity = "Recovery required"
        self._activity = activity
        if activity in {"Connected / idle", "Disconnected"}:
            self.error_label.clear()
            self.error_label.hide()
        if activity == "Paused":
            self._paused = True
        elif activity in {"Monitoring", "Busy with acquisition"}:
            was_paused = self._paused
            self._monitor_session = True
            self._paused = False
            if was_paused:
                self._acknowledged_interval_ms = self._requested_interval_ms
        elif activity in {"Connected / idle", "Disconnected", "Recovery required"}:
            self._monitor_session = False
            self._paused = False
        self._update_controls()
        if activity == "Connected / idle" and self._configuration_dirty and self._pending_configuration is None:
            self._configuration_timer.start()
        if activity in {"Connected / idle", "Disconnected", "Recovery required"}:
            self._connection_request_pending = False
            self._connection_operation_started = False
        self.connection_status_changed.emit(self._backend_state, self._activity)

    @Slot(object, float)
    def _on_reading(self, reading: PowerReading, elapsed_s: float) -> None:
        (self.ch1 if reading.channel is PowerChannel.CH1 else self.ch2).set_reading(reading, elapsed_s)
        self._last_elapsed = max(self._last_elapsed, elapsed_s)
        self._redraw()

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _clear_channel(self, channel: PM5020ChannelControls) -> None:
        """Clear only this channel; monitoring and the other channel continue."""
        channel.clear_history()
        self._redraw()

    @Slot()
    def _clear_history(self) -> None:
        self.ch1.clear_history()
        self.ch2.clear_history()
        self._last_elapsed = 0.0
        self._redraw()

    @Slot(int, int, int, bool, str)
    def _on_configuration_result(
        self, generation: int, request_id: int, revision: int, success: bool, message: str
    ) -> None:
        if self._pending_configuration != (generation, request_id, revision):
            return
        self._pending_configuration = None
        valid = (
            success
            and generation == self._connection_generation
            and revision == self._settings_revision
            and self._backend_state is PM5020State.IDLE
        )
        self._configuration_applied = valid
        self._configuration_dirty = False
        if valid:
            self.error_label.clear()
            self.error_label.hide()
            self._activity = "Connected / idle"
        else:
            self._show_error(message or "Settings changed or the PM5020 state changed before confirmation.")
        for channel in (self.ch1, self.ch2):
            selected = "enabled" if channel.input_filter.isChecked() else "disabled"
            channel.input_filter.setToolTip(
                f"PM5020 input filter requested {selected}; "
                f"{'configuration acknowledged' if valid else 'setting not confirmed' if not success else 'settings changed before acknowledgement'}."
            )
        self._update_controls()

    @Slot(int, int)
    def _on_monitoring_started(self, generation: int, session_id: int) -> None:
        if generation != self._connection_generation:
            return
        self._clear_history()
        self._monitor_session_id = session_id
        self._monitor_session = True
        self._paused = False
        self._acknowledged_interval_ms = self._starting_interval_ms
        self._activity = "Monitoring"
        self._update_controls()
        if self._requested_interval_ms != self._acknowledged_interval_ms:
            self._send_interval_update()

    def _redraw(self) -> None:
        window_s = int(self.history.currentData())
        self.ch1.redraw(self._last_elapsed, window_s)
        self.ch2.redraw(self._last_elapsed, window_s)

    @Slot()
    def _update_controls(self, *_args: object) -> None:
        recovering = self._backend_state is PM5020State.ERROR
        ready = self._backend_state is PM5020State.IDLE
        interval_valid = self.interval.hasAcceptableInput()
        if interval_valid:
            try:
                interval_valid = 250 <= self._interval_ms() <= 5000
            except ValueError:
                interval_valid = False
        can_start = (
            ready and self._configuration_applied and interval_valid and not recovering and not self._monitor_session
        )
        self.start_button.setEnabled(can_start)
        self.pause_resume_button.setText("Resume" if self._paused else "Pause")
        self.pause_resume_button.setIcon(QIcon(":/icons/play.svg") if self._paused else QIcon(":/icons/pause.svg"))
        self.pause_resume_button.setEnabled(
            self._monitor_session and not recovering and (not self._paused or interval_valid)
        )
        self.stop_button.setEnabled(self._monitor_session or self._paused)
        for channel in (self.ch1, self.ch2):
            for widget in (channel.wavelength, channel.auto_range, channel.input_filter, channel.enabled):
                widget.setEnabled(not self._monitor_session and not self._paused)
            manual_enabled = not self._monitor_session and not self._paused and not channel.auto_range.isChecked()
            channel.manual_range.setEnabled(manual_enabled)
            channel.range_unit.setEnabled(manual_enabled)
            channel.unit.setEnabled(True)

    @Slot()
    def _on_thread_finished(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_timer.stop()
        self._shutdown_complete = True
        self.connection_status_changed.emit(self._backend_state, "Disconnected")
        self.shutdown_complete.emit()

    @Slot()
    def _on_shutdown_timeout(self) -> None:
        if self._shutdown_complete:
            return
        message = (
            "PM5020 is still finishing a device call. The application is waiting safely; "
            "the instrument thread will not be terminated."
        )
        self._show_error(message)
        self.shutdown_timeout.emit(message)

    def request_shutdown(self) -> None:
        """Stop and close on the owner thread without waiting in the GUI thread."""
        if self._shutdown_complete:
            return
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._configuration_timer.stop()
        self.connection_status_changed.emit(self._backend_state, "Shutting down")
        if self.task.worker_thread.isRunning():
            self._shutdown_timer.start()
            self.shutdown_requested.emit()
        else:
            self._shutdown_complete = True
            self.shutdown_complete.emit()

    @property
    def shutdown_finished(self) -> bool:
        return self._shutdown_complete or not self._thread_started

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.shutdown_finished:
            self.request_shutdown()
            event.ignore()
            return
        super().closeEvent(event)
