"""Lossless, hardware-independent PM5020 monitoring trace snapshots and CSV/MAT exports.

Polling the PM5020 channels is sequential. Each row represents one actual
channel reading at its own acquisition time, not a fabricated simultaneous
pair. A NaN in the other channel's column means "not sampled at this time".
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from hardware.pm5020_types import ChannelConfig, PowerChannel, PowerReading
from logic.export_file_io import derive_export_targets
from logic.power_monitor_export import PowerMonitorExportPayload

PM5020_TRACE_EXPORT_SCHEMA_VERSION = 1
PM5020_TRACE_CSV_COLUMNS = (
    "Elapsed_time_[s]",
    "Acquired_at_UNIX_[s]",
    "CH1_Power_[W]",
    "CH2_Power_[W]",
)


@dataclass(frozen=True, slots=True)
class PM5020TraceRecording:
    """Immutable full-session snapshot, independent of bounded plot history."""

    samples: tuple[tuple[PowerReading, float], ...]
    channel_configs: tuple[tuple[PowerChannel, ChannelConfig], ...]
    started_at_utc: datetime
    completed_at_utc: datetime
    backend: str
    initial_poll_interval_ms: int
    final_poll_interval_ms: int

    def __post_init__(self) -> None:
        samples = tuple(self.samples)
        configurations = tuple(self.channel_configs)
        if not configurations or len({channel for channel, _ in configurations}) != len(configurations):
            raise ValueError("Recording requires unique configured channels")
        if any(
            not isinstance(channel, PowerChannel) or not isinstance(config, ChannelConfig)
            for channel, config in configurations
        ):
            raise ValueError("Invalid PM5020 channel configuration")
        if self.started_at_utc.utcoffset() is None or self.completed_at_utc.utcoffset() is None:
            raise ValueError("Recording UTC times must be timezone-aware")
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("Recording completed before it started")
        if self.initial_poll_interval_ms <= 0 or self.final_poll_interval_ms <= 0:
            raise ValueError("Polling intervals must be positive")
        if self.backend not in {"simulation", "physical"}:
            raise ValueError("Unknown PM5020 backend mode")
        selected = {channel for channel, _ in configurations}
        previous_elapsed = -1.0
        for reading, elapsed in samples:
            if not isinstance(reading, PowerReading) or reading.channel not in selected:
                raise ValueError("Reading channel is not part of this session")
            if not math.isfinite(elapsed) or elapsed < 0 or elapsed < previous_elapsed:
                raise ValueError("Elapsed times must be finite and monotonic")
            previous_elapsed = elapsed
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "channel_configs", configurations)


def default_pm5020_trace_export_name(recording: PM5020TraceRecording) -> str:
    return recording.started_at_utc.astimezone(UTC).strftime("pm5020_traces_%Y%m%dT%H%M%SZ")


def derive_pm5020_trace_export_targets(
    selected_path: str | Path, *, include_csv: bool, include_mat: bool
) -> dict[str, Path]:
    return derive_export_targets(
        selected_path,
        formats={"CSV": include_csv, "MAT": include_mat},
        recognized_suffixes=frozenset({".csv", ".mat"}),
    )


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_pm5020_trace_export_v1(recording: PM5020TraceRecording, *, comment: str = "") -> PowerMonitorExportPayload:
    """Create a numeric sparse-wide CSV and a MATLAB representation.

    Original power readings stay in W; display-unit changes never modify them.
    UNIX seconds are written as float64 at sub-microsecond resolution near the
    present epoch. The original Python datetime precision is microseconds.
    """
    if not recording.samples:
        raise ValueError("This recording has no PM5020 samples to export.")
    count = len(recording.samples)
    csv_data = np.full((count, len(PM5020_TRACE_CSV_COLUMNS)), np.nan, dtype=np.float64)
    channel_ids = np.empty(count, dtype=np.int8)
    for index, (reading, elapsed) in enumerate(recording.samples):
        csv_data[index, 0] = elapsed
        csv_data[index, 1] = reading.acquired_at_utc.timestamp()
        csv_data[index, reading.channel.value + 1] = reading.power_w
        channel_ids[index] = reading.channel.value
    csv_data.setflags(write=False)
    channel_ids.setflags(write=False)

    configurations = {
        f"CH{channel.value}": {
            "wavelength_nm": config.wavelength_nm,
            "auto_range": config.auto_range,
            "power_range_w": config.power_range_w,
            "frequency_mode": config.frequency_mode,
            "input_filter_enabled": config.input_filter_enabled,
        }
        for channel, config in recording.channel_configs
    }
    normalized_comment = str(comment).replace("\r\n", "\n").replace("\r", "\n")
    samples_per_channel = {
        f"CH{channel.value}": int(np.count_nonzero(channel_ids == channel.value))
        for channel, _ in recording.channel_configs
    }
    started = _utc(recording.started_at_utc)
    completed = _utc(recording.completed_at_utc)
    configs_json = json.dumps(configurations, ensure_ascii=False, sort_keys=True)
    counts_json = json.dumps(samples_per_channel, sort_keys=True)
    metadata = [
        "# MeasurementType: PM5020ChannelTraces",
        f"# SchemaVersion: {PM5020_TRACE_EXPORT_SCHEMA_VERSION}",
        f"# StartedAtUTC: {started}",
        f"# CompletedAtUTC: {completed}",
        f"# SampleCount: {count}",
        f"# ChannelSampleCounts: {counts_json}",
        f"# ActiveChannels: {','.join(configurations)}",
        f"# Backend: {recording.backend}",
        f"# Simulated: {recording.backend == 'simulation'}",
        f"# InitialPollInterval_ms: {recording.initial_poll_interval_ms}",
        f"# FinalPollInterval_ms: {recording.final_poll_interval_ms}",
        "# Polling: sequential per-channel; each row is one acquisition; NaN means not sampled",
        "# PowerUnit: W (original instrument readings, not plot display units)",
        "# TimeBases: elapsed = worker monotonic clock; acquired = UTC UNIX seconds",
        f"# ChannelConfiguration: {configs_json}",
        f"# Comment: {json.dumps(normalized_comment, ensure_ascii=False)}",
        "# " + ", ".join(PM5020_TRACE_CSV_COLUMNS),
    ]
    mat_data: dict[str, object] = {
        "measurement_type": "PM5020ChannelTraces",
        "schema_version": PM5020_TRACE_EXPORT_SCHEMA_VERSION,
        "elapsed_s": csv_data[:, 0].copy(),
        "acquired_at_unix_s": csv_data[:, 1].copy(),
        "sample_channel_id": channel_ids.copy(),
        "power_ch1_w": csv_data[:, 2].copy(),
        "power_ch2_w": csv_data[:, 3].copy(),
        "started_at_utc": started,
        "completed_at_utc": completed,
        "sample_count": count,
        "channel_sample_counts_json": counts_json,
        "channel_configuration_json": configs_json,
        "initial_poll_interval_ms": recording.initial_poll_interval_ms,
        "final_poll_interval_ms": recording.final_poll_interval_ms,
        "backend": recording.backend,
        "simulated": recording.backend == "simulation",
        "comment": normalized_comment,
    }
    return PowerMonitorExportPayload(csv_data=csv_data, csv_header="\n".join(metadata), mat_data=mat_data)
