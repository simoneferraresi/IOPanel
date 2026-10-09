import json
import logging
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
    assert panel.scan_elapsed_label.text() == "Elapsed: 00:00    Estimated remaining: unavailable"
    assert panel._scan_elapsed_timer.interval() == 1000
    assert panel._scan_elapsed_timer.isActive()

    now[0] = 101.2
    panel._update_scan_elapsed()
    assert panel.scan_elapsed_label.text() == "Elapsed: 00:01    Estimated remaining: unavailable"

    now[0] = 3765.9
    panel._update_scan_elapsed()
    assert panel.scan_elapsed_label.text() == "Elapsed: 61:05    Estimated remaining: unavailable"


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (0, "Elapsed: 00:00    Estimated remaining: 00:02"),
        (1.01, "Elapsed: 00:01    Estimated remaining: 00:01"),
        (2, "Elapsed: 00:02    Estimated duration exceeded"),
        (9, "Elapsed: 00:09    Estimated duration exceeded"),
    ],
)
def test_eta_uses_snapshot_and_reports_overrun(qtbot, monkeypatch, elapsed, expected):
    panel = _panel(qtbot)
    now = [10.0]
    monkeypatch.setattr(control_panel_module.time, "monotonic", lambda: now[0])
    panel._scan_estimated_duration_seconds = 2
    panel._start_scan_activity_ui()
    # Changing editable fields after scan start cannot change the captured estimate.
    panel.motor_speed.setText("99")
    now[0] += elapsed

    panel._update_scan_elapsed()

    assert panel.scan_elapsed_label.text() == expected
    assert (panel.progress_bar.minimum(), panel.progress_bar.maximum()) == (0, 0)


def test_stopping_state_replaces_remaining_estimate(qtbot, monkeypatch):
    panel = _panel(qtbot)
    now = [10.0]
    monkeypatch.setattr(control_panel_module.time, "monotonic", lambda: now[0])
    panel._scan_estimated_duration_seconds = 50
    panel._start_scan_activity_ui()
    panel._scan_stopping = True
    now[0] += 3

    panel._update_scan_elapsed()

    assert panel.scan_elapsed_label.text() == "Elapsed: 00:03    Stopping…"


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
    assert panel._scan_estimated_duration_seconds is None


def test_invalid_scan_validation_does_not_start_activity(qtbot, monkeypatch):
    panel = _panel(qtbot)
    panel.final_wl.setText("1549")
    monkeypatch.setattr(control_panel_module.QMessageBox, "critical", lambda *_args: None)

    panel._start_scan()

    assert not panel.scanning
    assert not panel._scan_elapsed_timer.isActive()
    assert not panel.progress_bar.isVisible()
    assert not panel.scan_elapsed_label.isVisible()


def test_simulated_scan_eta_is_unavailable_and_logs_speed_source(qtbot, caplog):
    device = DummyCT400(scan_duration=0)
    panel = _panel(qtbot, device)
    panel.config.scan_defaults.speed_nm_s = 3
    panel.initial_wl.setText("1550")
    panel.final_wl.setText("1556")
    panel.set_next_scan_id("calibration-run-007")
    timing_events = []
    measurements = []
    panel.scan_timing_ready.connect(timing_events.append)
    panel.scan_data_ready.connect(measurements.append)

    with caplog.at_level(logging.INFO, logger="LabApp.control_panel"):
        panel._start_scan()
        active_label_text = panel.scan_elapsed_label.text()
        active_progress_range = (panel.progress_bar.minimum(), panel.progress_bar.maximum())
        qtbot.waitUntil(lambda: not panel.scanning, timeout=3000)

    assert active_label_text.endswith("Estimated remaining: unavailable")
    assert active_progress_range == (0, 0)
    assert device.scan_wait_end_calls == 1
    record = next(record for record in caplog.records if record.getMessage().startswith("Scan timing summary: "))
    timing = json.loads(record.getMessage().split(": ", 1)[1])
    assert timing["configured_speed_nm_s_at_connect"] == 3
    assert timing["scan_panel_speed_nm_s"] == "10"
    assert timing["scan_panel_speed_differs_from_configured"] is True
    assert timing["simulated"] is True
    assert len(timing_events) == 1
    assert timing_events[0]["scan_id"] == "calibration-run-007"
    assert measurements[0].scan_id == timing_events[0]["scan_id"]
    assert timing_events[0]["gui_start_to_completion_seconds"] >= timing_events[0]["worker_duration_seconds"]
    assert timing_events[0]["gui_completion_dispatch_latency_seconds"] >= 0


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
