"""EDFA tab: read-only monitoring and separately gated supervised controls."""

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
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from hardware.edfa_controls import (
    EDFAWriteKind,
    EDFAWriteOutcome,
    EDFAWritePolicy,
    EDFAWriteRequest,
    EDFAWriteResult,
)
from hardware.edfa_types import (
    EDFAConfiguration,
    EDFAControlMode,
    EDFAOpticalPower,
    EDFAState,
    EDFATelemetry,
    PowerUnit,
)
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
    configuration_requested = Signal()
    control_requested = Signal(object)
    start_requested = Signal(int)
    stop_requested = Signal()
    shutdown_requested = Signal()
    shutdown_complete = Signal()
    shutdown_timeout = Signal(str)
    connection_status_changed = Signal(object, str)
    observations_changed = Signal(str)

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
        write_policy: EDFAWritePolicy | None = None,
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
        self._identity_text = ""
        self._last_observations: str | None = None
        self._last_error: str | None = None
        self._last_configuration: EDFAConfiguration | None = None
        self._configuration_pending = False
        self._control_pending = False
        self._pending_control: EDFAWriteRequest | None = None
        self._last_telemetry: EDFATelemetry | None = None
        self._write_policy = write_policy or EDFAWritePolicy()
        self._field_dirty: dict[EDFAWriteKind, bool] = {
            kind: False for kind in (EDFAWriteKind.POWER, EDFAWriteKind.GAIN, EDFAWriteKind.CURRENT)
        }

        self.worker = worker if worker is not None else EDFAMonitorWorker(write_policy=self._write_policy)
        self.task = TaskRunner(self.worker, auto_start_run=False)
        self.task.worker_thread.setObjectName("EDFA-readonly-monitor")
        self.task.worker_thread.started.connect(self.worker.initialize)
        self.task.worker_thread.finished.connect(self._on_thread_finished, Qt.ConnectionType.QueuedConnection)
        self.connect_requested.connect(self.worker.connect_instrument)
        self.disconnect_requested.connect(self.worker.disconnect_instrument)
        self.read_requested.connect(self.worker.read_once)
        self.configuration_requested.connect(self.worker.read_configuration)
        self.control_requested.connect(self.worker.apply_control)
        self.start_requested.connect(self.worker.start_monitoring)
        self.stop_requested.connect(self.worker.stop_monitoring)
        self.shutdown_requested.connect(self.worker.shutdown)
        self.worker.state_changed.connect(self._on_state)
        self.worker.identity_ready.connect(self._set_identity)
        self.worker.telemetry_ready.connect(self._on_telemetry)
        self.worker.configuration_ready.connect(self._on_configuration)
        self.worker.control_finished.connect(self._on_control_finished)
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

        # Connection, backend mode and identity are shown in the Instruments
        # menu and the persistent status bar rather than duplicate tab widgets.
        operation = QGroupBox("Read-only acquisition")
        operation.setObjectName("edfaOperationGroup")
        style_group_box_title(operation)
        operation_layout = QVBoxLayout(operation)
        operation_layout.setSpacing(7)
        self.read_button = QPushButton("Read once")
        self.read_button.setObjectName("edfaReadButton")
        self.read_button.setMinimumHeight(35)
        operation_layout.addWidget(self.read_button)
        self.configuration_button = QPushButton("Read configuration")
        self.configuration_button.setObjectName("edfaConfigurationButton")
        self.configuration_button.setMinimumHeight(35)
        self.configuration_button.setToolTip(
            "On-demand configuration and limits: never part of periodic monitoring; "
            "M3.1 readbacks were qualified on firmware Rev 1.1.175 (2026-11-03)."
        )
        operation_layout.addWidget(self.configuration_button)
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
            "No emission enablement or mode selection is available. Readbacks do not "
            "prove optical safety; physical setpoint writes require site approval."
        )
        self.safety_label.setObjectName("edfaSafetyLabel")
        self.safety_label.setWordWrap(True)
        controls_layout.addWidget(self.safety_label)
        controls_layout.addStretch(1)
        root.addWidget(self.controls_panel, stretch=0)

        self.dashboard_panel = QWidget(self)
        self.dashboard_panel.setObjectName("edfaTelemetryContent")
        self.dashboard_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dashboard_layout = QHBoxLayout(self.dashboard_panel)
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
        optical_column = QWidget()
        optical_stack = QVBoxLayout(optical_column)
        optical_stack.setContentsMargins(0, 0, 0, 0)
        optical_stack.setSpacing(10)
        optical_stack.addWidget(optical)

        configuration = QGroupBox("Configuration and limits (manual refresh)")
        configuration.setObjectName("edfaConfigurationGroup")
        style_group_box_title(configuration)
        configuration_grid = QGridLayout(configuration)
        configuration_grid.setContentsMargins(10, 14, 10, 8)
        configuration_grid.setSpacing(5)
        for column in range(2):
            configuration_grid.setColumnStretch(column, 1)
        metric(configuration_grid, "edfaPowerLimitLabel", "Output power limit", 0, 0, size=12)
        metric(configuration_grid, "edfaCurrentSetpointLabel", "Diode current setpoint", 0, 1, size=12)
        metric(configuration_grid, "edfaCurrentLimitLabel", "Diode current limit", 1, 0, size=12)
        metric(configuration_grid, "edfaGainSetpointLabel", "Gain setpoint", 1, 1, size=12)
        metric(configuration_grid, "edfaGainLimitLabel", "Gain limit", 2, 0, size=12)
        metric(configuration_grid, "edfaConfigUpdatedLabel", "Settings read at (UTC)", 2, 1, size=11)
        optical_stack.addWidget(configuration)
        optical_stack.addStretch(1)
        dashboard_layout.addWidget(optical_column, stretch=1)

        health = QGroupBox("Amplifier status")
        health.setObjectName("edfaHealthGroup")
        style_group_box_title(health)
        health_grid = QGridLayout(health)
        health_grid.setContentsMargins(10, 14, 10, 8)
        health_grid.setSpacing(6)
        # Two columns keep long status words and UTC timestamps readable.
        for column in range(2):
            health_grid.setColumnStretch(column, 1)
        metric(health_grid, "edfaCurrentLabel", "Pump diode current", 0, 0, size=13)
        metric(health_grid, "edfaTemperatureLabel", "Case temperature", 0, 1, size=13)
        metric(health_grid, "edfaControlModeLabel", "Control mode", 1, 0, size=13)
        metric(health_grid, "edfaReflectionLabel", "Back-reflection (% of max.)", 1, 1, size=12)
        metric(health_grid, "edfaRawStatusLabel", "Raw status word", 2, 0, size=12)
        metric(health_grid, "edfaUpdatedLabel", "Sampled at (UTC)", 2, 1, size=12)
        health_column = QWidget()
        health_stack = QVBoxLayout(health_column)
        health_stack.setContentsMargins(0, 0, 0, 0)
        health_stack.setSpacing(10)
        health_stack.addWidget(health)
        health_stack.addWidget(self._build_amplifier_controls())
        health_stack.addStretch(1)
        dashboard_layout.addWidget(health_column, stretch=1)
        root.addWidget(self.dashboard_panel, stretch=1)

        self.read_button.clicked.connect(self.request_read)
        self.configuration_button.clicked.connect(self.request_configuration)
        self.start_button.clicked.connect(self.request_monitoring)
        self.stop_button.clicked.connect(self.request_stop)
        self._update_controls()

    def _build_amplifier_controls(self) -> QGroupBox:
        """Supervised controls live in the expanding dashboard, never the left column."""
        group = QGroupBox("Amplifier controls (supervised)")
        group.setObjectName("edfaAmplifierControlsGroup")
        style_group_box_title(group)
        grid = QGridLayout(group)
        grid.setContentsMargins(10, 14, 10, 8)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(5)
        grid.setColumnStretch(1, 1)
        self.setting_fields: dict[EDFAWriteKind, QLineEdit] = {}
        self.setting_buttons: dict[EDFAWriteKind, QPushButton] = {}
        self.setting_states: dict[EDFAWriteKind, QLabel] = {}
        self.setting_limits: dict[EDFAWriteKind, QLabel] = {}
        definitions = (
            (EDFAWriteKind.POWER, "Power setpoint", "W / dBm"),
            (EDFAWriteKind.GAIN, "Gain setpoint", "dB"),
            (EDFAWriteKind.CURRENT, "Current setpoint", "A"),
        )
        for i, (kind, caption, unit) in enumerate(definitions):
            row = 2 * i
            grid.addWidget(QLabel(caption), row, 0)
            field = QLineEdit()
            field.setObjectName(f"edfa{kind.name.title()}Entry")
            field.setPlaceholderText("Read device first")
            field.setToolTip("Editing never sends a command. Apply requires a separate confirmation.")
            field.textEdited.connect(lambda _text, k=kind: self._mark_setting_dirty(k))
            grid.addWidget(field, row, 1)
            units = QLabel(unit)
            if kind is EDFAWriteKind.POWER:
                self.power_entry_unit = units
            grid.addWidget(units, row, 2)
            apply = QPushButton("Apply")
            apply.setObjectName(f"edfa{kind.name.title()}Apply")
            apply.clicked.connect(lambda _checked=False, k=kind: self.request_apply_setting(k))
            grid.addWidget(apply, row, 3)
            detail = QLabel("Device readback —; hardware max —")
            detail.setWordWrap(True)
            detail.setFont(make_font("sans", 9))
            grid.addWidget(detail, row + 1, 0, 1, 3)
            state = QLabel("—")
            state.setFont(make_font("sans", 9))
            grid.addWidget(state, row + 1, 3)
            self.setting_fields[kind] = field
            self.setting_buttons[kind] = apply
            self.setting_states[kind] = state
            self.setting_limits[kind] = detail

        grid.addWidget(QLabel("Device power units"), 6, 0)
        self.device_units_combo = QComboBox()
        self.device_units_combo.setObjectName("edfaDeviceUnitsCombo")
        self.device_units_combo.addItem("W", PowerUnit.W)
        self.device_units_combo.addItem("dBm", PowerUnit.DBM)
        self.device_units_combo.currentIndexChanged.connect(lambda _index: self._update_controls())
        grid.addWidget(self.device_units_combo, 6, 1, 1, 2)
        self.device_units_apply = QPushButton("Change")
        self.device_units_apply.setObjectName("edfaDeviceUnitsApply")
        self.device_units_apply.setToolTip("Changes instrument reporting units; not a display-only conversion")
        self.device_units_apply.clicked.connect(self.request_device_units)
        grid.addWidget(self.device_units_apply, 6, 3)

        self.control_feedback = QLabel(
            "Setpoint writes are disabled by default. Mode changes and physical emission ON are unavailable."
        )
        self.control_feedback.setObjectName("edfaControlFeedback")
        self.control_feedback.setWordWrap(True)
        self.control_feedback.setFont(make_font("sans", 9))
        grid.addWidget(self.control_feedback, 7, 0, 1, 4)
        return group

    def _mark_setting_dirty(self, kind: EDFAWriteKind) -> None:
        self._field_dirty[kind] = True
        self.setting_states[kind].setText("Pending")
        self._update_controls()

    def _populate_setting(self, kind: EDFAWriteKind, value: float | None, text: str) -> None:
        self.setting_limits[kind].setText(text)
        if value is not None and not self._field_dirty[kind]:
            self.setting_fields[kind].setText(f"{value:.9g}")
            self.setting_states[kind].setText("Readback")

    def request_apply_setting(self, kind: EDFAWriteKind) -> None:
        if kind not in self.setting_fields or not self.setting_buttons[kind].isEnabled():
            return
        config = self._last_configuration
        if config is None:
            return
        try:
            number = float(self.setting_fields[kind].text().strip())
            unit = (
                (PowerUnit.DBM if config.status.logarithmic_power_units else PowerUnit.W)
                if kind is EDFAWriteKind.POWER else None
            )
            request = EDFAWriteRequest(kind, number, unit=unit)
        except (ValueError, TypeError) as exc:
            self._on_error(f"Invalid proposed EDFA setpoint: {exc}")
            return
        self._confirm_and_send(request)

    def request_device_units(self) -> None:
        if not self.device_units_apply.isEnabled():
            return
        target = self.device_units_combo.currentData()
        try:
            request = EDFAWriteRequest(EDFAWriteKind.POWER_UNITS, target)
        except ValueError as exc:
            self._on_error(str(exc))
            return
        self._confirm_and_send(request)

    def _confirm_and_send(self, request: EDFAWriteRequest) -> None:
        from datetime import UTC, datetime, timedelta

        config = self._last_configuration
        if (
            config is None or self._last_telemetry is None
            or datetime.now(UTC) - config.acquired_at_utc > timedelta(minutes=2)
        ):
            self._on_error("Configuration readback is missing or older than 2 minutes; refresh before Apply.")
            return
        if self._control_pending or self._operation_pending or self._monitoring or self._shutdown_requested:
            return
        is_physical = self.backend_mode == "physical"
        message = (
            f"Proposed {request.kind.value}: {request.value}"
            + (f" {request.unit.value}" if request.unit is not None else "")
            + "\n\nDevice setpoint/limits will be queried again before any write. "
            "One command will be sent at most once; post-write readback may fail.\n\n"
            "Do you authorize this single setting change?"
        )
        if is_physical:
            message += (
                "\n\nCONFIRM INDEPENDENTLY: physical emission is OFF at the amplifier; "
                "key switch, interlocks, optical route and laboratory authorization were checked. "
                "The internal status and 'Off' token are NOT independent safety checks."
                f"\nApproval: {self._write_policy.site_reference}."
            )
        answer = QMessageBox.question(
            self, "Confirm supervised EDFA operation", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        request = EDFAWriteRequest(
            request.kind, request.value, unit=request.unit,
            operator_confirmed=True, emission_off_attested=is_physical,
        )
        self._pending_control = request
        self._control_pending = True
        self.control_feedback.setText(f"Pending: {request.kind.value}; device value not yet confirmed.")
        self._update_controls()
        self.control_requested.emit(request)

    @Slot(object)
    def _on_control_finished(self, result: EDFAWriteResult) -> None:
        if self._shutdown_requested:
            return
        if result.request is not self._pending_control:
            return
        self._control_pending = False
        self._pending_control = None
        self.control_feedback.setText(
            f"{result.outcome.value.upper()}: requested={result.request.value}; "
            f"before={result.before}; after={result.after}. {result.message}"
        )
        if result.outcome is EDFAWriteOutcome.CONFIRMED:
            if result.request.kind is not EDFAWriteKind.POWER_UNITS:
                self._field_dirty[result.request.kind] = False
            else:
                self._clear_configuration()
            if result.telemetry is not None:
                self._on_telemetry(result.telemetry)
            if result.configuration is not None:
                self._on_configuration(result.configuration)
        elif result.outcome in (EDFAWriteOutcome.UNKNOWN, EDFAWriteOutcome.MISMATCH):
            self._clear_telemetry()
            self.error_label.setText("EDFA state UNKNOWN after write. Physically verify before reconnecting.")
            self.error_label.show()
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
        self._identity_text = ""
        self._last_observations = None
        self._last_error = None
        self._last_configuration = None
        self._configuration_pending = False
        self._clear_configuration()
        self._activity = "Connecting"
        self.error_label.clear()
        self.error_label.hide()
        self._update_controls()
        self._publish_status_observations()
        self.connection_status_changed.emit(self._backend_state, self._activity)
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
        self._last_error = None
        self._update_controls()
        self._publish_status_observations()
        self.connection_status_changed.emit(self._backend_state, self._activity)
        self.disconnect_requested.emit()

    def request_read(self) -> None:
        if self._shutdown_requested or self._operation_pending or self._backend_state is not EDFAState.IDLE:
            return
        self.read_requested.emit()

    def request_configuration(self) -> None:
        if (
            self._shutdown_requested
            or self._operation_pending
            or self._backend_state is not EDFAState.IDLE
            or self._monitoring
            or self._configuration_pending
        ):
            return
        if self.backend_mode == "physical":
            result = QMessageBox.question(
                self,
                "Confirm extended read-only EDFA queries",
                "Five new configuration readbacks have not yet been qualified on this firmware. "
                "Use the supervised CLI command-by-command procedure first. "
                "These reads do not enable emission, but can change GPIB remote/local state "
                "and may time out. Is this readback explicitly authorized?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
        self._configuration_pending = True
        self._update_controls()
        self.configuration_requested.emit()

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
        if state is not EDFAState.IDLE:
            self._configuration_pending = False
            if state in (EDFAState.DISCONNECTED, EDFAState.ERROR):
                self._clear_telemetry()
        self._update_controls()
        self._publish_status_observations()
        self.connection_status_changed.emit(state, activity)

    @Slot(str)
    def _set_identity(self, identity: str) -> None:
        if not self._shutdown_requested:
            self._identity_text = identity
            self._publish_status_observations()

    @Slot(object)
    def _on_telemetry(self, report: EDFATelemetry) -> None:
        if self._shutdown_requested:
            return
        previous = self._last_configuration
        relevant_bits = (1 << 0) | (1 << 9) | (1 << 16) | (1 << 18) | (1 << 20)
        if previous is not None and (previous.status.raw_word ^ report.status.raw_word) & relevant_bits:
            self._clear_configuration()
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
        self._last_observations = "; ".join(alarms) if alarms else "No alarm bits asserted (not a safety check)"
        self._last_error = None
        self._publish_status_observations()
        self.error_label.clear()
        self.error_label.hide()

    @Slot(object)
    def _on_configuration(self, report: EDFAConfiguration) -> None:
        if self._shutdown_requested:
            return
        self._last_configuration = report
        self._configuration_pending = False
        self._update_controls()
        gain_available = report.status.agc_available
        values = {
            "edfaPowerLimitLabel": format_edfa_power(report.power_limit),
            "edfaCurrentSetpointLabel": f"{report.diode_current_setpoint_a:.5g} A",
            "edfaCurrentLimitLabel": f"{report.diode_current_limit_a:.5g} A",
            "edfaGainSetpointLabel": (
                f"{report.gain_setpoint_db:.5g} dB"
                if report.gain_setpoint_db is not None
                else "Not available"
                if not gain_available
                else "—"
            ),
            "edfaGainLimitLabel": (
                f"{report.gain_limit_db:.5g} dB"
                if report.gain_limit_db is not None
                else "Not available"
                if not gain_available
                else "—"
            ),
            "edfaConfigUpdatedLabel": report.acquired_at_utc.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for key, value in values.items():
            self.reading_labels[key].setText(value)
        self._last_error = None
        self.error_label.clear()
        self.error_label.hide()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._configuration_pending = False
        self._update_controls()
        self._last_error = message
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        self._publish_status_observations()

    def _clear_configuration(self) -> None:
        self._last_configuration = None
        for key in (
            "edfaPowerLimitLabel",
            "edfaCurrentSetpointLabel",
            "edfaCurrentLimitLabel",
            "edfaGainSetpointLabel",
            "edfaGainLimitLabel",
            "edfaConfigUpdatedLabel",
        ):
            self.reading_labels[key].setText("—")

    def _clear_telemetry(self) -> None:
        for label in self.reading_labels.values():
            label.setText("—")
        self._last_observations = None
        self._last_configuration = None

    def _publish_status_observations(self) -> None:
        """Publish connection and last observation together, never a stale disconnect."""
        prefix = "EDFA [SIM]" if self.backend_mode == "simulation" else "EDFA [GPIB]"
        if self._last_error:
            detail = "Error — see EDFA tab"
        elif self._operation_pending:
            detail = self._activity
        elif self._backend_state is EDFAState.DISCONNECTED:
            detail = "Disconnected"
        elif self._backend_state is EDFAState.ERROR:
            detail = "Recovery required"
        elif self._last_observations is not None:
            detail = self._last_observations
        else:
            detail = "Monitoring — awaiting telemetry" if self._monitoring else "Connected — awaiting telemetry"
        self.observations_changed.emit(f"{prefix}: {detail}")

    def _update_controls(self) -> None:
        ready = self._backend_state is EDFAState.IDLE
        blocked = self._operation_pending or self._shutdown_requested
        self.read_button.setEnabled(not blocked and ready)
        self.configuration_button.setEnabled(
            not blocked and ready and not self._monitoring and not self._configuration_pending
        )
        self.start_button.setEnabled(not blocked and ready and not self._monitoring)
        self.stop_button.setEnabled(not self._shutdown_requested and self._monitoring)
        self.interval_combo.setEnabled(not blocked and not self._monitoring)

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
