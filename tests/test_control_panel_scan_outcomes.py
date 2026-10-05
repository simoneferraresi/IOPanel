import threading

import pytest
from PySide6.QtWidgets import QMessageBox

from config_model import AppConfig
from hardware.ct400_types import ScanWaitResult
from hardware.dummy_ct400 import DummyCT400
from ui.constants import (
    MSG_SCAN_CANCELLED,
    MSG_SCAN_COMPLETED,
    MSG_SCAN_COMPLETED_WITH_WARNING,
    MSG_SCAN_FAILED,
    MSG_SCAN_READY,
    MSG_SCAN_SCANNING,
    MSG_SCAN_STOPPING,
)
from ui.control_panel import CT400ControlPanel, ScanSettings


def _panel(qtbot, device=None):
    panel = CT400ControlPanel(ScanSettings(), device or DummyCT400(scan_duration=0), AppConfig())
    qtbot.addWidget(panel)
    panel.initial_wl.setText("1550")
    panel.final_wl.setText("1550.004")
    panel.resolution.setText("1")
    panel.motor_speed.setText("10")
    panel.laser_power.setText("1")
    panel.show()
    return panel


def _gated_scan(device, result_code, message="controlled result"):
    entered = threading.Event()
    release = threading.Event()

    def wait_end():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release ScanWaitEnd")
        return ScanWaitResult(result_code, message)

    device.scan_wait_end = wait_end
    return entered, release


def _wait_for_finish(qtbot, panel):
    qtbot.waitUntil(lambda: not panel.scanning, timeout=3000)
    if panel.scan_thread is not None:
        panel.scan_thread.wait(3000)


def test_status_lifecycle_success_and_previous_outcome_is_replaced(qtbot):
    device = DummyCT400(scan_duration=0)
    entered, release = _gated_scan(device, 0)
    panel = _panel(qtbot, device)

    assert panel.scan_status_label.text() == MSG_SCAN_READY
    try:
        panel._start_scan()
        assert panel.scan_status_label.text() == MSG_SCAN_SCANNING
        assert entered.wait(2)
        release.set()
        _wait_for_finish(qtbot, panel)
        assert panel.scan_status_label.text() == MSG_SCAN_COMPLETED

        # Hold the next valid scan inside ScanWaitEnd to inspect its active state.
        entered2, release2 = _gated_scan(device, 0)
        panel._start_scan()
        assert entered2.wait(2)
        assert panel.scan_status_label.text() == MSG_SCAN_SCANNING
        release2.set()
        _wait_for_finish(qtbot, panel)
        assert panel.scan_status_label.text() == MSG_SCAN_COMPLETED
    finally:
        release.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)


def test_stop_stays_stopping_until_thread_finishes(qtbot, monkeypatch):
    device = DummyCT400(scan_duration=0)
    entered, release = _gated_scan(device, 1, "cancelled")
    panel = _panel(qtbot, device)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: dialogs.append(args))

    try:
        panel._start_scan()
        assert entered.wait(2)
        assert panel._scan_elapsed_timer.isActive()

        panel._toggle_scan()

        assert panel.scan_status_label.text() == MSG_SCAN_STOPPING
        assert panel._scan_elapsed_timer.isActive()
        assert panel.scan_thread.isRunning()

        release.set()
        _wait_for_finish(qtbot, panel)
        assert panel.scan_status_label.text() == MSG_SCAN_CANCELLED
        assert not panel._scan_elapsed_timer.isActive()
        assert dialogs == []
    finally:
        release.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)


def test_failed_stop_request_keeps_stopping_status_until_scan_result(qtbot, monkeypatch):
    device = DummyCT400(scan_duration=0)
    entered, release = _gated_scan(device, 1, "cancelled after failed stop request")

    def fail_stop():
        raise RuntimeError("stop request failed")

    device.stop_scan = fail_stop
    panel = _panel(qtbot, device)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: dialogs.append(args))

    try:
        panel._start_scan()
        assert entered.wait(2)
        panel._toggle_scan()

        assert panel.scan_status_label.text() == MSG_SCAN_STOPPING
        assert dialogs and dialogs[0][1] == "CT400 Stop Failed"
        assert panel.scan_thread.isRunning()

        release.set()
        _wait_for_finish(qtbot, panel)
        assert panel.scan_status_label.text() == MSG_SCAN_CANCELLED
    finally:
        release.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)


def test_warning_outcome_keeps_detailed_warning_signal(qtbot):
    device = DummyCT400(scan_duration=0)
    entered, release = _gated_scan(device, 100, "resolution adjusted")
    panel = _panel(qtbot, device)
    warnings = []
    panel.scan_warning.connect(warnings.append)

    try:
        panel._start_scan()
        assert entered.wait(2)
        release.set()
        _wait_for_finish(qtbot, panel)

        assert panel.scan_status_label.text() == MSG_SCAN_COMPLETED_WITH_WARNING
        assert warnings == ["CT400 scan warning 100: resolution adjusted"]
    finally:
        release.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)


@pytest.mark.parametrize(
    ("result_code", "dialog_title"),
    [(2, "CT400 Scan Error"), (6, "Unexpected CT400 Result")],
    ids=["fatal", "unexpected"],
)
def test_terminal_error_is_failed_and_preserves_critical_dialog(qtbot, monkeypatch, result_code, dialog_title):
    device = DummyCT400(scan_duration=0)
    entered, release = _gated_scan(device, result_code, "controlled error")
    panel = _panel(qtbot, device)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: dialogs.append(args))

    try:
        panel._start_scan()
        assert entered.wait(2)
        release.set()
        _wait_for_finish(qtbot, panel)

        assert panel.scan_status_label.text() == MSG_SCAN_FAILED
        assert len(dialogs) == 1
        assert dialogs[0][1] == dialog_title
        assert "controlled error" in dialogs[0][2]
    finally:
        release.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)


def test_generic_exception_sets_failed_after_critical_dialog(qtbot, monkeypatch):
    device = DummyCT400(scan_duration=0)

    def fail_sampling(_resolution):
        raise RuntimeError("sampling setup broke")

    device.set_sampling_res = fail_sampling
    panel = _panel(qtbot, device)
    dialogs = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: dialogs.append(args))

    panel._start_scan()
    _wait_for_finish(qtbot, panel)

    assert panel.scan_status_label.text() == MSG_SCAN_FAILED
    assert len(dialogs) == 1
    assert dialogs[0][1] == "CT400 Operation Error"
    assert "sampling setup broke" in dialogs[0][2]
