import numpy as np

from config_model import AppConfig
from hardware.ct400_types import Detector, PowerData
from hardware.dummy_ct400 import DummyCT400
from logic.power_monitor_recording import PowerMonitorRecordingStopReason
from ui.control_panel import HistogramControlPanel


def _panel(qtbot):
    device = DummyCT400(scan_duration=0)
    panel = HistogramControlPanel(device, AppConfig())
    qtbot.addWidget(panel)
    panel.wavelength_input.setText("1550")
    panel.laser_power.setText("1.0")
    panel.detector_cbs[1].setChecked(False)
    panel.detector_cbs[2].setChecked(True)
    panel.detector_cbs[3].setChecked(False)
    return panel, device


def test_record_button_initial_state(qtbot):
    panel, _ = _panel(qtbot)
    assert not panel._recording_active
    assert not panel.record_btn.isEnabled()
    assert panel.record_btn.text() == "Record"
    assert panel.recording_elapsed_label.isHidden()
    assert panel.last_recording is None
    panel.cleanup_worker_thread()


def test_recording_captures_irregular_samples_and_manual_stop_keeps_monitoring(qtbot, monkeypatch):
    panel, device = _panel(qtbot)
    times = iter([100.0, 100.25, 100.63, 101.0])
    monkeypatch.setattr("ui.control_panel.time.monotonic", lambda: next(times))
    emitted = []
    displayed = []
    panel.recording_completed.connect(emitted.append)
    panel.power_data_ready.connect(displayed.append)
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    reads = []
    device.get_all_powers = lambda: reads.append(True) or PowerData(1.0, {})
    panel._start_recording()

    assert panel._recording_active
    assert panel.record_btn.text() == "Stop Recording"
    assert not panel.recording_elapsed_label.isHidden()
    assert panel._recording_settings.detectors == (Detector.DE_1, Detector.DE_3)
    panel._handle_worker_data_ready(PowerData(10.0, {Detector.DE_1: 11.0, Detector.DE_3: 13.0}))
    panel._handle_worker_data_ready(PowerData(20.0, {Detector.DE_1: 21.0}))
    assert displayed[-1] == {
        "pout": 20.0,
        "detectors": {"Det 1": 21.0, "Det 2": 0.0, "Det 3": 0.0, "Det 4": 0.0},
    }
    panel._finalize_recording(PowerMonitorRecordingStopReason.USER_STOPPED)

    recording = panel.last_recording
    assert recording is emitted[0]
    assert recording.stop_reason is PowerMonitorRecordingStopReason.USER_STOPPED
    np.testing.assert_allclose(recording.elapsed_s, [0.25, 0.63])
    np.testing.assert_allclose(recording.pout_data, [10.0, 20.0])
    assert recording.detector_data[1, 1] != recording.detector_data[1, 1]
    assert panel.monitoring
    assert panel.timer.isActive()
    assert panel.record_btn.isEnabled()
    assert panel.record_btn.text() == "Record"
    assert panel.recording_elapsed_label.isHidden()
    assert reads == []
    panel.timer.stop()
    panel.monitoring = False
    panel.cleanup_worker_thread()


def test_monitor_stop_auto_finalizes_and_keeps_zero_sample_recording(qtbot, monkeypatch):
    panel, _ = _panel(qtbot)
    monkeypatch.setattr("ui.control_panel.time.monotonic", lambda: 20.0)
    emitted = []
    panel.recording_completed.connect(emitted.append)
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    panel._start_recording()
    panel._stop_monitoring()
    assert len(emitted) == 1
    assert emitted[0].stop_reason is PowerMonitorRecordingStopReason.MONITORING_STOPPED
    assert emitted[0].elapsed_s.shape == (0,)
    assert emitted[0].pout_data.shape == (0,)
    assert emitted[0].detector_data.shape == (2, 0)
    assert not panel._recording_active
    panel.cleanup_worker_thread()


def test_monitor_error_auto_finalizes_captured_samples(qtbot, monkeypatch):
    panel, _ = _panel(qtbot)
    times = iter([10.0, 10.5, 11.0])
    monkeypatch.setattr("ui.control_panel.time.monotonic", lambda: next(times))
    emitted = []
    panel.recording_completed.connect(emitted.append)
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    panel._start_recording()
    panel._handle_worker_data_ready(PowerData(1.0, {Detector.DE_1: 2.0, Detector.DE_3: 3.0}))
    panel._stop_monitoring(instrument_error_or_disconnect=True)
    assert len(emitted) == 1
    assert emitted[0].stop_reason is PowerMonitorRecordingStopReason.MONITORING_ERROR
    np.testing.assert_array_equal(emitted[0].pout_data, [1.0])
    panel.cleanup_worker_thread()
