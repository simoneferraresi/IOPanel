"""Pure schema-v1 exports for immutable Power Monitor recordings."""

import json
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

import numpy as np

from hardware.ct400_types import Detector
from logic.power_monitor_recording import PowerMonitorRecording

POWER_MONITOR_EXPORT_SCHEMA_VERSION = 1
POWER_MONITOR_EXPORT_DETECTORS = (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4)
POWER_MONITOR_CSV_COLUMNS = (
    "Elapsed_time_[s]",
    "Pout_[dBm]",
    "Power_Det_1_[dBm]",
    "Power_Det_2_[dBm]",
    "Power_Det_3_[dBm]",
    "Power_Det_4_[dBm]",
)


def derive_power_monitor_export_targets(
    selected_path: str | Path,
    *,
    include_csv: bool,
    include_mat: bool,
) -> dict[str, Path]:
    """Return selected CSV/MAT paths after stripping one recognized suffix."""
    path = Path(selected_path)
    stem = path.with_suffix("") if path.suffix.lower() in {".csv", ".mat"} else path
    targets: dict[str, Path] = {}
    if include_csv:
        targets["CSV"] = stem.with_name(stem.name + ".csv")
    if include_mat:
        targets["MAT"] = stem.with_name(stem.name + ".mat")
    return targets


def default_power_monitor_export_name(recording: PowerMonitorRecording) -> str:
    """Return a stable basename derived from the recording's UTC start time."""
    return recording.started_at_utc.astimezone(UTC).strftime("power_monitor_%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class PowerMonitorExportPayload:
    """Independent CSV and MAT representations of one completed recording."""

    csv_data: np.ndarray
    csv_header: str
    mat_data: dict[str, object]


def _utc_isoformat(value) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def build_power_monitor_export_v1(
    recording: PowerMonitorRecording,
    *,
    comment: str = "",
) -> PowerMonitorExportPayload:
    """Build fixed DE1-DE4 outputs, mapping selected rows by detector identity."""
    sample_count = len(recording.elapsed_s)
    if sample_count == 0:
        raise ValueError("This recording contains no captured samples and cannot be exported.")

    detector_data = {detector: recording.detector_data[row].copy() for row, detector in enumerate(recording.detectors)}
    fixed_data = {
        detector: detector_data.get(detector, np.full(sample_count, np.nan, dtype=float))
        for detector in POWER_MONITOR_EXPORT_DETECTORS
    }
    elapsed = recording.elapsed_s.copy()
    pout = recording.pout_data.copy()
    csv_data = np.column_stack((elapsed, pout, *(fixed_data[detector] for detector in POWER_MONITOR_EXPORT_DETECTORS)))
    csv_data.setflags(write=False)

    settings = recording.settings
    started_at_utc = _utc_isoformat(recording.started_at_utc)
    completed_at_utc = _utc_isoformat(recording.completed_at_utc)
    active_detectors = ",".join(detector.name for detector in recording.detectors)
    active_detector_ids = np.asarray([int(detector) for detector in recording.detectors], dtype=np.int64)
    active_detector_mask = np.asarray(
        [int(detector in recording.detectors) for detector in POWER_MONITOR_EXPORT_DETECTORS], dtype=np.int8
    )
    normalized_comment = str(comment).replace("\r\n", "\n").replace("\r", "\n")
    detector_unit = recording.detector_unit
    pout_unit = recording.pout_unit

    metadata = [
        "# MeasurementType: PowerMonitorTimeRecording",
        f"# SchemaVersion: {POWER_MONITOR_EXPORT_SCHEMA_VERSION}",
        f"# StartedAtUTC: {started_at_utc}",
        f"# CompletedAtUTC: {completed_at_utc}",
        f"# Duration_s: {recording.duration_s:.17g}",
        f"# SampleCount: {sample_count}",
        f"# Wavelength_nm: {settings.wavelength_nm:.17g}",
        f"# RequestedPollInterval_ms: {settings.requested_poll_interval_ms}",
        f"# EnteredLaserPower: {json.dumps(settings.entered_laser_power, ensure_ascii=False)}",
        f"# EnteredLaserPowerUnit: {json.dumps(settings.entered_laser_power_unit, ensure_ascii=False)}",
        f"# LaserPower_mW: {settings.laser_power_mw:.17g}",
        f"# LaserInput: {settings.laser_input.name}",
        f"# ActiveDetectorCount: {len(recording.detectors)}",
        f"# ActiveDetectors: {active_detectors}",
        f"# OpticalDetectorMask(DE1-DE4): {','.join(map(str, active_detector_mask.tolist()))}",
        f"# Backend: {json.dumps(recording.backend, ensure_ascii=False)}",
        f"# Simulated: {recording.simulated}",
        f"# StopReason: {recording.stop_reason.name}",
        f"# DetectorUnit: {detector_unit}",
        f"# PoutUnit: {pout_unit}",
        f"# Comment: {json.dumps(normalized_comment, ensure_ascii=False)}",
        "# " + ", ".join(POWER_MONITOR_CSV_COLUMNS),
    ]
    csv_header = "\n".join(metadata)

    mat_data: dict[str, object] = {
        "measurement_type": "PowerMonitorTimeRecording",
        "schema_version": POWER_MONITOR_EXPORT_SCHEMA_VERSION,
        "elapsed_s": elapsed,
        "pout_dBm": pout,
        "power_det_1_dBm": fixed_data[Detector.DE_1],
        "power_det_2_dBm": fixed_data[Detector.DE_2],
        "power_det_3_dBm": fixed_data[Detector.DE_3],
        "power_det_4_dBm": fixed_data[Detector.DE_4],
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "duration_s": recording.duration_s,
        "sample_count": sample_count,
        "wavelength_nm": settings.wavelength_nm,
        "requested_poll_interval_ms": settings.requested_poll_interval_ms,
        "entered_laser_power": settings.entered_laser_power,
        "entered_laser_power_unit": settings.entered_laser_power_unit,
        "laser_power_mw": settings.laser_power_mw,
        "laser_input": settings.laser_input.name,
        "active_detector_count": len(recording.detectors),
        "active_detectors": active_detectors,
        "active_detector_ids": active_detector_ids,
        "optical_detector_mask_de1_de4": active_detector_mask,
        "backend": recording.backend,
        "simulated": recording.simulated,
        "stop_reason": recording.stop_reason.name,
        "detector_unit": detector_unit,
        "pout_unit": pout_unit,
        "comment": normalized_comment,
    }
    return PowerMonitorExportPayload(csv_data=csv_data, csv_header=csv_header, mat_data=mat_data)
