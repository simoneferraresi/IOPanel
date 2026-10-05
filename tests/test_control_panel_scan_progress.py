import threading

import pytest

from config_model import AppConfig
from hardware.ct400_types import ScanWaitResult
from hardware.dummy_ct400 import DummyCT400
from ui import control_panel as control_panel_module
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


def test_scan_activity_initial_state(qtbot):
    panel = _panel(qtbot)

    assert not panel.progress_bar.isVisible()
    assert not panel.scan_elapsed_label.isVisible()
    assert panel.scan_elapsed_label.text() == "Elapsed: 00:00"
    assert not panel._scan_elapsed_timer.isActive()


def test_scan_activity_is_indeterminate_and_elapsed_time_is_deterministic(qtbot, monkeypatch):
    panel = _panel(qtbot)
    now = [100.0]
    monkeypatch.setattr(control_panel_module.time, "monotonic", lambda: now[0])

    panel._start_scan_activity_ui()

    assert panel.progress_bar.isVisible()
    assert (panel.progress_bar.minimum(), panel.progress_bar.maximum()) == (0, 0)
    assert not panel.progress_bar.isTextVisible()
    assert panel.scan_elapsed_label.isVisible()
    assert panel.scan_elapsed_label.text() == "Elapsed: 00:00"
    assert panel._scan_elapsed_timer.interval() == 1000
    assert panel._scan_elapsed_timer.isActive()

    now[0] = 101.2
    panel._update_scan_elapsed()
    assert panel.scan_elapsed_label.text() == "Elapsed: 00:01"

    now[0] = 3765.9
    panel._update_scan_elapsed()
    assert panel.scan_elapsed_label.text() == "Elapsed: 61:05"


def test_scan_activity_reset_is_complete_and_idempotent(qtbot):
    panel = _panel(qtbot)
    panel._start_scan_activity_ui()

    panel._reset_scan_ui()
    panel._reset_scan_ui()

    assert not panel._scan_elapsed_timer.isActive()
    assert panel._scan_started_at is None
    assert (panel.progress_bar.minimum(), panel.progress_bar.maximum()) == (0, 100)
    assert panel.progress_bar.value() == 0
    assert not panel.progress_bar.isVisible()
    assert not panel.scan_elapsed_label.isVisible()
    assert panel.scan_elapsed_label.text() == "Elapsed: 00:00"


def test_invalid_scan_validation_does_not_start_activity(qtbot, monkeypatch):
    panel = _panel(qtbot)
    panel.final_wl.setText("1549")
    monkeypatch.setattr(control_panel_module.QMessageBox, "critical", lambda *_args: None)

    panel._start_scan()

    assert not panel.scanning
    assert not panel._scan_elapsed_timer.isActive()
    assert not panel.progress_bar.isVisible()
    assert not panel.scan_elapsed_label.isVisible()


def test_stop_request_keeps_busy_ui_until_cancelled_worker_finishes(qtbot):
    device = DummyCT400(scan_duration=0)
    wait_entered = threading.Event()
    release_wait = threading.Event()

    def gated_wait_end():
        wait_entered.set()
        if not release_wait.wait(5):
            raise TimeoutError("test did not release ScanWaitEnd")
        return ScanWaitResult(1, "Measurement cancelled by user.")

    device.scan_wait_end = gated_wait_end
    panel = _panel(qtbot, device)

    try:
        panel._start_scan()
        assert wait_entered.wait(2)
        qtbot.waitUntil(lambda: panel.scan_thread is not None and panel.scan_thread.isRunning())
        assert panel.progress_bar.isVisible()
        assert panel._scan_elapsed_timer.isActive()

        panel._toggle_scan()

        assert panel.scan_btn.text() == "Stopping Scan…"
        assert not panel.scan_btn.isEnabled()
        assert panel.progress_bar.isVisible()
        assert (panel.progress_bar.minimum(), panel.progress_bar.maximum()) == (0, 0)
        assert panel.scan_elapsed_label.isVisible()
        assert panel._scan_elapsed_timer.isActive()

        release_wait.set()
        qtbot.waitUntil(lambda: not panel.scanning, timeout=3000)

        assert not panel._scan_elapsed_timer.isActive()
        assert not panel.progress_bar.isVisible()
        assert not panel.scan_elapsed_label.isVisible()
        assert panel.scan_elapsed_label.text() == "Elapsed: 00:00"
    finally:
        release_wait.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)


@pytest.mark.parametrize("result_code", [0, 100, 2], ids=["success", "warning", "fatal-error"])
def test_terminal_scan_results_reset_activity_after_thread_finishes(qtbot, monkeypatch, result_code):
    device = DummyCT400(scan_duration=0)
    wait_entered = threading.Event()
    release_wait = threading.Event()

    def gated_wait_end():
        wait_entered.set()
        if not release_wait.wait(5):
            raise TimeoutError("test did not release ScanWaitEnd")
        return ScanWaitResult(result_code, "controlled test result")

    device.scan_wait_end = gated_wait_end
    panel = _panel(qtbot, device)
    monkeypatch.setattr(control_panel_module.QMessageBox, "critical", lambda *_args: None)

    try:
        panel._start_scan()
        assert wait_entered.wait(2)
        assert panel._scan_elapsed_timer.isActive()
        assert panel.progress_bar.isVisible()
        assert panel.scan_elapsed_label.isVisible()

        release_wait.set()
        qtbot.waitUntil(lambda: not panel.scanning, timeout=3000)

        assert not panel._scan_elapsed_timer.isActive()
        assert not panel.progress_bar.isVisible()
        assert not panel.scan_elapsed_label.isVisible()
        assert panel.scan_elapsed_label.text() == "Elapsed: 00:00"
    finally:
        release_wait.set()
        if panel.scan_thread is not None:
            panel.scan_thread.wait(3000)
