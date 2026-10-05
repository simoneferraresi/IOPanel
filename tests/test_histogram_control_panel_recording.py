from typing import cast

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


def _shutdown_panel(qtbot, panel):
    if panel._monitor_stop_pending:
        qtbot.waitUntil(lambda: not panel._monitor_stop_pending, timeout=3000)
    panel.cleanup_worker_thread()
    # closeEvent normally initiates this cleanup; we've already done it here so
    # pytest-qt can close the widget without touching the deleted QThread wrapper.
    panel.cleanup_worker_thread = lambda: None


def test_record_button_initial_state(qtbot):
    panel, _ = _panel(qtbot)
    assert not panel._recording_active
    assert not panel.record_btn.isEnabled()
    assert panel.record_btn.text() == "Record"
    assert panel.recording_elapsed_label.isHidden()
    assert panel.last_recording is None
    _shutdown_panel(qtbot, panel)


def test_recording_captures_irregular_samples_and_manual_stop_keeps_monitoring(qtbot, monkeypatch):
    panel, device = _panel(qtbot)
    times = iter([100.0, 100.25, 100.63, 101.0])
    monkeypatch.setattr("ui.control_panel.time.monotonic", lambda: next(times))
    emitted = []
    displayed = []
    started = []
    samples = []
    panel.recording_completed.connect(emitted.append)
    panel.recording_started.connect(started.append)
    panel.recording_sample_captured.connect(samples.append)
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
    assert started == [panel._recording_settings]
    panel._handle_worker_data_ready(PowerData(10.0, {Detector.DE_1: 11.0, Detector.DE_3: 13.0}))
    panel._handle_worker_data_ready(PowerData(20.0, {Detector.DE_1: 21.0}))
    assert len(samples) == 2
    assert samples[0].elapsed_s == 0.25
    assert samples[0].detectors == (Detector.DE_1, Detector.DE_3)
    assert samples[0].detector_values == (11.0, 13.0)
    assert np.isnan(samples[1].detector_values[1])
    assert displayed[-1] == {
        "pout": 20.0,
        "detectors": {"Det 1": 21.0, "Det 2": 0.0, "Det 3": 0.0, "Det 4": 0.0},
    }
    panel._finalize_recording(PowerMonitorRecordingStopReason.USER_STOPPED)

    recording = panel.last_recording
    assert recording is emitted[0]
    assert recording.stop_reason is PowerMonitorRecordingStopReason.USER_STOPPED
    assert recording.detector_unit == "dBm"
    assert recording.pout_unit == "dBm"
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
    _shutdown_panel(qtbot, panel)


def test_monitor_stop_auto_finalizes_and_keeps_zero_sample_recording(qtbot, monkeypatch):
    panel, _ = _panel(qtbot)
    monkeypatch.setattr("ui.control_panel.time.monotonic", lambda: 20.0)
    emitted = []
    panel.recording_completed.connect(emitted.append)
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    panel._start_recording()
    samples = []
    panel.recording_sample_captured.connect(samples.append)
    panel._stop_monitoring()
    assert samples == []
    assert len(emitted) == 1
    assert emitted[0].stop_reason is PowerMonitorRecordingStopReason.MONITORING_STOPPED
    assert emitted[0].elapsed_s.shape == (0,)
    assert emitted[0].pout_data.shape == (0,)
    assert emitted[0].detector_data.shape == (2, 0)
    assert not panel._recording_active
    _shutdown_panel(qtbot, panel)


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
    _shutdown_panel(qtbot, panel)


def test_malformed_recording_sample_does_not_break_live_display_or_partially_append(qtbot, monkeypatch):
    panel, _ = _panel(qtbot)
    times = iter([30.0, 30.25, 30.5, 30.8])
    monkeypatch.setattr("ui.control_panel.time.monotonic", lambda: next(times))
    displayed = []
    samples = []
    panel.power_data_ready.connect(displayed.append)
    panel.recording_sample_captured.connect(samples.append)
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    panel._start_recording()

    malformed = PowerData(
        3.0,
        cast(dict[Detector, float], {Detector.DE_1: "not numeric", Detector.DE_3: 9.0}),
    )
    panel._handle_worker_data_ready(malformed)

    assert displayed[-1]["pout"] == 3.0
    assert displayed[-1]["detectors"]["Det 1"] == "not numeric"
    assert panel.monitoring
    assert panel._recording_active
    assert panel._recording_elapsed == []
    assert panel._recording_pout == []
    assert panel._recording_detector_values == [[], []]
    assert samples == []

    panel._handle_worker_data_ready(PowerData(4.0, {Detector.DE_1: 5.0, Detector.DE_3: 6.0}))
    assert len(panel._recording_elapsed) == 1
    assert panel._recording_pout == [4.0]
    assert panel._recording_detector_values == [[5.0], [6.0]]
    assert len(samples) == 1
    panel._toggle_recording()
    panel.timer.stop()
    panel.monitoring = False
    _shutdown_panel(qtbot, panel)


def test_monitor_stop_continues_when_recording_finalization_fails(qtbot, monkeypatch):
    panel, _ = _panel(qtbot)

    def fail_recording_construction(**_kwargs):
        raise RuntimeError("controlled recording construction failure")

    monkeypatch.setattr("ui.control_panel.PowerMonitorRecording", fail_recording_construction)
    emitted = []
    discarded = []
    operation_finished = []
    panel.recording_completed.connect(emitted.append)
    panel.recording_discarded.connect(lambda: discarded.append(True))
    panel.operation_finished.connect(lambda: operation_finished.append(True))
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    panel._start_recording()

    panel._stop_monitoring()

    assert not panel.monitoring
    assert not panel.timer.isActive()
    assert not panel._recording_active
    assert panel._recording_settings is None
    assert emitted == []
    assert discarded == [True]
    qtbot.waitUntil(lambda: not panel._monitor_stop_pending, timeout=3000)
    assert operation_finished == [True]
    assert panel.record_btn.text() == "Record"
    assert not panel.record_btn.isEnabled()
    _shutdown_panel(qtbot, panel)


def test_manual_recording_stop_clears_state_when_finalization_fails(qtbot, monkeypatch):
    panel, _ = _panel(qtbot)

    def fail_recording_construction(**_kwargs):
        raise RuntimeError("controlled recording construction failure")

    monkeypatch.setattr("ui.control_panel.PowerMonitorRecording", fail_recording_construction)
    emitted = []
    panel.recording_completed.connect(emitted.append)
    panel.monitoring = True
    panel.timer.start()
    panel._update_recording_button()
    panel._start_recording()

    panel._toggle_recording()

    assert panel.monitoring
    assert panel.timer.isActive()
    assert not panel._recording_active
    assert panel._recording_settings is None
    assert emitted == []
    assert panel.record_btn.isEnabled()
    assert panel.record_btn.text() == "Record"
    panel.timer.stop()
    panel.monitoring = False
    _shutdown_panel(qtbot, panel)
