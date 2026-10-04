"""Pure schema-v2 export transformations for measurements and save-time annotations."""

import json
from dataclasses import dataclass
from datetime import UTC

import numpy as np

from hardware.ct400_types import Detector
from logic.scan_measurement import ScanMeasurement

SCAN_EXPORT_SCHEMA_VERSION = 2
OPTICAL_EXPORT_DETECTORS = (
    Detector.DE_1,
    Detector.DE_2,
    Detector.DE_3,
    Detector.DE_4,
)
CSV_COLUMNS = (
    "WL_[nm]",
    "Transfer_function_Det_1_[dB]",
    "Transfer_function_Det_2_[dB]",
    "Transfer_function_Det_3_[dB]",
    "Transfer_function_Det_4_[dB]",
)


@dataclass(frozen=True)
class ScanExportPayload:
    """Schema-v2 outputs from an immutable measurement and save-time annotation."""

    csv_data: np.ndarray
    csv_header: str
    mat_data: dict[str, object]


def build_scan_export_v2(measurement: ScanMeasurement, *, comment: str = "") -> ScanExportPayload:
    """Build schema-v2 outputs from a scan snapshot and an explicit save-time comment."""
    if Detector.DE_5 in measurement.detectors:
        raise ValueError(
            "External/BNC detector export is not yet supported because its unit and SetBNC configuration "
            "are not preserved in the measurement snapshot."
        )
    if not measurement.detectors:
        raise ValueError("Cannot export a scan with no wavelength-resolved detector data.")

    wavelengths = measurement.wavelengths_nm
    optical_data = {detector: np.zeros(wavelengths.shape, dtype=measurement.detector_data.dtype) for detector in OPTICAL_EXPORT_DETECTORS}
    for row_index, detector in enumerate(measurement.detectors):
        optical_data[detector] = measurement.detector_data[row_index]

    csv_data = np.column_stack((wavelengths, *(optical_data[detector] for detector in OPTICAL_EXPORT_DETECTORS)))
    settings = measurement.settings
    active_names = ",".join(detector.name for detector in measurement.detectors)
    active_ids = np.asarray([int(detector) for detector in measurement.detectors], dtype=np.int64)
    active_mask = np.asarray([int(detector in measurement.detectors) for detector in OPTICAL_EXPORT_DETECTORS], dtype=np.int8)
    completed_at_utc = measurement.completed_at_utc.astimezone(UTC).isoformat().replace("+00:00", "Z")

    metadata = [
        f"# SchemaVersion: {SCAN_EXPORT_SCHEMA_VERSION}",
        f"# CompletedAtUTC: {completed_at_utc}",
        f"# Resolution(pm): {settings.requested_resolution_pm}",
        f"# Speed(nm/s): {settings.requested_speed_nm_s}",
        f"# LaserPower: {settings.entered_laser_power} {settings.entered_laser_power_unit}",
        f"# Input: {settings.laser_input.name}",
        f"# ActiveDetectorCount: {len(measurement.detectors)}",
        f"# ActiveDetectors: {active_names}",
        f"# OpticalDetectorMask(DE1-DE4): {','.join(map(str, active_mask.tolist()))}",
        f"# Backend: {measurement.backend}",
        f"# Simulated: {measurement.simulated}",
        f"# ResultKind: {measurement.result_kind.name}",
        f"# RawResultCode: {measurement.raw_result_code}",
    ]
    if measurement.final_pout is not None:
        metadata.append(f"# Pout(dBm): {measurement.final_pout:.3f}")
    normalized_comment = comment.replace("\r\n", "\n").replace("\r", "\n")
    metadata.append(f"# Comment: {json.dumps(normalized_comment, ensure_ascii=False)}")
    csv_header = "\n".join((*metadata, "# " + ", ".join(CSV_COLUMNS)))

    mat_data: dict[str, object] = {
        "schema_version": SCAN_EXPORT_SCHEMA_VERSION,
        "wl_nm": wavelengths,
        "transfer_function_det_1_dB": optical_data[Detector.DE_1],
        "transfer_function_det_2_dB": optical_data[Detector.DE_2],
        "transfer_function_det_3_dB": optical_data[Detector.DE_3],
        "transfer_function_det_4_dB": optical_data[Detector.DE_4],
        "active_detector_count": len(measurement.detectors),
        "active_detector_ids": active_ids,
        "active_detector_mask": active_mask,
        "completed_at_utc": completed_at_utc,
        "res_pm": settings.requested_resolution_pm,
        "speed_nms": settings.requested_speed_nm_s,
        "lp_set": settings.entered_laser_power,
        "lp_unit": settings.entered_laser_power_unit,
        "laser_input": settings.laser_input.name,
        "backend": measurement.backend,
        "simulated": measurement.simulated,
        "result_kind": measurement.result_kind.name,
        "raw_result_code": measurement.raw_result_code,
        "comment": normalized_comment,
    }
    if measurement.final_pout is not None:
        mat_data["pout_dBm"] = measurement.final_pout

    return ScanExportPayload(csv_data=csv_data, csv_header=csv_header, mat_data=mat_data)
