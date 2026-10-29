"""Hardware-free PM5020 full-session export contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from hardware.pm5020_types import ChannelConfig, PowerChannel, PowerReading
from logic.pm5020_trace_export import (
    PM5020TraceRecording,
    build_pm5020_trace_export_v1,
    default_pm5020_trace_export_name,
    derive_pm5020_trace_export_targets,
)


def _recording():
    start = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    samples = (
        (PowerReading(PowerChannel.CH1, 1e-3, start + timedelta(milliseconds=10)), 0.01),
        (PowerReading(PowerChannel.CH2, 2e-9, start + timedelta(milliseconds=23)), 0.023),
        (PowerReading(PowerChannel.CH1, 0.0, start + timedelta(milliseconds=260)), 0.26),
    )
    configs = (
        (PowerChannel.CH1, ChannelConfig(wavelength_nm=1550.0, input_filter_enabled=True)),
        (PowerChannel.CH2, ChannelConfig(wavelength_nm=775.0)),
    )
    return PM5020TraceRecording(
        samples=samples,
        channel_configs=configs,
        started_at_utc=start,
        completed_at_utc=start + timedelta(seconds=1),
        backend="simulation",
        initial_poll_interval_ms=250,
        final_poll_interval_ms=500,
    )


def test_export_preserves_original_units_and_asynchronous_channel_times():
    recording = _recording()
    payload = build_pm5020_trace_export_v1(recording, comment="SHG test\r\nSecond line")
    data = payload.csv_data
    assert data.shape == (3, 4)
    np.testing.assert_allclose(data[:, 0], (0.01, 0.023, 0.26))
    np.testing.assert_allclose(
        data[:, 1],
        [sample.acquired_at_utc.timestamp() for sample, _elapsed in recording.samples],
    )
    np.testing.assert_allclose(data[[0, 2], 2], (1e-3, 0.0))
    assert np.isnan(data[1, 2]) and np.isnan(data[0, 3]) and np.isnan(data[2, 3])
    assert data[1, 3] == 2e-9
    assert "# SampleCount: 3" in payload.csv_header
    assert '# ChannelSampleCounts: {"CH1": 2, "CH2": 1}' in payload.csv_header
    assert '"wavelength_nm": 1550.0' in payload.csv_header
    assert '"input_filter_enabled": true' in payload.csv_header
    assert "SHG test\\nSecond line" in payload.csv_header
    assert "sequential per-channel" in payload.csv_header
    np.testing.assert_array_equal(payload.mat_data["sample_channel_id"], [1, 2, 1])
    assert payload.mat_data["initial_poll_interval_ms"] == 250
    assert payload.mat_data["final_poll_interval_ms"] == 500
    assert not data.flags.writeable


def test_snapshot_is_not_an_alias_of_mutable_live_sample_list():
    recording = _recording()
    live = list(recording.samples)
    frozen = PM5020TraceRecording(
        samples=tuple(live),
        channel_configs=recording.channel_configs,
        started_at_utc=recording.started_at_utc,
        completed_at_utc=recording.completed_at_utc,
        backend=recording.backend,
        initial_poll_interval_ms=250,
        final_poll_interval_ms=250,
    )
    live.clear()
    assert len(frozen.samples) == 3
    assert len(build_pm5020_trace_export_v1(frozen).csv_data) == 3


def test_export_uses_separate_directory_targets_and_utc_name(tmp_path):
    recording = _recording()
    assert default_pm5020_trace_export_name(recording) == "pm5020_traces_20261029T100000Z"
    targets = derive_pm5020_trace_export_targets(tmp_path / "trace.CSV", include_csv=True, include_mat=True)
    assert targets == {"CSV": tmp_path / "trace.csv", "MAT": tmp_path / "trace.mat"}


def test_export_rejects_empty_samples():
    recording = _recording()
    empty = PM5020TraceRecording(
        samples=(),
        channel_configs=recording.channel_configs,
        started_at_utc=recording.started_at_utc,
        completed_at_utc=recording.completed_at_utc,
        backend="physical",
        initial_poll_interval_ms=1000,
        final_poll_interval_ms=1000,
    )
    with pytest.raises(ValueError, match="no PM5020 samples"):
        build_pm5020_trace_export_v1(empty)


def test_model_rejects_readings_from_unconfigured_channels_and_nonmonotonic_time():
    source = _recording()
    with pytest.raises(ValueError, match="not part of this session"):
        PM5020TraceRecording(
            samples=source.samples,
            channel_configs=source.channel_configs[:1],
            started_at_utc=source.started_at_utc,
            completed_at_utc=source.completed_at_utc,
            backend="simulation",
            initial_poll_interval_ms=250,
            final_poll_interval_ms=250,
        )
    with pytest.raises(ValueError, match="monotonic"):
        PM5020TraceRecording(
            samples=tuple(reversed(source.samples)),
            channel_configs=source.channel_configs,
            started_at_utc=source.started_at_utc,
            completed_at_utc=source.completed_at_utc,
            backend="simulation",
            initial_poll_interval_ms=250,
            final_poll_interval_ms=250,
        )
