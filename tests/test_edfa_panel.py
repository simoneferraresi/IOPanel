"""Simulation-only tests for the M3 read-only EDFA tab and thread ownership."""

from __future__ import annotations

import threading

from PySide6.QtWidgets import QMessageBox

from hardware.dummy_edfa import DummyEDFA
from hardware.edfa_types import EDFAOpticalPower, EDFAState, PowerUnit
from logic.edfa_monitor import EDFAMonitorWorker
from ui.edfa_panel import EDFAReadOnlyPanel, format_edfa_power


def test_tab_starts_inert_and_offline(qtbot):
    panel = EDFAReadOnlyPanel()
    qtbot.addWidget(panel)
    assert panel.backend_mode == "simulation"
    assert panel._backend_state is EDFAState.DISCONNECTED
    assert not panel.task.worker_thread.isRunning()
    assert panel.connect_button.isEnabled()
    assert not panel.read_button.isEnabled()
    assert not panel.disconnect_button.isEnabled()
    assert not hasattr(panel, "emission_button")
    assert "no emission" in panel.safety_label.text().lower()
    panel.request_shutdown()
    assert panel.shutdown_finished


def test_simulation_worker_thread_connect_read_monitor_disconnect_and_shutdown(qtbot):
    worker_thread_ids: list[int] = []

    def make_simulator():
        worker_thread_ids.append(threading.get_ident())
        return DummyEDFA()

    panel = EDFAReadOnlyPanel(worker=EDFAMonitorWorker(simulator_factory=make_simulator))
    qtbot.addWidget(panel)
    gui_thread_id = threading.get_ident()
    panel.connect_button.click()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.IDLE, timeout=3000)
    assert worker_thread_ids and worker_thread_ids[0] != gui_thread_id
    assert "SIMULATION" in panel.mode_label.text()
    panel.read_button.click()
    qtbot.waitUntil(lambda: panel.reading_labels["edfaRawStatusLabel"].text() != "—", timeout=3000)
    assert panel.reading_labels["edfaOutputLabel"].text() == "0 W"
    panel.start_button.click()
    qtbot.waitUntil(lambda: panel._monitoring, timeout=3000)
    panel.stop_button.click()
    qtbot.waitUntil(lambda: not panel._monitoring, timeout=3000)
    panel.disconnect_button.click()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.DISCONNECTED, timeout=3000)
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=3000)


def test_reject_physical_connection_without_operator_confirmation(qtbot, monkeypatch):
    panel = EDFAReadOnlyPanel(backend_mode="physical", resource_name="GPIB0::12::INSTR")
    qtbot.addWidget(panel)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    panel.connect_button.click()
    assert panel._backend_state is EDFAState.DISCONNECTED
    assert not panel.task.worker_thread.isRunning()
    panel.request_shutdown()


def test_missing_physical_resource_never_creates_worker_thread(qtbot):
    panel = EDFAReadOnlyPanel(backend_mode="physical", resource_name="")
    qtbot.addWidget(panel)
    panel.connect_button.click()
    assert "resource" in panel.error_label.text()
    assert not panel.task.worker_thread.isRunning()
    panel.request_shutdown()


def test_off_reply_is_not_rendered_as_a_measured_zero():
    assert format_edfa_power(None, nonnumeric_reply="Off").startswith("Off")
    assert format_edfa_power(None, nonnumeric_reply="Off") != "0 W"
    assert format_edfa_power(EDFAOpticalPower(0.0, PowerUnit.W)) == "0 W"


def test_failed_simulated_telemetry_stops_monitoring_without_blocking_gui(qtbot):
    class FailingEDFA(DummyEDFA):
        def read_telemetry(self):
            raise OSError("simulated EDFA communication failure")

    panel = EDFAReadOnlyPanel(worker=EDFAMonitorWorker(simulator_factory=FailingEDFA))
    qtbot.addWidget(panel)
    panel.connect_button.click()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.IDLE, timeout=3000)
    panel.start_button.click()
    qtbot.waitUntil(lambda: "telemetry failed" in panel.error_label.text().lower(), timeout=3000)
    assert not panel._monitoring
    panel.disconnect_button.click()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.DISCONNECTED, timeout=3000)
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=3000)
