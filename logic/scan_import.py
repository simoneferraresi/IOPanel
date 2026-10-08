"""Strict, hardware-independent reader for IOPanel schema-v2 scan exports."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput

MAX_SCAN_FILE_BYTES = 32 * 1024 * 1024
_CSV_COLUMNS = (
    "WL_[nm]",
    "Transfer_function_Det_1_[dB]",
    "Transfer_function_Det_2_[dB]",
    "Transfer_function_Det_3_[dB]",
    "Transfer_function_Det_4_[dB]",
)
_MAT_TRACE_KEYS = tuple(f"transfer_function_det_{i}_dB" for i in range(1, 5))


class ScanImportError(ValueError):
    """Invalid or unsupported IOPanel scan export with a user-readable reason."""


@dataclass(frozen=True)
class ImportedScan:
    """Immutable scan data and only the metadata actually stored by schema v2."""

    wavelengths_nm: np.ndarray
    detector_data: np.ndarray
    detectors: tuple[Detector, ...]
    source_path: Path
    source_format: str
    schema_version: int
    completed_at_utc: datetime
    resolution_pm: int
    speed_nm_s: float
    laser_power: float
    laser_power_unit: str
    laser_input: LaserInput
    comment: str
    backend: str
    simulated: bool
    result_kind: CT400ScanResultKind
    raw_result_code: int
    final_pout: float | None

    def __post_init__(self) -> None:
        wavelengths = np.array(self.wavelengths_nm, dtype=float, copy=True)
        values = np.array(self.detector_data, dtype=float, copy=True)
        if wavelengths.ndim != 1 or values.shape != (len(self.detectors), len(wavelengths)):
            raise ValueError("Imported scan arrays have inconsistent dimensions")
        wavelengths.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "wavelengths_nm", wavelengths)
        object.__setattr__(self, "detector_data", values)


def load_scan(path: Path | str) -> ImportedScan:
    """Load an IOPanel schema-v2 CSV or MAT export without touching hardware."""
    source = Path(path)
    extension = source.suffix.lower()
    if extension not in {".csv", ".mat"}:
        raise ScanImportError(f"Unsupported scan file type '{source.suffix or '(none)'}'. Choose a .csv or .mat file.")
    try:
        size = source.stat().st_size
        if size == 0:
            raise ScanImportError("The selected scan file is empty.")
        if size > MAX_SCAN_FILE_BYTES:
            raise ScanImportError("The selected scan file exceeds the 32 MiB import limit.")
        if extension == ".csv":
            parsed = _load_csv(source)
        else:
            parsed = _load_mat(source)
    except ScanImportError:
        raise
    except Exception as error:  # Normalize SciPy's format-specific parse failures for the GUI.
        raise ScanImportError(f"Could not read '{source.name}': {error}") from error
    return ImportedScan(source_path=source.resolve(), source_format=extension[1:].upper(), **parsed)


def _validate_ids(ids: list[int], count: int, mask: list[int]) -> tuple[Detector, ...]:
    if count != len(ids) or count < 1 or count > 4:
        raise ScanImportError("Active detector count does not match the detector identity list.")
    if len(set(ids)) != len(ids):
        raise ScanImportError("Active detector identities contain duplicates.")
    if any(detector_id not in (1, 2, 3, 4) for detector_id in ids):
        raise ScanImportError("Active detector IDs must be between 1 and 4.")
    if len(mask) != 4 or any(bit not in (0, 1) for bit in mask):
        raise ScanImportError("Optical detector mask must contain four 0/1 values.")
    if set(ids) != {index + 1 for index, active in enumerate(mask) if active}:
        raise ScanImportError("Optical detector mask is inconsistent with active detector identities.")
    return tuple(Detector(detector_id) for detector_id in ids)


def _metadata(meta: dict[str, str]) -> dict[str, Any]:
    required = {
        "SchemaVersion",
        "CompletedAtUTC",
        "Resolution(pm)",
        "Speed(nm/s)",
        "LaserPower",
        "Input",
        "ActiveDetectorCount",
        "ActiveDetectors",
        "OpticalDetectorMask(DE1-DE4)",
        "Backend",
        "Simulated",
        "ResultKind",
        "RawResultCode",
        "Comment",
    }
    missing = sorted(required - meta.keys())
    if missing:
        raise ScanImportError("Missing required metadata: " + ", ".join(missing))
    if _integer(meta["SchemaVersion"], "SchemaVersion") != 2:
        raise ScanImportError(
            f"Unsupported scan schema version '{meta['SchemaVersion']}'. Only version 2 is supported."
        )
    try:
        timestamp = datetime.fromisoformat(meta["CompletedAtUTC"])
    except ValueError as error:
        raise ScanImportError("CompletedAtUTC is not a valid ISO-8601 timestamp.") from error
    if timestamp.utcoffset() is None:
        raise ScanImportError("CompletedAtUTC must include a timezone.")
    resolution = _integer(meta["Resolution(pm)"], "Resolution(pm)")
    speed = _number(meta["Speed(nm/s)"], "Speed(nm/s)")
    power_parts = meta["LaserPower"].strip().rsplit(maxsplit=1)
    if len(power_parts) != 2 or power_parts[1] not in {"mW", "dBm"}:
        raise ScanImportError("LaserPower must include a numeric value and mW or dBm unit.")
    power = _number(power_parts[0], "LaserPower")
    try:
        laser_input = LaserInput[meta["Input"]]
    except KeyError as error:
        raise ScanImportError(f"Invalid laser input '{meta['Input']}'.") from error
    names = [name.strip() for name in meta["ActiveDetectors"].split(",") if name.strip()]
    try:
        ids = [Detector[name].value for name in names]
    except KeyError as error:
        raise ScanImportError(f"Invalid active detector identity '{error.args[0]}'.") from error
    mask = [_integer(part.strip(), "OpticalDetectorMask") for part in meta["OpticalDetectorMask(DE1-DE4)"].split(",")]
    detectors = _validate_ids(ids, _integer(meta["ActiveDetectorCount"], "ActiveDetectorCount"), mask)
    simulated_text = meta["Simulated"].strip().lower()
    if simulated_text not in {"true", "false"}:
        raise ScanImportError("Simulated must be True or False.")
    try:
        result_kind = CT400ScanResultKind[meta["ResultKind"]]
    except KeyError as error:
        raise ScanImportError(f"Invalid ResultKind '{meta['ResultKind']}'.") from error
    backend = meta["Backend"].strip()
    if not backend:
        raise ScanImportError("Backend metadata cannot be empty.")
    try:
        comment_value = json.loads(meta["Comment"])
    except json.JSONDecodeError as error:
        raise ScanImportError("Comment metadata is not a valid JSON string.") from error
    if not isinstance(comment_value, str):
        raise ScanImportError("Comment metadata must encode a string.")
    pout = _number(meta["Pout(dBm)"], "Pout(dBm)") if "Pout(dBm)" in meta else None
    if resolution <= 0 or speed <= 0:
        raise ScanImportError("Resolution and speed must be positive.")
    return {
        "schema_version": 2,
        "completed_at_utc": timestamp,
        "resolution_pm": resolution,
        "speed_nm_s": speed,
        "laser_power": power,
        "laser_power_unit": power_parts[1],
        "laser_input": laser_input,
        "detectors": detectors,
        "comment": comment_value,
        "backend": backend,
        "simulated": simulated_text == "true",
        "result_kind": result_kind,
        "raw_result_code": _integer(meta["RawResultCode"], "RawResultCode"),
        "final_pout": pout,
    }


def _load_csv(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    meta: dict[str, str] = {}
    lines = text.splitlines()
    header_index = None
    for index, line in enumerate(lines):
        if not line.startswith("#"):
            if line.strip():
                raise ScanImportError("CSV data begins before the required metadata and column header.")
            continue
        content = line[1:].strip()
        if content.startswith("WL_[nm]"):
            header_index = index
            if tuple(value.strip() for value in content.split(",")) != _CSV_COLUMNS:
                raise ScanImportError("CSV column header does not match the IOPanel schema-v2 columns.")
            break
        if ":" in content:
            key, value = content.split(":", 1)
            key = key.strip()
            if key in meta:
                raise ScanImportError(f"Duplicate metadata field '{key}'.")
            meta[key] = value.strip()
    if header_index is None:
        raise ScanImportError("Missing required wavelength data column header.")
    metadata = _metadata(meta)
    rows = list(csv.reader(lines[header_index + 1 :]))
    rows = [row for row in rows if row and any(cell.strip() for cell in row)]
    if not rows:
        raise ScanImportError("CSV contains no wavelength samples.")
    if any(len(row) != 5 for row in rows):
        raise ScanImportError("CSV contains a truncated row or a row with an unexpected number of columns.")
    try:
        data = np.asarray([[float(cell.strip()) for cell in row] for row in rows], dtype=float)
    except ValueError as error:
        raise ScanImportError("CSV contains invalid numeric scan data.") from error
    return _assemble(data[:, 0], data[:, 1:].T, metadata)


def _mat_string(value: Any, name: str) -> str:
    array = np.asarray(value)
    if array.size == 1:
        scalar = array.reshape(-1)[0]
        if isinstance(scalar, bytes):
            return scalar.decode("utf-8").strip()
        if isinstance(scalar, str):
            return scalar.strip()
    if array.dtype.kind in {"U", "S"} and array.ndim <= 2:
        return "".join(str(item) for item in array.reshape(-1)).strip()
    raise ScanImportError(f"MAT field '{name}' must be a scalar string.")


def _load_mat(path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = loadmat(path, squeeze_me=False, chars_as_strings=True)
    required = {
        "schema_version",
        "wl_nm",
        *_MAT_TRACE_KEYS,
        "active_detector_count",
        "active_detector_ids",
        "active_detector_mask",
        "completed_at_utc",
        "res_pm",
        "speed_nms",
        "lp_set",
        "lp_unit",
        "laser_input",
        "backend",
        "simulated",
        "result_kind",
        "raw_result_code",
        "comment",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ScanImportError("Missing required MAT fields: " + ", ".join(missing))
    meta = {
        "SchemaVersion": str(_scalar_int(raw["schema_version"], "schema_version")),
        "CompletedAtUTC": _mat_string(raw["completed_at_utc"], "completed_at_utc"),
        "Resolution(pm)": str(_scalar_int(raw["res_pm"], "res_pm")),
        "Speed(nm/s)": _mat_string_or_number(raw["speed_nms"], "speed_nms"),
        "LaserPower": f"{_mat_string_or_number(raw['lp_set'], 'lp_set')} {_mat_string(raw['lp_unit'], 'lp_unit')}",
        "Input": _mat_string(raw["laser_input"], "laser_input"),
        "ActiveDetectorCount": str(_scalar_int(raw["active_detector_count"], "active_detector_count")),
        "ActiveDetectors": ",".join(
            f"DE_{int(x)}" for x in _vector(raw["active_detector_ids"], "active_detector_ids", integer=True)
        ),
        "OpticalDetectorMask(DE1-DE4)": ",".join(
            str(x) for x in _vector(raw["active_detector_mask"], "active_detector_mask", integer=True)
        ),
        "Backend": _mat_string(raw["backend"], "backend"),
        "Simulated": str(bool(_scalar_int(raw["simulated"], "simulated"))),
        "ResultKind": _mat_string(raw["result_kind"], "result_kind"),
        "RawResultCode": str(_scalar_int(raw["raw_result_code"], "raw_result_code")),
        "Comment": json.dumps(_mat_string(raw["comment"], "comment"), ensure_ascii=False),
    }
    if "pout_dBm" in raw:
        meta["Pout(dBm)"] = _mat_string_or_number(raw["pout_dBm"], "pout_dBm")
    metadata = _metadata(meta)
    wavelengths = _vector(raw["wl_nm"], "wl_nm")
    detector_vectors = [_vector(raw[key], key) for key in _MAT_TRACE_KEYS]
    if any(values.size != wavelengths.size for values in detector_vectors):
        raise ScanImportError("Detector arrays must each contain exactly one sample per wavelength.")
    detector_rows = np.vstack(detector_vectors)
    return _assemble(wavelengths, detector_rows, metadata)


def _assemble(wavelengths: np.ndarray, all_rows: np.ndarray, metadata: dict[str, Any]) -> dict[str, Any]:
    if wavelengths.ndim != 1 or wavelengths.size == 0:
        raise ScanImportError("Wavelength samples must be a non-empty vector.")
    if all_rows.shape != (4, wavelengths.size):
        raise ScanImportError("Detector arrays must each contain exactly one sample per wavelength.")
    active_rows = [detector.value - 1 for detector in metadata["detectors"]]
    return {**metadata, "wavelengths_nm": wavelengths, "detector_data": all_rows[active_rows]}


def _vector(value: Any, name: str, *, integer: bool = False) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim not in (1, 2) or (array.ndim == 2 and min(array.shape) != 1):
        raise ScanImportError(f"MAT field '{name}' must be a vector, got shape {array.shape}.")
    try:
        values = np.asarray(array.reshape(-1), dtype=float)
    except (TypeError, ValueError) as error:
        raise ScanImportError(f"MAT field '{name}' must contain numeric values.") from error
    if integer and (not np.all(np.isfinite(values)) or not np.all(values == np.floor(values))):
        raise ScanImportError(f"MAT field '{name}' must contain finite integers.")
    return values.astype(int) if integer else values


def _scalar_int(value: Any, name: str) -> int:
    arr = np.asarray(value)
    if arr.size != 1:
        raise ScanImportError(f"MAT field '{name}' must be scalar.")
    number = float(arr.reshape(-1)[0])
    if not math.isfinite(number) or number != math.floor(number):
        raise ScanImportError(f"MAT field '{name}' must be a finite integer.")
    return int(number)


def _mat_string_or_number(value: Any, name: str) -> str:
    try:
        return _mat_string(value, name)
    except ScanImportError:
        return str(_scalar_number(value, name))


def _scalar_number(value: Any, name: str) -> float:
    arr = np.asarray(value)
    if arr.size != 1:
        raise ScanImportError(f"MAT field '{name}' must be scalar.")
    return _number(str(arr.reshape(-1)[0]), name)


def _integer(value: str, name: str) -> int:
    try:
        return int(value.strip())
    except ValueError as error:
        raise ScanImportError(f"{name} must be an integer.") from error


def _number(value: str, name: str) -> float:
    try:
        number = float(value.strip())
    except ValueError as error:
        raise ScanImportError(f"{name} must be numeric.") from error
    if not math.isfinite(number):
        raise ScanImportError(f"{name} must be finite.")
    return number
