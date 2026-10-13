"""Standalone, independently operated PM5020 monitoring panel."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import override

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from hardware.pm5020_types import ChannelConfig, PM5020State, PowerChannel, PowerReading
from logic.pm5020_monitor import DISPLAY_UNITS, PM5020MonitorWorker, convert_power, format_power
from logic.task_runner import TaskRunner

logger = logging.getLogger("LabApp.pm5020_panel")

HISTORY_SECONDS = (10, 30, 60, 300, 600)
MAX_HISTORY_POINTS = 2400


class PM5020ChannelControls(QGroupBox):
    """Settings and current reading for one independent meter channel."""

    def __init__(self, channel: PowerChannel, *, default_wavelength_nm: float, default_unit: str):
        super().__init__(f"CH{channel.value} — Optical Power")
        self.channel = channel
        self._rows: deque[tuple[float, float]] = deque(maxlen=MAX_HISTORY_POINTS)
        layout = QVBoxLayout(self)

        self.reading_label = QLabel("—")
        self.reading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.reading_label.setStyleSheet("font-size: 24px; font-weight: 600;")
        self.measurement_status = QLabel("Not measured")
        self.age_label = QLabel("Last reading: —")
        layout.addWidget(self.reading_label)
        layout.addWidget(self.measurement_status)
        layout.addWidget(self.age_label)

        form = QFormLayout()
        self.wavelength = QDoubleSpinBox()
        self.wavelength.setRange(1, 100_000)
        self.wavelength.setDecimals(2)
        self.wavelength.setSuffix(" nm")
        self.wavelength.setValue(default_wavelength_nm)
        self.auto_range = QCheckBox("Automatic")
        self.auto_range.setChecked(True)
        self.manual_range = QDoubleSpinBox()
        self.manual_range.setRange(1e-15, 1e3)
        self.manual_range.setDecimals(12)
        self.manual_range.setValue(1e-3)
        self.manual_range.setSuffix(" W")
        self.manual_range.setEnabled(False)
        self.auto_range.toggled.connect(self.manual_range.setDisabled)
        self.input_filter = QCheckBox("Enabled")
        self.enabled = QCheckBox("Include in monitoring")
        self.enabled.setChecked(True)
        self.unit = QComboBox()
        self.unit.addItems(DISPLAY_UNITS)
        self.unit.setCurrentText(default_unit)

        form.addRow("Wavelength:", self.wavelength)
        form.addRow("Power range:", self.auto_range)
        form.addRow("Manual range:", self.manual_range)
        form.addRow("Input filter:", self.input_filter)
        form.addRow("Display unit:", self.unit)
        form.addRow("", self.enabled)
        layout.addLayout(form)

        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("bottom", "Elapsed time", units="s")
        self.plot.setLabel("left", "Optical power", units="W")
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setMinimumHeight(145)
        self.plot.setTitle(f"CH{channel.value} Power vs Time")
        self.curve = self.plot.plot(pen=pg.mkPen("#1976d2" if channel is PowerChannel.CH1 else "#d95f02", width=2))
        layout.addWidget(self.plot, stretch=1)
        self.unit.currentTextChanged.connect(self.refresh_reading)
        self._latest_power_w: float | None = None
        self._latest_timestamp: datetime | None = None

    def channel_config(self) -> ChannelConfig:
        manual_range = None if self.auto_range.isChecked() else self.manual_range.value()
        return ChannelConfig(
            wavelength_nm=self.wavelength.value(),
            auto_range=self.auto_range.isChecked(),
            power_range_w=manual_range,
            input_filter_enabled=self.input_filter.isChecked(),
        )

    def set_reading(self, reading: PowerReading, elapsed_s: float) -> None:
        self._latest_power_w = reading.power_w
        self._latest_timestamp = reading.acquired_at_utc
        self._rows.append((elapsed_s, reading.power_w))
        self.measurement_status.setText("Reading received")
        self.refresh_reading()
        self.age_label.setText(f"Last reading: {reading.acquired_at_utc.astimezone().strftime('%H:%M:%S.%f')[:-3]}")

    @Slot()
    def refresh_reading(self) -> None:
        if self._latest_power_w is not None:
            self.reading_label.setText(format_power(self._latest_power_w, self.unit.currentText()))

    def redraw(self, elapsed_now: float, window_s: float) -> None:
        visible = [(x, y) for x, y in self._rows if x >= elapsed_now - window_s]
        unit = self.unit.currentText()
        self.plot.setLabel("left", "Optical power", units=unit)
        if visible:
            times, raw_powers = zip(*visible, strict=True)
            powers = [convert_power(power, unit) for power in raw_powers]
            self.curve.setData(times, [float("nan") if power is None else power for power in powers])
            self.plot.setXRange(max(0.0, elapsed_now - window_s), max(window_s, elapsed_now))
        else:
            self.curve.setData([], [])

    def clear_history(self) -> None:
        self._rows.clear()
        self.curve.setData([], [])

    def mark_stale(self) -> None:
        self.measurement_status.setText("No new readings")


class PM5020Panel(QWidget):
    """Independent PM5020 connection, configuration, measurement, and plotting UI."""

    connect_requested = Signal(str, str)
    configure_requested = Signal(object)
    channels_requested = Signal(object)
    start_requested = Signal(int)
    pause_requested = Signal()
    resume_requested = Signal(int)
    stop_requested = Signal()
    disconnect_requested = Signal()
    shutdown_requested = Signal()
    shutdown_complete = Signal()
    shutdown_timeout = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, worker: PM5020MonitorWorker | None = None):
        super().__init__(parent)
        self._backend_state = PM5020State.DISCONNECTED
        self._activity = "Disconnected"
        self._configuration_applied = False
        self._monitor_session = False
        self._paused = False
        self._last_elapsed = 0.0
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.setInterval(5000)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)

        self.worker = worker if worker is not None else PM5020MonitorWorker()
        self.task = TaskRunner(self.worker, auto_start_run=False)
        self.task.worker_thread.setObjectName("PM5020-monitor")
        self.task.worker_thread.started.connect(self.worker.initialize)
        self.worker.instrument_state.connect(self._on_instrument_state)
        self.worker.activity_state.connect(self._on_activity_state)
        self.worker.reading_ready.connect(self._on_reading)
        self.worker.error_occurred.connect(self._show_error)
        self.task.worker_thread.finished.connect(self._on_thread_finished, Qt.ConnectionType.DirectConnection)
        self._set_request_connections()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)

        connection_box = QGroupBox("Connection and instrument status")
        connection_form = QFormLayout(connection_box)
        self.mode = QComboBox()
        self.mode.addItems(("Simulator", "Physical PM5020"))
        self.resource = QLineEdit()
        self.resource.setPlaceholderText("USB0::...::INSTR (enter the instrument's VISA resource)")
        self.status_label = QLabel("Disconnected")
        self.readiness_label = QLabel("Not ready")
        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setEnabled(False)
        connection_form.addRow("Instrument:", self.mode)
        connection_form.addRow("USB/VISA resource:", self.resource)
        connection_form.addRow("Panel status:", self.status_label)
        connection_form.addRow("Instrument state:", self.readiness_label)
        connection_buttons = QHBoxLayout()
        connection_buttons.addWidget(self.connect_button)
        connection_buttons.addWidget(self.disconnect_button)
        connection_form.addRow("", connection_buttons)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b71c1c;")
        connection_form.addRow("Message:", self.error_label)
        layout.addWidget(connection_box)

        self.ch1 = PM5020ChannelControls(PowerChannel.CH1, default_wavelength_nm=1550.0, default_unit="mW")
        self.ch2 = PM5020ChannelControls(PowerChannel.CH2, default_wavelength_nm=775.0, default_unit="nW")
        channels = QHBoxLayout()
        channels.addWidget(self.ch1, stretch=1)
        channels.addWidget(self.ch2, stretch=1)
        layout.addLayout(channels, stretch=1)

        controls = QGroupBox("Live monitoring")
        controls_layout = QHBoxLayout(controls)
        self.interval = QSpinBox()
        self.interval.setRange(250, 5000)
        self.interval.setSingleStep(250)
        self.interval.setValue(1000)
        self.interval.setSuffix(" ms")
        self.history = QComboBox()
        for seconds in HISTORY_SECONDS:
            label = f"{seconds} s" if seconds < 60 else f"{seconds // 60} min"
            self.history.addItem(label, seconds)
        self.apply_button = QPushButton("Apply Channel Settings")
        self.start_button = QPushButton("Start")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        self.clear_button = QPushButton("Clear Plot")
        for button in (self.start_button, self.pause_button, self.resume_button, self.stop_button):
            button.setEnabled(False)
        controls_layout.addWidget(QLabel("Update:"))
        controls_layout.addWidget(self.interval)
        controls_layout.addWidget(QLabel("History:"))
        controls_layout.addWidget(self.history)
        controls_layout.addWidget(self.apply_button)
        controls_layout.addWidget(self.start_button)
        controls_layout.addWidget(self.pause_button)
        controls_layout.addWidget(self.resume_button)
        controls_layout.addWidget(self.stop_button)
        controls_layout.addWidget(self.clear_button)
        layout.addWidget(controls)
        scroll.setWidget(content)
        root.addWidget(scroll)

        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.apply_button.clicked.connect(self._apply_configuration)
        self.start_button.clicked.connect(self._start)
        self.pause_button.clicked.connect(self.pause_requested)
        self.resume_button.clicked.connect(lambda: self.resume_requested.emit(self.interval.value()))
        self.stop_button.clicked.connect(self.stop_requested)
        self.clear_button.clicked.connect(self._clear_history)
        self.history.currentIndexChanged.connect(self._redraw)
        self.mode.currentTextChanged.connect(self._update_controls)
        self.resource.textChanged.connect(self._update_controls)
        self.interval.valueChanged.connect(self._update_controls)
        for channel in (self.ch1, self.ch2):
            channel.wavelength.valueChanged.connect(self._configuration_edited)
            channel.auto_range.toggled.connect(self._configuration_edited)
            channel.manual_range.valueChanged.connect(self._configuration_edited)
            channel.input_filter.toggled.connect(self._configuration_edited)
            channel.enabled.toggled.connect(self._configuration_edited)
            channel.unit.currentTextChanged.connect(lambda _text: self._redraw())
        self._update_controls()

    def _set_request_connections(self) -> None:
        self.connect_requested.connect(self.worker.connect_instrument)
        self.configure_requested.connect(self.worker.configure_channels)
        self.channels_requested.connect(self.worker.set_channels)
        self.start_requested.connect(self.worker.start_monitoring)
        self.pause_requested.connect(self.worker.pause_monitoring)
        self.resume_requested.connect(self.worker.resume_monitoring)
        self.stop_requested.connect(self.worker.stop_monitoring)
        self.disconnect_requested.connect(self.worker.disconnect_instrument)
        self.shutdown_requested.connect(self.worker.shutdown)

    def _ensure_worker_started(self) -> None:
        if not self.task.worker_thread.isRunning() and not self._shutdown_requested:
            self.task.start()

    @Slot()
    def _connect(self) -> None:
        self.error_label.clear()
        self._ensure_worker_started()
        self.connect_requested.emit(self.mode.currentText(), self.resource.text())
        self._activity = "Connecting"
        self._update_controls()

    @Slot()
    def _apply_configuration(self) -> None:
        channels = [channel for channel in (self.ch1, self.ch2) if channel.enabled.isChecked()]
        if not channels:
            self.error_label.setText("Enable at least one channel before applying settings.")
            return
        try:
            settings = [(channel.channel, channel.channel_config()) for channel in channels]
        except ValueError as exc:
            self.error_label.setText(f"Invalid channel settings: {exc}")
            return
        self.channels_requested.emit(tuple(channel.channel for channel in channels))
        self.configure_requested.emit(tuple(settings))
        self._configuration_applied = True
        self._update_controls()

    @Slot()
    def _start(self) -> None:
        enabled = tuple(channel.channel for channel in (self.ch1, self.ch2) if channel.enabled.isChecked())
        if not enabled:
            self.error_label.setText("Enable at least one channel before monitoring.")
            return
        self.channels_requested.emit(enabled)
        self.start_requested.emit(self.interval.value())
        self._monitor_session = True
        self._paused = False
        self._activity = "Monitoring"
        self._last_elapsed = 0.0
        self._update_controls()

    @Slot()
    def _configuration_edited(self, *_args: object) -> None:
        self._configuration_applied = False
        self._update_controls()

    @Slot(object)
    def _on_instrument_state(self, state: PM5020State) -> None:
        self._backend_state = state
        self.readiness_label.setText(f"{state.name.title()} — {'Ready' if state is PM5020State.IDLE else 'Not ready'}")
        if state is PM5020State.ERROR:
            self._activity = "Recovery required"
            self._monitor_session = False
            self._paused = False
            self.error_label.setText("Recovery required. Stop monitoring, disconnect, and reconnect deliberately.")
        self._update_controls()

    @Slot(str)
    def _on_activity_state(self, activity: str) -> None:
        self._activity = activity
        if activity in {"Connected / idle", "Disconnected"}:
            self.error_label.clear()
        if activity == "Paused":
            self._paused = True
        elif activity in {"Monitoring", "Busy with acquisition"}:
            self._monitor_session = True
            self._paused = False
        elif activity in {"Connected / idle", "Disconnected", "Recovery required"}:
            self._monitor_session = False
            self._paused = False
        self._update_controls()

    @Slot(object, float)
    def _on_reading(self, reading: PowerReading, elapsed_s: float) -> None:
        (self.ch1 if reading.channel is PowerChannel.CH1 else self.ch2).set_reading(reading, elapsed_s)
        self._last_elapsed = max(self._last_elapsed, elapsed_s)
        self._redraw()

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)

    @Slot()
    def _clear_history(self) -> None:
        self.ch1.clear_history()
        self.ch2.clear_history()

    def _redraw(self) -> None:
        window_s = int(self.history.currentData())
        self.ch1.redraw(self._last_elapsed, window_s)
        self.ch2.redraw(self._last_elapsed, window_s)

    @Slot()
    def _update_controls(self, *_args: object) -> None:
        connected = self._backend_state is not PM5020State.DISCONNECTED
        recovering = self._backend_state is PM5020State.ERROR
        busy = self._activity in {"Connecting", "Disconnecting", "Busy"}
        physical_missing_resource = self.mode.currentText() == "Physical PM5020" and not self.resource.text().strip()
        self.connect_button.setEnabled(not connected and not busy and not physical_missing_resource)
        self.disconnect_button.setEnabled(connected and not busy)
        self.mode.setEnabled(not connected and not busy)
        self.resource.setEnabled(not connected and not busy and self.mode.currentText() == "Physical PM5020")
        self.apply_button.setEnabled(connected and not recovering and not self._monitor_session and not self._paused)
        can_start = connected and self._configuration_applied and not recovering and not self._monitor_session
        self.start_button.setEnabled(can_start)
        self.pause_button.setEnabled(self._monitor_session and not self._paused and not recovering)
        self.resume_button.setEnabled(self._monitor_session and self._paused and not recovering)
        self.stop_button.setEnabled(self._monitor_session or self._paused)
        for channel in (self.ch1, self.ch2):
            for widget in (channel.wavelength, channel.auto_range, channel.input_filter, channel.enabled):
                widget.setEnabled(not self._monitor_session and not self._paused)
            channel.manual_range.setEnabled(
                not self._monitor_session and not self._paused and not channel.auto_range.isChecked()
            )
            channel.unit.setEnabled(True)
        self.status_label.setText(self._activity)

    @Slot()
    def _on_thread_finished(self) -> None:
        self._shutdown_timer.stop()
        self._shutdown_complete = True
        self.shutdown_complete.emit()

    @Slot()
    def _on_shutdown_timeout(self) -> None:
        if self._shutdown_complete:
            return
        message = (
            "PM5020 is still finishing a device call. The application is waiting safely; "
            "the instrument thread will not be terminated."
        )
        self.error_label.setText(message)
        self.shutdown_timeout.emit(message)

    def request_shutdown(self) -> None:
        """Stop and close on the owner thread without waiting in the GUI thread."""
        if self._shutdown_complete:
            return
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        if self.task.worker_thread.isRunning():
            self._shutdown_timer.start()
            self.shutdown_requested.emit()
        else:
            self._shutdown_complete = True
            self.shutdown_complete.emit()

    @property
    def shutdown_finished(self) -> bool:
        if self._shutdown_complete:
            return True
        return not self.task.worker_thread.isRunning()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.shutdown_finished:
            self.request_shutdown()
            event.ignore()
            return
        super().closeEvent(event)
