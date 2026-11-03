"""Offline Qt coverage for supervised EDFA setting controls and worker cancellation."""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QMessageBox

from hardware.dummy_edfa import DummyEDFA
from hardware.edfa_controls import (
    EDFAWriteKind,
    EDFAWriteOutcome,
    EDFAWriteRequest,
    EDFAWriteResult,
)
from hardware.edfa_types import EDFAState, PowerUnit
from logic.edfa_monitor import EDFAMonitorWorker
from ui.edfa_panel import EDFAReadOnlyPanel


def prepare_panel(qtbot):
    panel = EDFAReadOnlyPanel()
    qtbot.addWidget(panel)
    fake = DummyEDFA()
    fake.connect()
    panel._on_state(EDFAState.IDLE, "Connected / read-only")
    panel._on_telemetry(fake.read_telemetry())
    panel._on_configuration(fake.read_configuration())
    return panel


def test_controls_are_inert_until_connected_with_fresh_readbacks(qtbot):
    panel = EDFAReadOnlyPanel()
    qtbot.addWidget(panel)
    assert not panel.task.worker_thread.isRunning()
    assert not panel.setting_buttons[EDFAWriteKind.POWER].isEnabled()
    assert not panel.device_units_apply.isEnabled()
    assert panel.controls_panel.findChild(QGroupBox, "edfaAmplifierControlsGroup") is None
    assert not hasattr(panel, "emission_button")
    panel.request_shutdown()


def test_editing_never_writes_and_operator_confirmation_can_reject(qtbot, monkeypatch):
    panel = prepare_panel(qtbot)
    requested = []
    panel.control_requested.connect(requested.append)
    assert panel.setting_buttons[EDFAWriteKind.POWER].isEnabled()
    assert not panel.setting_buttons[EDFAWriteKind.GAIN].isEnabled()
    assert not panel.setting_buttons[EDFAWriteKind.CURRENT].isEnabled()
    field = panel.setting_fields[EDFAWriteKind.POWER]
    field.setFocus()
    field.selectAll()
    qtbot.keyClicks(field, "0.3")
    assert panel.setting_states[EDFAWriteKind.POWER].text() == "Pending"
    assert requested == []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.StandardButton.No)
    panel.setting_buttons[EDFAWriteKind.POWER].click()
    assert requested == []
    assert not panel._control_pending
    panel.request_shutdown()


def test_each_physical_action_is_locked_without_site_optin(qtbot):
    panel = EDFAReadOnlyPanel(backend_mode="physical", resource_name="GPIB0::12::INSTR")
    qtbot.addWidget(panel)
    sim = DummyEDFA()
    sim.connect()
    panel._on_state(EDFAState.IDLE, "Connected / read-only")
    panel._on_telemetry(sim.read_telemetry())
    panel._on_configuration(sim.read_configuration())
    assert all(not button.isEnabled() for button in panel.setting_buttons.values())
    assert not panel.device_units_apply.isEnabled()
    panel.request_shutdown()


def test_confirmation_pending_and_result_cleanup(qtbot, monkeypatch):
    panel = prepare_panel(qtbot)
    requests = []
    panel.control_requested.connect(requests.append)
    field = panel.setting_fields[EDFAWriteKind.POWER]
    field.setText("0.4")
    panel._mark_setting_dirty(EDFAWriteKind.POWER)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.StandardButton.Yes)
    panel.request_apply_setting(EDFAWriteKind.POWER)
    assert len(requests) == 1
    assert requests[0].operator_confirmed
    assert panel._control_pending
    assert not panel.read_button.isEnabled()
    assert not panel.start_button.isEnabled()
    result = EDFAWriteResult(requests[0], EDFAWriteOutcome.REJECTED, 1.0, None, "No write sent")
    panel._on_control_finished(result)
    assert not panel._control_pending
    assert "REJECTED" in panel.control_feedback.text()
    panel.request_shutdown()


def test_device_unit_combobox_does_not_write_until_separate_confirmation(qtbot, monkeypatch):
    panel = prepare_panel(qtbot)
    requests = []
    panel.control_requested.connect(requests.append)
    panel.device_units_combo.setCurrentIndex(1)
    assert requests == []
    assert panel.device_units_apply.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kw: QMessageBox.StandardButton.Yes)
    panel.request_device_units()
    assert len(requests) == 1
    assert requests[0].kind is EDFAWriteKind.POWER_UNITS
    assert requests[0].value is PowerUnit.DBM
    panel.request_shutdown()
    assert requests[0].cancelled.is_set()


def test_worker_rejects_cancelled_queued_write_without_simulated_write():
    simulated = DummyEDFA()
    worker = EDFAMonitorWorker(simulator_factory=lambda: simulated)
    results = []
    worker.control_finished.connect(results.append)
    worker.connect_instrument("simulation", "", 3000, "none", "none")
    req = EDFAWriteRequest(
        EDFAWriteKind.POWER, 0.3, PowerUnit.W, operator_confirmed=True,
    )
    req.cancelled.set()
    worker.apply_control(req)
    assert results and results[-1].outcome is EDFAWriteOutcome.CANCELLED
    assert simulated.write_commands == []
    worker.shutdown()
