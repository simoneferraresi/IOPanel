"""Driver-free supervised campaign integration tests (no CT400 DLL calls)."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QWidget, QMessageBox

from config_model import AppConfig
from hardware.ct400 import CT400
from hardware.ct400_types import Detector, LaserInput
from ui.ct400_calibration_run_dialog import CT400CalibrationRunDialog


class FakeScanPanel(QWidget):
    scan_timing_ready = Signal(object)
    scan_data_ready = Signal(object)
    operation_finished = Signal()

    def __init__(self, ct400):
        super().__init__()
        self.ct400 = ct400
        self.ct400_operation_state = "IDLE"
        self.connected_laser_input = LaserInput.LI_1
        self.is_instrument_connected = True
        self.initial_wl = QLineEdit("1530")
        self.final_wl = QLineEdit("1565")
        self.resolution = QLineEdit("1")
        self.motor_speed = QLineEdit("10")
        self.laser_power = QLineEdit("1")
        self.power_unit = QComboBox()
        self.power_unit.addItems(["mW", "dBm"])
        self.input_port = QComboBox()
        for laser_input in LaserInput:
            self.input_port.addItem(str(laser_input.value), laser_input)
        self.input_port.setCurrentIndex(self.input_port.findData(LaserInput.LI_1))
        self.scan_detector_cbs = {d: QCheckBox() for d in (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4)}
        self.scan_detector_cbs[Detector.DE_1].setChecked(True)
        self.scanning = False
        self.scan_thread = None
        self.scan_worker = None
        self.owner = None
        self.next_id = None
        self.started = []
        self.stops = 0
        self.auto_finish = True
        self.next_outcome = "success"
        self.next_simulated = False

    def is_busy(self):
        return self.scanning

    def _get_laser_power_mw(self):
        return float(self.laser_power.text())

    def reserve_calibration_campaign(self, campaign_id):
        if self.owner is not None or self.scanning:
            raise RuntimeError("already reserved")
        self.owner = campaign_id

    def release_calibration_campaign(self, campaign_id):
        if self.owner != campaign_id or self.scanning:
            raise RuntimeError("wrong owner")
        self.owner = None

    def clear_next_scan_id(self):
        self.next_id = None

    def set_next_scan_id(self, scan_id):
        if self.next_id is not None or self.scanning:
            raise RuntimeError("already queued")
        self.next_id = scan_id

    def _start_scan(self):
        assert self.owner is not None
        assert not self.scanning
        assert self.next_id is not None
        self.scanning = True
        self.scan_worker = SimpleNamespace(scan_id=self.next_id)
        self.started.append((self.next_id, float(self.initial_wl.text()), float(self.final_wl.text()),
                             int(self.resolution.text()), self.motor_speed.text(),
                             tuple(detector.name for detector, cb in self.scan_detector_cbs.items() if cb.isChecked())))
        self.next_id = None
        if self.auto_finish:
            QTimer.singleShot(0, self.finish)

    def finish(self):
        assert self.scanning
        run_id = self.scan_worker.scan_id
        outcome = self.next_outcome
        if outcome in ("success", "warning"):
            self.scan_data_ready.emit(SimpleNamespace(scan_id=run_id))
        self.scan_timing_ready.emit({
            "scan_id": run_id, "outcome": outcome,
            "simulated": self.next_simulated,
            "stage_seconds": {"scan_wait_end_seconds": 0.01},
            "worker_duration_seconds": 0.02, "returned_sample_count": 5001,
            "configured_speed_nm_s_at_connect": float(self.motor_speed.text()),
            "scan_panel_speed_differs_from_configured": False,
            "failed_stages": [], "cleanup_failed": False,
        })
        self.scanning = False
        self.scan_worker = None
        self.operation_finished.emit()

    def _stop_scan(self, cancelled=False):
        assert cancelled
        self.stops += 1
        self.next_outcome = "cancellation"


class FakeWindow(QWidget):
    def __init__(self):
        super().__init__()
        # No CT400 constructor or vendor DLL initialization is called.
        self.ct400_device = CT400.__new__(CT400)
        self.control_panel = FakeScanPanel(self.ct400_device)
        self.histogram_control = SimpleNamespace(is_busy=lambda: False)
        self.config = AppConfig()
        self.config.scan_defaults.speed_nm_s = 10
        self._ct400_operation_state = SimpleNamespace(name="IDLE")
        self._ct400_connection_configured = True
        self._ct400_scan_state_uncertain = False
        self._ct400_recovery_disconnect_completed = False
        self._ct400_connected_laser_input = LaserInput.LI_1
        self._ct400_cleanup_laser_input = LaserInput.LI_1
        self.is_ct400_connected_state = True
        self._ct400_connect_speed_nm_s = 10

    def _ct400_device_has_unresolved_laser_state(self):
        return self._ct400_connection_configured

    def disconnect(self):
        self._ct400_connected_laser_input = None
        self._ct400_cleanup_laser_input = None
        self._ct400_connection_configured = False
        self._ct400_connect_speed_nm_s = None
        self.is_ct400_connected_state = False
        self.control_panel.connected_laser_input = None

    def connect_5(self):
        self._ct400_connected_laser_input = LaserInput.LI_1
        self._ct400_cleanup_laser_input = LaserInput.LI_1
        self._ct400_connection_configured = True
        self._ct400_connect_speed_nm_s = 5
        self.is_ct400_connected_state = True
        self.control_panel.connected_laser_input = LaserInput.LI_1


def prepared_dialog(qtbot, monkeypatch, tmp_path, *, four=True):
    window = FakeWindow()
    dialog = CT400CalibrationRunDialog(window)
    qtbot.addWidget(window)
    qtbot.addWidget(dialog)
    dialog._INTER_RUN_DELAY_MS = 0
    dialog.path.setText(str(tmp_path))
    dialog.operator.setText("offline CI case")
    dialog.physical_min.setValue(1500)
    dialog.physical_max.setValue(1600)
    for check in (dialog.confirm_range, dialog.confirm_power, dialog.confirm_risk):
        check.setChecked(True)
    dialog.confirm_four.setChecked(four)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    return window, dialog


@pytest.mark.parametrize(("four", "physical_starts"), [(True, 18), (False, 15)])
def test_complete_18_case_campaign_with_explicit_speed_reconnect(qtbot, monkeypatch, tmp_path, four, physical_starts):
    window, dialog = prepared_dialog(qtbot, monkeypatch, tmp_path, four=four)
    dialog.start_campaign()
    qtbot.waitUntil(lambda: dialog._state == "checkpoint", timeout=4000)
    assert len(window.control_panel.started) == (15 if four else 12)
    assert dialog.index == 15
    assert window.config.scan_defaults.speed_nm_s == 10
    assert dialog._approved_speed == 10
    window.disconnect()
    dialog.prepare_next_speed()
    assert window.config.scan_defaults.speed_nm_s == 5
    assert len(window.control_panel.started) == (15 if four else 12)
    window.connect_5()
    dialog.confirm_speed_checkpoint()
    qtbot.waitUntil(lambda: dialog._state == "completed", timeout=4000)
    assert len(window.control_panel.started) == physical_starts
    assert dialog.index == 18
    assert dialog.campaign is not None
    assert len(dialog.campaign.records) == 18
    assert window.control_panel.owner is None
    assert all(r["simulated"] is False for r in dialog.campaign.records)
    if not four:
        assert [r["case_id"] for r in dialog.campaign.records if r["outcome"] == "skipped"] == ["F"] * 3
    assert [r["speed_block_nm_s"] for r in dialog.campaign.records] == [10] * 15 + [5] * 3
    assert len({r["run_id"] for r in dialog.campaign.records}) == 18


def test_preflight_blocks_wrong_connected_speed_without_starting_scan(qtbot, monkeypatch, tmp_path):
    window, dialog = prepared_dialog(qtbot, monkeypatch, tmp_path)
    window._ct400_connect_speed_nm_s = 5
    dialog.start_campaign()
    assert dialog._state == "idle"
    assert window.control_panel.owner is None
    assert window.control_panel.started == []


def test_stop_during_active_scan_uses_only_existing_panel_stop_and_no_next_scan(qtbot, monkeypatch, tmp_path):
    window, dialog = prepared_dialog(qtbot, monkeypatch, tmp_path)
    window.control_panel.auto_finish = False
    dialog.start_campaign()
    qtbot.waitUntil(lambda: dialog._state == "scanning", timeout=1000)
    assert len(window.control_panel.started) == 1
    dialog.stop_campaign()
    assert dialog._state == "stopping"
    assert window.control_panel.stops == 1
    window.control_panel.finish()
    assert dialog._state == "stopped"
    assert len(window.control_panel.started) == 1
    assert dialog.campaign.records[0]["outcome"] == "cancellation"
    assert window.control_panel.owner is None


def test_failed_native_result_halts_without_scheduling_followup(qtbot, monkeypatch, tmp_path):
    window, dialog = prepared_dialog(qtbot, monkeypatch, tmp_path)
    window.control_panel.next_outcome = "failed"
    dialog.start_campaign()
    qtbot.waitUntil(lambda: dialog._state == "failed", timeout=1000)
    assert len(window.control_panel.started) == 1
    assert dialog.campaign.records[0]["outcome"] == "failed"
    assert window.control_panel.owner is None


def test_simulated_backend_result_fails_closed(qtbot, monkeypatch, tmp_path):
    window, dialog = prepared_dialog(qtbot, monkeypatch, tmp_path)
    window.control_panel.next_simulated = True
    dialog.start_campaign()
    qtbot.waitUntil(lambda: dialog._state == "failed", timeout=1000)
    assert len(window.control_panel.started) == 1
    assert dialog.campaign.records[0]["outcome"] == "failed"
    assert window.control_panel.owner is None


def test_campaign_close_is_deferred_until_scan_cleanup(qtbot, monkeypatch, tmp_path):
    window, dialog = prepared_dialog(qtbot, monkeypatch, tmp_path)
    window.control_panel.auto_finish = False
    dialog.show()
    dialog.start_campaign()
    qtbot.waitUntil(lambda: dialog._state == "scanning", timeout=1000)
    dialog.close()
    assert dialog.isVisible()
    assert window.control_panel.stops == 1
    window.control_panel.finish()
    assert dialog._state == "stopped"
    dialog.close()
    assert not dialog.isVisible()
