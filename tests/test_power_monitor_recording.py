from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingSample,
    PowerMonitorRecordingStopReason,
)


def _settings(detectors=(Detector.DE_1, Detector.DE_3)):
    return PowerMonitorAcquisitionSettings(1550.0, "1.0", "mW", 1.0, LaserInput.LI_1, detectors, 250)


def _recording(**overrides):
    started = datetime.now(UTC)
    values = {
        "settings": _settings(),
        "elapsed_s": np.array([0.25, 0.63]),
        "pout_data": np.array([1.0, 2.0]),
        "detector_data": np.array([[3.0, 4.0], [5.0, 6.0]]),
        "detectors": (Detector.DE_1, Detector.DE_3),
        "started_at_utc": started,
        "completed_at_utc": started + timedelta(seconds=1),
        "duration_s": 1.0,
        "backend": "test.Backend",
        "simulated": True,
        "stop_reason": PowerMonitorRecordingStopReason.USER_STOPPED,
    }
    values.update(overrides)
    return PowerMonitorRecording(**values)


def test_recording_copies_arrays_preserves_detector_order_and_is_read_only():
    elapsed = np.array([0.25, 0.63])
    pout = np.array([1.0, 2.0])
    detector_data = np.array([[3.0, 4.0], [5.0, 6.0]])
    recording = _recording(elapsed_s=elapsed, pout_data=pout, detector_data=detector_data)

    elapsed[0] = 99
    pout[0] = 99
    detector_data[0, 0] = 99

    assert recording.detectors == (Detector.DE_1, Detector.DE_3)
    assert recording.detector_data.shape == (2, 2)
    np.testing.assert_array_equal(recording.elapsed_s, [0.25, 0.63])
    np.testing.assert_array_equal(recording.pout_data, [1.0, 2.0])
    for array in (recording.elapsed_s, recording.pout_data, recording.detector_data):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array.flat[0] = 0


def test_empty_recording_and_empty_detector_selection_are_valid():
    settings = _settings(())
    recording = _recording(
        settings=settings,
        detectors=(),
        elapsed_s=np.array([]),
        pout_data=np.array([]),
        detector_data=np.empty((0, 0)),
        duration_s=0,
    )
    assert recording.detector_data.shape == (0, 0)


@pytest.mark.parametrize("detectors", [(Detector.DE_1, Detector.DE_1), (Detector.POUT,), (Detector.DE_5,), ("DE1",)])
def test_settings_reject_invalid_detector_rows(detectors):
    with pytest.raises((TypeError, ValueError)):
        _settings(detectors)


def test_settings_reject_nonpositive_poll_interval():
    with pytest.raises(ValueError, match="greater than zero"):
        PowerMonitorAcquisitionSettings(1550, "1", "mW", 1, LaserInput.LI_1, (), 0)


@pytest.mark.parametrize(
    "updates",
    [
        {"elapsed_s": np.array([[0.0]])},
        {"pout_data": np.array([[0.0]])},
        {"pout_data": np.array([0.0])},
        {"detector_data": np.array([1.0, 2.0])},
        {"detector_data": np.zeros((1, 2))},
        {"detectors": (Detector.DE_3, Detector.DE_1)},
        {"elapsed_s": np.array([0.2, 0.1])},
        {"elapsed_s": np.array([-0.1, 0.2])},
        {"elapsed_s": np.array([np.nan, 0.2])},
        {"duration_s": -1.0},
        {"duration_s": np.nan},
        {"duration_s": 0.2},
    ],
)
def test_recording_rejects_invalid_shape_and_time_contracts(updates):
    with pytest.raises(ValueError):
        _recording(**updates)


def test_recording_rejects_naive_or_reversed_utc_timestamps():
    started = datetime.now(UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        _recording(started_at_utc=started.replace(tzinfo=None))
    with pytest.raises(ValueError, match="precede"):
        _recording(started_at_utc=started, completed_at_utc=started - timedelta(seconds=1))


def test_recording_sample_preserves_irregular_time_detector_order_and_nonfinite_values():
    sample = PowerMonitorRecordingSample(
        elapsed_s=0.63,
        pout=float("inf"),
        detectors=(Detector.DE_3, Detector.DE_1),
        detector_values=(float("nan"), float("-inf")),
    )
    assert sample.elapsed_s == 0.63
    assert sample.detectors == (Detector.DE_3, Detector.DE_1)
    assert np.isnan(sample.detector_values[0])
    assert sample.detector_values[1] == float("-inf")
    assert sample.pout == float("inf")


def test_recording_sample_rejects_value_length_mismatch():
    with pytest.raises(ValueError, match="length must match"):
        PowerMonitorRecordingSample(0.25, 1.0, (Detector.DE_1, Detector.DE_2), (2.0,))


@pytest.mark.parametrize("detectors", [(Detector.POUT,), (Detector.DE_5,), (Detector.DE_1, Detector.DE_1), ("DE1",)])
def test_recording_sample_rejects_invalid_detector_rows(detectors):
    with pytest.raises((TypeError, ValueError)):
        PowerMonitorRecordingSample(0.25, 1.0, detectors, (1.0,) * len(detectors))


@pytest.mark.parametrize("elapsed_s", [-0.1, np.nan, np.inf, -np.inf])
def test_recording_sample_rejects_invalid_elapsed_time(elapsed_s):
    with pytest.raises(ValueError, match="elapsed_s"):
        PowerMonitorRecordingSample(elapsed_s, 1.0, (), ())
