"""EDFA tab: explicit read-only connection and nonblocking telemetry display."""

from __future__ import annotations

import logging
from typing import override

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from hardware.edfa_types import EDFAOpticalPower, EDFAState, EDFATelemetry
from logic.edfa_monitor import EDFAMonitorWorker
from logic.task_runner import TaskRunner
from ui.typography import style_group_box_title

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

        root = QVBoxLayout(self)
        root.setContentsMargins(5, 5, 5, 5)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        root.addWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self.mode_label = QLabel(
            "SIMULATION — synthetic telemetry, no physical EDFA"
            if backend_mode == "simulation"
            else f"PHYSICAL / READ ONLY — {resource_name or 'no GPIB address configured'}"
        )
        self.mode_label.setObjectName("edfaModeLabel")
        layout.addWidget(self.mode_label)

        connection = QGroupBox("Connection")
        style_group_box_title(connection)
        connection_layout = QVBoxLayout(connection)
        buttons = QHBoxLayout()
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("edfaConnectButton")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("edfaDisconnectButton")
        self.read_button = QPushButton("Read once")
        self.read_button.setObjectName("edfaReadButton")
        for button in (self.connect_button, self.disconnect_button, self.read_button):
            buttons.addWidget(button)
        buttons.addStretch()
        connection_layout.addLayout(buttons)
        self.status_label = QLabel("Disconnected")
        self.status_label.setObjectName("edfaStatusLabel")
        self.identity_label = QLabel("Identity: —")
        self.identity_label.setObjectName("edfaIdentityLabel")
        connection_layout.addWidget(self.status_label)
        connection_layout.addWidget(self.identity_label)
        layout.addWidget(connection)

        telemetry = QGroupBox("Telemetry — observational only")
        style_group_box_title(telemetry)
        telemetry_layout = QVBoxLayout(telemetry)
        monitor_buttons = QHBoxLayout()
        self.start_button = QPushButton("Start monitoring")
        self.start_button.setObjectName("edfaStartButton")
        self.stop_button = QPushButton("Stop monitoring")
        self.stop_button.setObjectName("edfaStopButton")
        self.interval_combo = QComboBox()
        self.interval_combo.setObjectName("edfaIntervalCombo")
        for seconds in (2, 5, 10):
            self.interval_combo.addItem(f"{seconds} s", seconds * 1000)
        monitor_buttons.addWidget(self.start_button)
        monitor_buttons.addWidget(self.stop_button)
        monitor_buttons.addWidget(QLabel("Update interval:"))
        monitor_buttons.addWidget(self.interval_combo)
        monitor_buttons.addStretch()
        telemetry_layout.addLayout(monitor_buttons)
        self.reading_labels: dict[str, QLabel] = {}
        form = QFormLayout()
        readings = (
            ("edfaOutputLabel", "Output optical power"),
            ("edfaInputLabel", "Input power (indicative)"),
            ("edfaInputThresholdLabel", "Reported minimum input"),
            ("edfaSetpointLabel", "Output setpoint (not measured)"),
            ("edfaCurrentLabel", "Pump diode current"),
            ("edfaTemperatureLabel", "Case temperature"),
            ("edfaReflectionLabel", "Back-reflection (% of max.)"),
            ("edfaControlModeLabel", "Control mode"),
            ("edfaRawStatusLabel", "Raw status"),
            ("edfaUpdatedLabel", "Sampled at (UTC)"),
        )
        for object_name, caption in readings:
            label = QLabel("—")
            label.setObjectName(object_name)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.reading_labels[object_name] = label
            form.addRow(caption + ":", label)
        telemetry_layout.addLayout(form)
        self.alarm_label = QLabel("Disconnected; alarm bits unavailable.")
        self.alarm_label.setObjectName("edfaAlarmLabel")
        self.alarm_label.setWordWrap(True)
        telemetry_layout.addWidget(self.alarm_label)
        layout.addWidget(telemetry)

        self.error_label = QLabel("")
        self.error_label.setObjectName("edfaErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #b71c1c;")
        layout.addWidget(self.error_label)
        self.safety_label = QLabel(
            "Read-only observations cannot certify safe optical conditions. "
            "This tab provides no emission, gain, diode-current, or optical-power controls."
        )
        self.safety_label.setObjectName("edfaSafetyLabel")
        self.safety_label.setWordWrap(True)
        layout.addWidget(self.safety_label)
        layout.addStretch(1)

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

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self.error_label.setText(message)

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
