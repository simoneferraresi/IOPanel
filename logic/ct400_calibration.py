"""Hardware-independent planning and durable records for CT400 ETA calibration.

Physical campaign orchestration is intentionally not provided here. A caller must
use the normal CT400 scan panel after the connection-state safety gate is ready.
"""

from __future__ import annotations

import csv
import json
import math
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import median, pstdev
from typing import Any

from logic.scan_eta import estimate_sweep_seconds


@dataclass(frozen=True)
class CalibrationCase:
    """One scan condition in a speed block."""

    case_id: str
    start_wavelength_nm: float
    end_wavelength_nm: float
    speed_nm_s: float
    resolution_pm: int
    detectors: tuple[str, ...] = ("DE_1",)
    repetitions: int = 1

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be empty")
        if not all(math.isfinite(value) for value in (self.start_wavelength_nm, self.end_wavelength_nm)):
            raise ValueError("wavelengths must be finite")
        if self.start_wavelength_nm >= self.end_wavelength_nm:
            raise ValueError("start wavelength must be below end wavelength")
        if not math.isfinite(self.speed_nm_s) or self.speed_nm_s <= 0:
            raise ValueError("speed must be finite and positive")
        if self.resolution_pm <= 0 or self.repetitions <= 0:
            raise ValueError("resolution and repetitions must be positive")
        detectors = tuple(self.detectors)
        if not detectors or any(detector not in {"DE_1", "DE_2", "DE_3", "DE_4"} for detector in detectors):
            raise ValueError("detectors must select one or more of DE_1 through DE_4")
        if len(set(detectors)) != len(detectors):
            raise ValueError("detector selections must not contain duplicates")
        object.__setattr__(self, "detectors", detectors)


@dataclass(frozen=True)
class CalibrationRun:
    campaign_id: str
    run_id: str
    case_id: str
    repetition: int
    speed_block_nm_s: float
    start_wavelength_nm: float
    end_wavelength_nm: float
    resolution_pm: int
    detectors: tuple[str, ...]
    predicted_sample_count: int
    nominal_sweep_seconds: float


ISSUE_140_CASES = (
    CalibrationCase("A", 1530.0, 1535.0, 10.0, 1, ("DE_1",), 3),
    CalibrationCase("B", 1530.0, 1545.0, 10.0, 1, ("DE_1",), 3),
    CalibrationCase("C", 1530.0, 1565.0, 10.0, 1, ("DE_1",), 3),
    CalibrationCase("E", 1530.0, 1565.0, 10.0, 10, ("DE_1",), 3),
    CalibrationCase("F", 1530.0, 1565.0, 10.0, 1, ("DE_1", "DE_2", "DE_3", "DE_4"), 3),
    CalibrationCase("D", 1530.0, 1565.0, 5.0, 1, ("DE_1",), 3),
)


def build_calibration_matrix(cases: tuple[CalibrationCase, ...] = ISSUE_140_CASES) -> tuple[CalibrationRun, ...]:
    """Expand the Issue #140 rows in declared order, retaining contiguous speed blocks."""
    if not cases:
        raise ValueError("at least one case is required")
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("case IDs must be unique")
    seen_speeds: set[float] = set()
    previous_speed: float | None = None
    for case in cases:
        if case.speed_nm_s != previous_speed:
            if case.speed_nm_s in seen_speeds:
                raise ValueError("speed blocks must be contiguous and require a single checkpoint")
            seen_speeds.add(case.speed_nm_s)
            previous_speed = case.speed_nm_s
    campaign_id = str(uuid.uuid4())
    runs = []
    for case in cases:
        sample_count = math.ceil((case.end_wavelength_nm - case.start_wavelength_nm) * 1000 / case.resolution_pm) + 1
        for repetition in range(1, case.repetitions + 1):
            runs.append(
                CalibrationRun(
                    campaign_id=campaign_id,
                    run_id=str(uuid.uuid4()),
                    case_id=case.case_id,
                    repetition=repetition,
                    speed_block_nm_s=case.speed_nm_s,
                    start_wavelength_nm=case.start_wavelength_nm,
                    end_wavelength_nm=case.end_wavelength_nm,
                    resolution_pm=case.resolution_pm,
                    detectors=case.detectors,
                    predicted_sample_count=sample_count,
                    nominal_sweep_seconds=estimate_sweep_seconds(
                        case.start_wavelength_nm, case.end_wavelength_nm, case.speed_nm_s
                    )
                    or 0.0,
                )
            )
    return tuple(runs)


@dataclass
class CalibrationCampaign:
    """Persistable campaign snapshot. Records are committed after every run."""

    campaign_id: str
    created_at_utc: str
    settings: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    analysis_summary: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, settings: dict[str, Any], provenance: dict[str, Any] | None = None) -> CalibrationCampaign:
        return cls(str(uuid.uuid4()), datetime.now(UTC).isoformat(), settings, provenance or {})


def append_calibration_record(directory: Path, campaign: CalibrationCampaign, record: dict[str, Any]) -> None:
    """Journal a record durably, then atomically refresh CSV and manifest snapshots."""
    directory.mkdir(parents=True, exist_ok=True)
    if not record.get("run_id"):
        raise ValueError("record requires a run_id")
    if any(existing.get("run_id") == record["run_id"] for existing in campaign.records):
        raise ValueError("run_id already recorded")
    journal = directory / f"{campaign.campaign_id}.jsonl"
    encoded_record = (json.dumps(record, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    with journal.open("ab" if campaign.records else "xb") as stream:
        stream.write(encoded_record)
        stream.flush()
        os.fsync(stream.fileno())
    campaign.records.append(dict(record))
    campaign.analysis_summary = summarize_calibration(campaign.records)
    _atomic_write(directory / f"{campaign.campaign_id}.json", _json_bytes(asdict(campaign)))
    _atomic_write(directory / f"{campaign.campaign_id}.csv", _csv_bytes(campaign.records))


def restore_campaign_journal(directory: Path, campaign: CalibrationCampaign) -> int:
    """Rebuild snapshots from durable records after interruption; never resume scans."""
    journal = directory / f"{campaign.campaign_id}.jsonl"
    records: list[dict[str, Any]] = []
    raw = journal.read_bytes()
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            if index == len(lines) - 1 and not line.endswith((b"\n", b"\r")):
                break  # A crash may leave only the final journal row incomplete.
            raise ValueError(f"Invalid calibration journal row {index + 1}") from None
        if not isinstance(record, dict) or not record.get("run_id"):
            raise ValueError(f"Invalid calibration journal row {index + 1}")
        if record.get("campaign_id", campaign.campaign_id) != campaign.campaign_id:
            raise ValueError(f"Journal campaign ID mismatch on row {index + 1}")
        records.append(record)
    if len({record["run_id"] for record in records}) != len(records):
        raise ValueError("Calibration journal contains duplicate run IDs")
    journal_bytes = b"".join(
        (json.dumps(record, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        for record in records
    )
    _atomic_write(journal, journal_bytes)
    campaign.records = records
    campaign.analysis_summary = summarize_calibration(records)
    _atomic_write(directory / f"{campaign.campaign_id}.json", _json_bytes(asdict(campaign)))
    _atomic_write(directory / f"{campaign.campaign_id}.csv", _csv_bytes(records))
    return len(records)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _atomic_write(target: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _csv_bytes(records: list[dict[str, Any]]) -> bytes:
    fields: list[str] = []
    for record in records:
        for key in record:
            if key not in fields:
                fields.append(key)
    if not fields:
        return b""
    from io import StringIO

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                key: json.dumps(value, sort_keys=True, separators=(",", ":"))
                if isinstance(value, (dict, list, tuple))
                else value
                for key, value in record.items()
            }
        )
    return output.getvalue().encode("utf-8-sig")


def summarize_calibration(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare predictions by speed and case, including observed stage contributions."""
    groups: dict[str, list[float]] = {}
    case_groups: dict[str, list[dict[str, Any]]] = {}
    outcome_counts: dict[str, int] = {}
    rows = []
    for record in records:
        outcome = str(record.get("outcome", "unknown"))
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        actual = record.get("gui_start_to_completion_seconds")
        nominal = record.get("nominal_sweep_seconds")
        if (
            record.get("outcome") != "success"
            or not isinstance(actual, (int, float))
            or not isinstance(nominal, (int, float))
            or not math.isfinite(actual)
            or not math.isfinite(nominal)
        ):
            continue
        error = float(actual) - float(nominal)
        key = str(record.get("speed_block_nm_s", "unknown"))
        groups.setdefault(key, []).append(error)
        case_groups.setdefault(str(record.get("case_id", "unknown")), []).append(record)
        rows.append({"run_id": record.get("run_id"), "error_seconds": error})
    by_speed = {
        speed: {
            "count": len(errors),
            "median_error_seconds": median(errors),
            "min_error_seconds": min(errors),
            "max_error_seconds": max(errors),
            "run_to_run_sd_seconds": pstdev(errors),
        }
        for speed, errors in groups.items()
    }
    by_case = {}
    for case_id, case_records in case_groups.items():
        errors = [
            float(record["gui_start_to_completion_seconds"]) - float(record["nominal_sweep_seconds"])
            for record in case_records
        ]
        stage_values: dict[str, list[float]] = {}
        for record in case_records:
            stages = record.get("stage_seconds")
            if not isinstance(stages, dict):
                continue
            for stage, value in stages.items():
                if isinstance(value, (int, float)) and math.isfinite(value):
                    stage_values.setdefault(str(stage), []).append(float(value))
        by_case[case_id] = {
            "count": len(case_records),
            "median_error_seconds": median(errors),
            "min_error_seconds": min(errors),
            "max_error_seconds": max(errors),
            "median_stage_seconds": {stage: median(values) for stage, values in sorted(stage_values.items())},
        }
    return {
        "total_measurements": len(records),
        "successful_measurements": len(rows),
        "outcome_counts": outcome_counts,
        "by_speed_nm_s": by_speed,
        "by_case": by_case,
        "runs": rows,
    }
