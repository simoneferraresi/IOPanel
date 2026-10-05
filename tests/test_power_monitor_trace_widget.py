from datetime import UTC, datetime, timedelta

import numpy as np

from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingSample,
    PowerMonitorRecordingStopReason,
)
from ui.plot_widgets import PowerMonitorTraceWidget


def _settings(detectors=(Detector.DE_1, Detector.DE_3)):
    return PowerMonitorAcquisitionSettings(1550.0, "1.0", "mW", 1.0, LaserInput.LI_1, detectors, 250)


def _recording(settings, elapsed, detector_data):
    now = datetime.now(UTC)
    return PowerMonitorRecording(
        settings=settings,
        elapsed_s=np.asarray(elapsed, dtype=float),
        pout_data=np.arange(len(elapsed), dtype=float),
        detector_data=np.asarray(detector_data, dtype=float).reshape((len(settings.detectors), len(elapsed))),
        detectors=settings.detectors,
        started_at_utc=now,
        completed_at_utc=now + timedelta(seconds=2),
        duration_s=2.0,
        backend="test.Backend",
        simulated=True,
        stop_reason=PowerMonitorRecordingStopReason.USER_STOPPED,
    )


def test_trace_plots_only_selected_detectors_with_actual_times_and_nan_gaps(qtbot):
    widget = PowerMonitorTraceWidget()
    qtbot.addWidget(widget)
    widget.start_recording(_settings())

    assert set(widget.curve_items) == {Detector.DE_1, Detector.DE_3}
    assert widget.plot_widget.getAxis("left").labelText == "Detector value (unit unverified)"
    assert widget.plot_widget.getAxis("bottom").labelText == "Elapsed time (s)"
    assert [curve.opts["name"] for curve in widget.curve_items.values()] == ["Det 1", "Det 3"]

    for elapsed, de1, de3 in ((0.25, 1.0, 3.0), (0.63, 2.0, np.nan), (1.42, 4.0, 6.0)):
        widget.append_sample(PowerMonitorRecordingSample(elapsed, 99.0, (Detector.DE_1, Detector.DE_3), (de1, de3)))

    np.testing.assert_array_equal(widget.elapsed_values, [0.25, 0.63, 1.42])
    np.testing.assert_array_equal(widget.curve_items[Detector.DE_1].getData()[0], [0.25, 0.63, 1.42])
    plotted_de3 = widget.curve_items[Detector.DE_3].getData()[1]
    assert np.isnan(plotted_de3[1])
    np.testing.assert_array_equal(widget.detector_values[Detector.DE_1], [1.0, 2.0, 4.0])
    widget.close()


def test_second_recording_resets_data_and_uses_its_frozen_selection(qtbot):
    widget = PowerMonitorTraceWidget()
    qtbot.addWidget(widget)
    widget.start_recording(_settings())
    widget.append_sample(PowerMonitorRecordingSample(0.25, 1.0, (Detector.DE_1, Detector.DE_3), (1.0, 3.0)))

    settings_b = _settings((Detector.DE_2,))
    widget.start_recording(settings_b)

    assert widget.elapsed_values == []
    assert widget.detector_values == {Detector.DE_2: []}
    assert set(widget.curve_items) == {Detector.DE_2}
    assert widget.elapsed_values == []
    widget.close()


def test_completed_zero_sample_and_zero_detector_recordings_canonicalize_and_discard(qtbot):
    widget = PowerMonitorTraceWidget()
    qtbot.addWidget(widget)
    settings = _settings()
    widget.start_recording(settings)
    widget.append_sample(PowerMonitorRecordingSample(0.25, 1.0, settings.detectors, (8.0, 9.0)))

    recording = _recording(settings, [0.25, 0.63], [[2.0, np.nan], [5.0, 6.0]])
    widget.set_completed_recording(recording)
    np.testing.assert_array_equal(widget.elapsed_values, recording.elapsed_s)
    np.testing.assert_array_equal(widget.detector_values[Detector.DE_1], recording.detector_data[0])
    assert not np.isnan(widget.detector_values[Detector.DE_3][0])
    assert np.isnan(widget.detector_values[Detector.DE_1][1])

    empty = _recording(settings, [], [[], []])
    widget.set_completed_recording(empty)
    assert widget.elapsed_values == []
    assert widget.detector_values == {Detector.DE_1: [], Detector.DE_3: []}
    assert widget.status_label.text() == "No samples captured"

    no_detectors = _settings(())
    widget.set_completed_recording(_recording(no_detectors, [], []))
    assert widget.curve_items == {}
    assert widget.status_label.text() == "No detector channels selected"

    widget.start_recording(settings)
    widget.append_sample(PowerMonitorRecordingSample(0.25, 1.0, settings.detectors, (8.0, 9.0)))
    widget.discard_recording()
    assert widget.isHidden()
    assert widget.elapsed_values == []
    assert widget.curve_items == {}
    widget.close()
