"""Hardware-free GUI acceptance tests for the real campaign control path."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QMessageBox

from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400 import CT400
from hardware.ct400_types import LaserInput
from ui.ct400_calibration_campaign import CT400SupervisedCampaignDialog
from ui.main_window import CT400OperationState, MainWindow


def _window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _window: None)
    config = AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "simulation"}})
    config.scan_defaults.input_port = 1
    config.scan_defaults.min_wavelength_nm = 1530
    config.scan_defaults.max_wavelength_nm = 1565
    config.scan_defaults.speed_nm_s = 10
    window = MainWindow(
        config, settings=AppSettings(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    )
    qtbot.addWidget(window)
    window.show()
    device = CT400.__new__(CT400)
    device.handle = 42
    device.dll = SimpleNamespace(CT400_Close=lambda _handle: 0)
    device.cmd_laser = lambda **_kwargs: None
    window._on_ct400_initialized(device)
    window.control_panel.input_port.setCurrentIndex(window.control_panel.input_port.findData(LaserInput.LI_1))
    window._handle_ct400_connection_success("Connected", LaserInput.LI_1)
    window._ct400_connected_speed_nm_s = 10
    return window


def _dialog(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = CT400SupervisedCampaignDialog(window)
    qtbot.addWidget(dialog)
    dialog.export_directory.setText(str(tmp_path / "calibration"))
    dialog.confirm_safe.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    return window, dialog


def test_campaign_guard_remains_disabled_without_operator_preflight(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = CT400SupervisedCampaignDialog(window)
    qtbot.addWidget(dialog)
    called = []
    monkeypatch.setattr(window.control_panel, "run_calibration_scan", lambda *_a, **_kw: called.append(True))
    try:
        assert not dialog.start_button.isEnabled()
        dialog._start()
        assert dialog.state == "ready"
        assert not called
        dialog.confirm_safe.setChecked(True)
        window._ct400_connected_speed_nm_s = None
        dialog._start()
        assert dialog.state == "ready"
        assert not called
        assert not window.control_panel._calibration_blocked
        assert not window.histogram_control._calibration_blocked
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


def test_first_supervised_fake_callback_is_journaled_then_stop_releases_ownership(qtbot, monkeypatch, tmp_path):
    window, dialog = _dialog(qtbot, monkeypatch, tmp_path)
    calls = []

    def simulated_ui_scan(owner, *, run, laser_power_mw, laser_input):
        assert owner is dialog
        assert laser_input is LaserInput.LI_1
        assert laser_power_mw > 0
        assert window.control_panel._calibration_blocked
        assert window.histogram_control._calibration_blocked
        calls.append(run.run_id)

        def finish():
            window.control_panel.scan_timing_ready.emit(
                {
                    "scan_id": run.run_id,
                    "outcome": "success",
                    "stage_seconds": {"start_scan_seconds": 0.07},
                    "gui_start_to_completion_seconds": 2.5,
                    "returned_sample_count": run.predicted_sample_count,
                    "failed_stages": [],
                    "cleanup_failed": False,
                }
            )
            window.control_panel.operation_finished.emit()

        QTimer.singleShot(0, finish)

    monkeypatch.setattr(window.control_panel, "run_calibration_scan", simulated_ui_scan)
    try:
        dialog._start()
        qtbot.waitUntil(lambda: dialog.state == "paused", timeout=3000)
        assert len(calls) == 1
        assert dialog.next_index == 1
        assert dialog.campaign.records[0]["run_id"] == calls[0]
        assert dialog.campaign.records[0]["simulated"] is False
        assert dialog.campaign.records[0]["outcome"] == "success"
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        dialog._stop()
        assert dialog.state == "stopped"
        assert not window.control_panel._calibration_blocked
        assert not window.histogram_control._calibration_blocked
        assert len(dialog.campaign.records) == 1
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


def test_full_18_fake_callbacks_require_manual_connect_speed_checkpoint(qtbot, monkeypatch, tmp_path):
    window, dialog = _dialog(qtbot, monkeypatch, tmp_path)
    dialog.confirm_extra.setChecked(True)
    dialog.auto_next.setChecked(True)
    dialog._INTER_RUN_DELAY_MS = 1
    calls = []

    def simulated_ui_scan(_owner, *, run, **_kwargs):
        calls.append((run.case_id, run.speed_block_nm_s, run.run_id))
        QTimer.singleShot(
            0,
            lambda: (
                window.control_panel.scan_timing_ready.emit(
                    {
                        "scan_id": run.run_id,
                        "outcome": "success",
                        "stage_seconds": {},
                        "gui_start_to_completion_seconds": run.nominal_sweep_seconds + 0.1,
                        "returned_sample_count": run.predicted_sample_count,
                        "failed_stages": [],
                        "cleanup_failed": False,
                    }
                ),
                window.control_panel.operation_finished.emit(),
            ),
        )

    monkeypatch.setattr(window.control_panel, "run_calibration_scan", simulated_ui_scan)
    try:
        dialog._start()
        qtbot.waitUntil(lambda: dialog.state == "checkpoint", timeout=7000)
        assert dialog.next_index == 15
        assert len(calls) == 15
        assert all(speed == 10 for _case, speed, _id in calls)
        dialog._prepare_next_connect_speed()
        assert window.config.scan_defaults.speed_nm_s == 10
        window._handle_ct400_disconnection_success("Disconnected")
        window._set_ct400_operation_state(CT400OperationState.IDLE)
        dialog._prepare_next_connect_speed()
        assert window.config.scan_defaults.speed_nm_s == 5
        window._handle_ct400_connection_success("Reconnected", LaserInput.LI_1)
        window._ct400_connected_speed_nm_s = 5
        dialog.confirm_safe.setChecked(True)
        dialog._acknowledge_speed_checkpoint()
        qtbot.waitUntil(lambda: dialog.state == "completed", timeout=5000)
        assert dialog.next_index == 18
        assert len(calls) == 18
        assert [speed for _case, speed, _id in calls] == [10] * 15 + [5] * 3
        assert len({run_id for _case, _speed, run_id in calls}) == 18
        assert len(dialog.campaign.records) == 18
        assert len((tmp_path / "calibration" / f"{dialog.campaign.campaign_id}.jsonl").read_text().splitlines()) == 18
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


@pytest.mark.parametrize("outcome", ["warning", "error", "cancellation"])
def test_unexpected_fake_worker_outcome_halts_future_scans(qtbot, monkeypatch, tmp_path, outcome):
    window, dialog = _dialog(qtbot, monkeypatch, tmp_path)
    dialog.auto_next.setChecked(True)
    calls = []

    def simulated_ui_scan(_owner, *, run, **_kwargs):
        calls.append(run.run_id)
        QTimer.singleShot(
            0,
            lambda: (
                window.control_panel.scan_timing_ready.emit(
                    {"scan_id": run.run_id, "outcome": outcome, "failed_stages": [], "cleanup_failed": False}
                ),
                window.control_panel.operation_finished.emit(),
            ),
        )

    monkeypatch.setattr(window.control_panel, "run_calibration_scan", simulated_ui_scan)
    try:
        dialog._start()
        qtbot.waitUntil(lambda: dialog.state in ("paused", "failed", "stopped"), timeout=3000)
        assert len(calls) == 1
        assert len(dialog.campaign.records) == 1
        if outcome == "warning":
            assert dialog.state == "paused"
            dialog._stop()
        elif outcome == "error":
            assert dialog.state == "failed"
        else:
            assert dialog.state == "stopped"
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


def test_refuses_stale_fake_timing_and_never_starts_next_run(qtbot, monkeypatch, tmp_path):
    window, dialog = _dialog(qtbot, monkeypatch, tmp_path)
    dialog.auto_next.setChecked(True)

    def wrong_scan(_owner, *, run, **_kwargs):
        QTimer.singleShot(
            0,
            lambda: (
                window.control_panel.scan_timing_ready.emit({"scan_id": "unrelated", "outcome": "success"}),
                window.control_panel.operation_finished.emit(),
            ),
        )

    monkeypatch.setattr(window.control_panel, "run_calibration_scan", wrong_scan)
    try:
        dialog._start()
        qtbot.waitUntil(lambda: dialog.state == "stopped", timeout=3000)
        assert dialog.next_index == 1
        assert len(dialog.campaign.records) == 1
        assert dialog.campaign.records[0]["outcome"] == "failed"
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


def test_withdrawing_operator_approval_pauses_before_next_fake_scan(qtbot, monkeypatch, tmp_path):
    window, dialog = _dialog(qtbot, monkeypatch, tmp_path)
    dialog.auto_next.setChecked(True)
    calls = []

    def fake_scan(_owner, *, run, **_kwargs):
        calls.append(run.run_id)
        dialog.confirm_safe.setChecked(False)
        QTimer.singleShot(
            0,
            lambda: (
                window.control_panel.scan_timing_ready.emit(
                    {"scan_id": run.run_id, "outcome": "success", "failed_stages": [], "cleanup_failed": False}
                ),
                window.control_panel.operation_finished.emit(),
            ),
        )

    monkeypatch.setattr(window.control_panel, "run_calibration_scan", fake_scan)
    try:
        dialog._start()
        qtbot.waitUntil(lambda: dialog.state == "paused", timeout=3000)
        assert len(calls) == 1
        assert dialog.next_index == 1
        dialog._continue()
        assert len(calls) == 1
        dialog._stop()
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


def test_initial_connect_speed_preparation_never_calls_hardware(qtbot, monkeypatch, tmp_path):
    window, dialog = _dialog(qtbot, monkeypatch, tmp_path)
    try:
        window.config.scan_defaults.speed_nm_s = 5
        dialog._prepare_initial_connect_speed()
        assert window.config.scan_defaults.speed_nm_s == 5
        window._handle_ct400_disconnection_success("Disconnected")
        dialog._prepare_initial_connect_speed()
        assert window.config.scan_defaults.speed_nm_s == 10
        assert dialog.state == "ready"
        assert window._ct400_connected_laser_input is None
        assert not dialog._owns_panel
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()
