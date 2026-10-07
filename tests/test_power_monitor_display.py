from datetime import UTC, datetime, timedelta

import numpy as np

from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_display import DEFAULT_DISPLAY_POINT_LIMIT, PowerMonitorDisplayHistory
from logic.power_monitor_export import build_power_monitor_export_v1
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingStopReason,
)


def _display(raw, channels, point_limit=DEFAULT_DISPLAY_POINT_LIMIT):
    display = PowerMonitorDisplayHistory(len(channels), point_limit)
    raw_channels = [[] for _ in channels]
    display.bind_raw_values(raw_channels)
    for row in range(raw):
        for channel, values in zip(raw_channels, channels, strict=True):
            channel.append(values[row])
        display.append(tuple(channel[row] for channel in channels))
    return display


def test_short_history_is_full_resolution_and_zero_detector_history_is_valid():
    channels = [[float(i) for i in range(50)], [-float(i) for i in range(50)]]
    history = _display(50, channels)
    np.testing.assert_array_equal(history.indices(0), np.arange(50))
    np.testing.assert_array_equal(history.indices(1), np.arange(50))
    empty = PowerMonitorDisplayHistory(0)
    for _ in range(100):
        empty.append(())


def test_long_history_is_bounded_keeps_latest_and_preserves_narrow_extrema_per_channel():
    count = 10_000
    channels = [np.sin(np.arange(count) / (70 + channel * 10)).tolist() for channel in range(4)]
    channels[0][4321] = 80.0
    channels[0][7321] = -80.0
    channels[1][2468] = 90.0
    channels[1][8642] = -90.0
    channels[2][1357] = 70.0
    channels[2][9753] = -70.0
    channels[3][3210] = 60.0
    channels[3][9123] = -60.0
    history = _display(count, channels)
    for channel in range(2):
        indices = history.indices(channel)
        assert len(indices) <= DEFAULT_DISPLAY_POINT_LIMIT
        assert indices[0] == 0
        assert indices[-1] == count - 1
        assert set(range(count - 256, count)).issubset(set(indices.tolist()))
        assert set(range(0, count, history._bucket_width)).issubset(set(indices.tolist()))
        assert np.nanmax(np.asarray(channels[channel])[indices]) == np.nanmax(channels[channel])
        assert np.nanmin(np.asarray(channels[channel])[indices]) == np.nanmin(channels[channel])
    assert 4321 in history.indices(0)
    assert 7321 in history.indices(0)
    assert 2468 in history.indices(1)
    assert 8642 in history.indices(1)
    assert 1357 in history.indices(2)
    assert 9753 in history.indices(2)
    assert 3210 in history.indices(3)
    assert 9123 in history.indices(3)


def test_display_downsampling_does_not_change_recording_or_export_samples():
    count = 10_000
    elapsed = np.arange(count, dtype=float) * 0.25
    pout = np.arange(count, dtype=float) / 10
    detector_data = np.vstack((np.sin(elapsed), np.cos(elapsed)))
    settings = PowerMonitorAcquisitionSettings(1550, "1", "mW", 1, LaserInput.LI_1, (Detector.DE_1, Detector.DE_3), 250)
    recording = PowerMonitorRecording(
        settings=settings,
        elapsed_s=elapsed,
        pout_data=pout,
        detector_data=detector_data,
        detectors=settings.detectors,
        started_at_utc=datetime.now(UTC),
        completed_at_utc=datetime.now(UTC) + timedelta(seconds=1),
        duration_s=float(elapsed[-1]),
        backend="test",
        simulated=True,
        stop_reason=PowerMonitorRecordingStopReason.USER_STOPPED,
    )
    channels = [recording.detector_data[row].tolist() for row in range(2)]
    history = _display(count, channels)
    payload = build_power_monitor_export_v1(recording)
    assert len(history.indices(0)) <= DEFAULT_DISPLAY_POINT_LIMIT
    assert len(recording.elapsed_s) == len(payload.csv_data) == count
    np.testing.assert_array_equal(payload.csv_data[:, 0], elapsed)
    np.testing.assert_array_equal(payload.csv_data[:, 1], pout)
    np.testing.assert_array_equal(payload.csv_data[:, 2], detector_data[0])
    np.testing.assert_array_equal(payload.csv_data[:, 4], detector_data[1])


def test_rebuilds_are_infrequent_and_clear_resets_display_indices():
    channels = [[], []]
    history = PowerMonitorDisplayHistory(2, point_limit=100)
    history.bind_raw_values(channels)
    previous_width = history._bucket_width
    rebuilds = 0
    for index in range(50_000):
        channels[0].append(float(index))
        channels[1].append(float(-index))
        history.append((float(index), float(-index)))
        if history._bucket_width != previous_width:
            rebuilds += 1
            previous_width = history._bucket_width
    assert rebuilds < 20
    assert max(len(history.indices(0)), len(history.indices(1))) <= 100
    history.bind_raw_values([[], []])
    history.clear()
    assert history.indices(0).size == 0
