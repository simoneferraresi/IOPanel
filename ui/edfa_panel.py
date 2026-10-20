"""EDFA tab: explicit read-only connection and nonblocking telemetry display."""

from __future__ import annotations

import logging
from typing import override

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from hardware.edfa_types import EDFAOpticalPower, EDFAState, EDFATelemetry
from logic.edfa_monitor import EDFAMonitorWorker
from logic.task_runner import TaskRunner
from ui.typography import make_font, style_group_box_title

logger = logging.getLogger("LabApp.edfa_panel")


def format_edfa_power(value: EDFAOpticalPower | None, *, nonnumeric_reply: str | None = None) -> str:
    if value is None:
        return f"{nonnumeric_reply} (instrument reply; not a measurement)" if nonnumeric_reply else "—"
    return f"{value.value:.5g} {value.unit.value}"


class EDFAReadOnlyPanel(QWidget):
    """Read-only EDFA controls. No device is initialized at startup."""

    connect_requested = Signal(str, str, int, str, str)
    disconnect_requested = Signal()
    read_requested = Signal()
    start_requested = Signal(int)
    stop_requested = Signal()
    shutdown_requested = Signal()
    shutdown_complete = Signal()
    shutdown_timeout = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        backend_mode: str = "simulation",
        resource_name: str = "",
        timeout_ms: int = 3000,
        write_termination: str = "none",
        read_termination: str = "none",
        worker: EDFAMonitorWorker | None = None,
    ) -> None:
        super().__init__(parent)
        if backend_mode not in {"simulation", "physical"}:
            raise ValueError(f"Unsupported EDFA backend: {backend_mode!r}")
        self.backend_mode = backend_mode
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self.write_termination = write_termination
        self.read_termination = read_termination
        self._backend_state = EDFAState.DISCONNECTED
        self._activity = "Disconnected"
        self._operation_pending = False
        self._monitoring = False
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._thread_started = False
        self._connect_started = False

        self.worker = worker if worker is not None else EDFAMonitorWorker()
        self.task = TaskRunner(self.worker, auto_start_run=False)
        self.task.worker_thread.setObjectName("EDFA-readonly-monitor")
        self.task.worker_thread.started.connect(self.worker.initialize)
        self.task.worker_thread.finished.connect(self._on_thread_finished, Qt.ConnectionType.QueuedConnection)
        self.connect_requested.connect(self.worker.connect_instrument)
        self.disconnect_requested.connect(self.worker.disconnect_instrument)
        self.read_requested.connect(self.worker.read_once)
        self.start_requested.connect(self.worker.start_monitoring)
        self.stop_requested.connect(self.worker.stop_monitoring)
        self.shutdown_requested.connect(self.worker.shutdown)
        self.worker.state_changed.connect(self._on_state)
        self.worker.identity_ready.connect(self._set_identity)
        self.worker.telemetry_ready.connect(self._on_telemetry)
        self.worker.error_occurred.connect(self._on_error)

        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.setInterval(5000)
        self._shutdown_timer.timeout.connect(self._on_shutdown_timeout)

        # Follow the PM5020 tab: a compact fixed-width control column and
        # a flexible readout area, both native QWidget layouts. No QScrollArea.
        root = QHBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        root.setSpacing(10)
        self.controls_panel = QWidget(self)
        self.controls_panel.setObjectName("edfaControlsContent")
        self.controls_panel.setMinimumWidth(320)
        self.controls_panel.setMaximumWidth(400)
        controls_layout = QVBoxLayout(self.controls_panel)
        controls_layout.setContentsMargins(10, 10, 10, 10)
        controls_layout.setSpacing(10)

        self.mode_label = QLabel(
            "SIMULATION — synthetic telemetry, no physical EDFA"
            if backend_mode == "simulation"
            else f"PHYSICAL / READ ONLY — {resource_name or 'no GPIB address configured'}"
        )
        self.mode_label.setObjectName("edfaModeLabel")
        self.mode_label.setFont(make_font("sans", 10, QFont.Weight.Medium))
        self.mode_label.setWordWrap(True)
        controls_layout.addWidget(self.mode_label)

        connection = QGroupBox("Connection")
        connection.setObjectName("edfaConnectionGroup")
        style_group_box_title(connection)
        connection_layout = QVBoxLayout(connection)
        connection_layout.setSpacing(7)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("edfaConnectButton")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("edfaDisconnectButton")
        for button in (self.connect_button, self.disconnect_button):
            button.setMinimumHeight(35)
            buttons.addWidget(button, stretch=1)
        connection_layout.addLayout(buttons)
        self.status_label = QLabel("Disconnected")
        self.status_label.setObjectName("edfaStatusLabel")
        self.status_label.setFont(make_font("sans", 11, QFont.Weight.DemiBold))
        self.identity_label = QLabel("Identity: —")
        self.identity_label.setObjectName("edfaIdentityLabel")
        self.identity_label.setWordWrap(True)
        self.identity_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        connection_layout.addWidget(self.status_label)
        connection_layout.addWidget(self.identity_label)
        controls_layout.addWidget(connection)

        operation = QGroupBox("Read-only acquisition")
        operation.setObjectName("edfaOperationGroup")
        style_group_box_title(operation)
        operation_layout = QVBoxLayout(operation)
        operation_layout.setSpacing(7)
        self.read_button = QPushButton("Read once")
        self.read_button.setObjectName("edfaReadButton")
        self.read_button.setMinimumHeight(35)
        operation_layout.addWidget(self.read_button)
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Update every:"))
        self.interval_combo = QComboBox()
        self.interval_combo.setObjectName("edfaIntervalCombo")
        for seconds in (2, 5, 10):
            self.interval_combo.addItem(f"{seconds} s", seconds * 1000)
        interval_row.addWidget(self.interval_combo, stretch=1)
        operation_layout.addLayout(interval_row)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("edfaStartButton")
        self.start_button.setIcon(QIcon(":/icons/play.svg"))
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("edfaStopButton")
        self.stop_button.setIcon(QIcon(":/icons/stop.svg"))
        self.start_button.setStyleSheet(
            "QPushButton#edfaStartButton { background-color: #1976d2; color: white; }"
            "QPushButton#edfaStartButton:disabled { background-color: #e0e0e0; color: #888888; }"
        )
        self.stop_button.setStyleSheet(
            "QPushButton#edfaStopButton { background-color: #c62828; color: white; }"
            "QPushButton#edfaStopButton:disabled { background-color: #e0e0e0; color: #888888; }"
        )
        for button in (self.start_button, self.stop_button):
            button.setMinimumHeight(35)
            actions.addWidget(button, stretch=1)
        operation_layout.addLayout(actions)
        controls_layout.addWidget(operation)

        self.error_label = QLabel("")
        self.error_label.setObjectName("edfaErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b71c1c;")
        self.error_label.hide()
        controls_layout.addWidget(self.error_label)
        self.safety_label = QLabel(
            "Read-only observations cannot certify safe optical conditions. "
            "This tab provides no emission, gain, diode-current, or optical-power controls."
        )
        self.safety_label.setObjectName("edfaSafetyLabel")
        self.safety_label.setWordWrap(True)
        controls_layout.addWidget(self.safety_label)
        controls_layout.addStretch(1)
        root.addWidget(self.controls_panel, stretch=0)

        self.dashboard_panel = QWidget(self)
        self.dashboard_panel.setObjectName("edfaTelemetryContent")
        self.dashboard_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dashboard_layout = QVBoxLayout(self.dashboard_panel)
        dashboard_layout.setContentsMargins(0, 0, 0, 0)
        dashboard_layout.setSpacing(10)
        self.reading_labels: dict[str, QLabel] = {}

        def metric(grid: QGridLayout, object_name: str, caption: str, row: int, column: int, *, size: int = 15) -> None:
            """Make a compact selectable readout using the app's Geist typography."""
            cell = QWidget()
            stack = QVBoxLayout(cell)
            stack.setContentsMargins(6, 5, 6, 5)
            stack.setSpacing(3)
            heading = QLabel(caption)
            heading.setFont(make_font("sans", 10))
            value = QLabel("—")
            value.setObjectName(object_name)
            value.setFont(make_font("mono", size, QFont.Weight.DemiBold))
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.reading_labels[object_name] = value
            stack.addWidget(heading)
            stack.addWidget(value)
            grid.addWidget(cell, row, column)

        optical = QGroupBox("Optical monitoring")
        optical.setObjectName("edfaOpticalGroup")
        style_group_box_title(optical)
        optical_grid = QGridLayout(optical)
        optical_grid.setContentsMargins(10, 14, 10, 8)
        optical_grid.setSpacing(8)
        optical_grid.setColumnStretch(0, 1)
        optical_grid.setColumnStretch(1, 1)
        metric(optical_grid, "edfaOutputLabel", "Output optical power", 0, 0, size=18)
        metric(optical_grid, "edfaInputLabel", "Input optical power (indicative)", 0, 1, size=18)
        metric(optical_grid, "edfaInputThresholdLabel", "Reported minimum input", 1, 0)
        metric(optical_grid, "edfaSetpointLabel", "Output setpoint (not measured)", 1, 1)
        dashboard_layout.addWidget(optical, stretch=1)

        health = QGroupBox("Amplifier status")
        health.setObjectName("edfaHealthGroup")
        style_group_box_title(health)
        health_grid = QGridLayout(health)
        health_grid.setContentsMargins(10, 14, 10, 8)
        health_grid.setSpacing(6)
        for column in range(3):
            health_grid.setColumnStretch(column, 1)
        metric(health_grid, "edfaCurrentLabel", "Pump diode current", 0, 0, size=13)
        metric(health_grid, "edfaTemperatureLabel", "Case temperature", 0, 1, size=13)
        metric(health_grid, "edfaControlModeLabel", "Control mode", 0, 2, size=13)
        metric(health_grid, "edfaReflectionLabel", "Back-reflection (% of max.)", 1, 0, size=12)
        metric(health_grid, "edfaRawStatusLabel", "Raw status word", 1, 1, size=12)
        metric(health_grid, "edfaUpdatedLabel", "Sampled at (UTC)", 1, 2, size=12)
        dashboard_layout.addWidget(health, stretch=1)

        observations = QGroupBox("Status observations")
        observations.setObjectName("edfaObservationsGroup")
        style_group_box_title(observations)
        observations_layout = QVBoxLayout(observations)
        self.alarm_label = QLabel("Disconnected; alarm bits unavailable.")
        self.alarm_label.setObjectName("edfaAlarmLabel")
        self.alarm_label.setWordWrap(True)
        observations_layout.addWidget(self.alarm_label)
        dashboard_layout.addWidget(observations)
        root.addWidget(self.dashboard_panel, stretch=1)

        self.connect_button.clicked.connect(self.request_connect)
        self.disconnect_button.clicked.connect(self.request_disconnect)
        self.read_button.clicked.connect(self.request_read)
        self.start_button.clicked.connect(self.request_monitoring)
        self.stop_button.clicked.connect(self.request_stop)
        self._update_controls()

    @property
    def shutdown_finished(self) -> bool:
        return self._shutdown_complete or not self._thread_started

    def _ensure_worker_started(self) -> None:
        if not self._thread_started and not self._shutdown_requested:
            self._thread_started = True
            self.task.start()

    def request_connect(self) -> None:
        if self._shutdown_requested or self._operation_pending or self._backend_state is not EDFAState.DISCONNECTED:
            return
        if self.backend_mode == "physical":
            if not self.resource_name:
                self._on_error("Configure the EDFA GPIB resource in the local INI before connecting.")
                return
            result = QMessageBox.question(
                self,
                "Confirm read-only EDFA connection",
                "Connecting can change the GPIB remote/local state. Confirm emission is OFF, "
                "the optical setup is supervised and no other software owns the GPIB controller.\n\n"
                f"Connect read-only to {self.resource_name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
        self._operation_pending = True
        self._connect_started = False
        self._activity = "Connecting"
        self.error_label.clear()
        self.error_label.hide()
        self._update_controls()
        self._ensure_worker_started()
        self.connect_requested.emit(
            self.backend_mode, self.resource_name, self.timeout_ms, self.write_termination, self.read_termination
        )

    def request_disconnect(self) -> None:
        if self._shutdown_requested or self._operation_pending or self._backend_state is EDFAState.DISCONNECTED:
            return
        self._operation_pending = True
        self._monitoring = False
        self._activity = "Disconnecting"
        self._update_controls()
        self.disconnect_requested.emit()

    def request_read(self) -> None:
        if self._shutdown_requested or self._operation_pending or self._backend_state is not EDFAState.IDLE:
            return
        self.read_requested.emit()

    def request_monitoring(self) -> None:
        if self._shutdown_requested or self._operation_pending or self._monitoring:
            return
        if self._backend_state is EDFAState.IDLE:
            self.start_requested.emit(int(self.interval_combo.currentData()))

    def request_stop(self) -> None:
        if not self._shutdown_requested and self._monitoring:
            self.stop_requested.emit()

    @Slot(object, str)
    def _on_state(self, state: EDFAState, activity: str) -> None:
        if self._shutdown_requested:
            return
        # The worker announces initial Disconnected when its thread starts.
        # Do not treat that as the result of our still-queued Connect request.
        if activity == "Disconnected" and self._operation_pending and not self._connect_started:
            return
        if activity == "Connecting":
            self._connect_started = True
        self._backend_state = state
        self._activity = activity
        self._operation_pending = activity in {"Connecting", "Disconnecting"}
        self._monitoring = activity == "Monitoring (read-only)"
        if state is EDFAState.DISCONNECTED:
            self._clear_telemetry()
        self._update_controls()

    @Slot(str)
    def _set_identity(self, identity: str) -> None:
        if not self._shutdown_requested:
            self.identity_label.setText(f"Identity: {identity or '—'}")

    @Slot(object)
    def _on_telemetry(self, report: EDFATelemetry) -> None:
        if self._shutdown_requested:
            return
        data = {
            "edfaOutputLabel": format_edfa_power(
                report.output_power, nonnumeric_reply=report.output_power_non_numeric_reply
            ),
            "edfaInputLabel": format_edfa_power(report.input_power),
            "edfaInputThresholdLabel": format_edfa_power(report.minimum_input_power),
            "edfaSetpointLabel": format_edfa_power(report.power_setpoint),
            "edfaCurrentLabel": f"{report.diode_current_a:.5g} A",
            "edfaTemperatureLabel": f"{report.case_temperature_c:.5g} °C",
            "edfaReflectionLabel": (
                f"{report.back_reflection_percent_of_max:.5g} %"
                if report.back_reflection_percent_of_max is not None
                else "Not available"
            ),
            "edfaControlModeLabel": report.status.control_mode.name if report.status.control_mode else "Unknown",
            "edfaRawStatusLabel": f"{report.status.raw_word} (0x{report.status.raw_word:08X})",
            "edfaUpdatedLabel": report.acquired_at_utc.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for key, value in data.items():
            self.reading_labels[key].setText(value)
        status = report.status
        alarms: list[str] = []
        if status.interlock_open:
            alarms.append("Interlock open")
        if status.overtemperature:
            alarms.append("Overtemperature")
        if status.input_power_low:
            alarms.append("Input-low flag")
        if status.back_reflection_alarm:
            alarms.append("Back-reflection alarm")
        if (
            report.input_power is not None
            and report.minimum_input_power is not None
            and report.input_power.unit is report.minimum_input_power.unit
            and report.input_power.value < report.minimum_input_power.value
        ):
            alarms.append("Input reading below device threshold (even if the input-low flag is clear)")
        self.alarm_label.setText(
            "Observations: " + "; ".join(alarms)
            if alarms
            else "No alarm bits asserted; this does not certify safe optical conditions."
        )
        self.error_label.clear()
        self.error_label.hide()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def _clear_telemetry(self) -> None:
        for label in self.reading_labels.values():
            label.setText("—")
        self.alarm_label.setText("Disconnected; alarm bits unavailable.")

    def _update_controls(self) -> None:
        ready = self._backend_state is EDFAState.IDLE
        blocked = self._operation_pending or self._shutdown_requested
        self.connect_button.setEnabled(not blocked and self._backend_state is EDFAState.DISCONNECTED)
        self.disconnect_button.setEnabled(not blocked and self._backend_state is not EDFAState.DISCONNECTED)
        self.read_button.setEnabled(not blocked and ready)
        self.start_button.setEnabled(not blocked and ready and not self._monitoring)
        self.stop_button.setEnabled(not self._shutdown_requested and self._monitoring)
        self.interval_combo.setEnabled(not blocked and not self._monitoring)
        self.status_label.setText(self._activity)

    def request_shutdown(self) -> None:
        if self._shutdown_requested or self._shutdown_complete:
            return
        self._shutdown_requested = True
        self._monitoring = False
        self._update_controls()
        if self.task.worker_thread.isRunning():
            self._shutdown_timer.start()
            self.shutdown_requested.emit()
        else:
            self._shutdown_complete = True
            self.shutdown_complete.emit()

    @Slot()
    def _on_thread_finished(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_timer.stop()
        self._shutdown_complete = True
        self.shutdown_complete.emit()

    @Slot()
    def _on_shutdown_timeout(self) -> None:
        if self._shutdown_complete:
            return
        message = "EDFA thread is completing a VISA operation; waiting without force-terminating hardware I/O."
        self._on_error(message)
        self.shutdown_timeout.emit(message)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.shutdown_finished:
            self.request_shutdown()
            event.ignore()
            return
        super().closeEvent(event)
