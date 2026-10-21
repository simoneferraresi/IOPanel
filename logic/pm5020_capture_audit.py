"""Offline audit for one archived CT400/PM5020 synchronized burst run.

Reads ONLY archived files produced by the M4 notebook. Does not import a
vendor driver, launch Qt, connect, arm, scan, or control optical emission.
A candidate index mapping is not a physical synchronization calibration.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from hardware.pm5020_types import BurstPowerData
from logic.trigger_sync_alignment import TriggerSyncError, align_ct400_burst, parse_ct400_sync_text

_RAW_HEADER = (
    "pm5020_buffer_index_0_based",
    "timestamp_raw_uint32_ticks",
    "ch1_power_W",
    "ch2_power_W",
)


class CaptureAuditError(ValueError):
    """A burst archive is missing, incomplete, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class IndexBaseCandidate:
    index_base: int
    structurally_valid: bool
    reason: str
    sample_count: int | None = None
    unmatched_prefix_samples: int | None = None
    unmatched_suffix_samples: int | None = None
    min_tick_step: int | None = None
    max_tick_step: int | None = None
    timing_qualified: bool = False


@dataclass(frozen=True, slots=True)
class BurstRunAudit:
    run_id: str
    pm5020_samples: int
    ct400_pulses: int
    ct400_first_pulse_index: int
    ct400_last_pulse_index: int
    wavelength_start_nm: float
    wavelength_end_nm: float
    candidates: tuple[IndexBaseCandidate, IndexBaseCandidate]
    timing_qualified: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _verified_path(directory: Path, name: str, metadata: dict[str, object]) -> Path:
    files = metadata.get("files")
    if not isinstance(files, dict) or not isinstance(files.get(name), dict):
        raise CaptureAuditError(f"Metadata lacks file integrity record: {name}")
    record = files[name]
    expected_file = "pm5020_raw_burst.csv" if name == "pm5020_raw_burst" else "sync_wavelength.txt"
    if record.get("file") != expected_file:
        raise CaptureAuditError(f"Unexpected archived filename for {name}")
    expected_sha = record.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise CaptureAuditError(f"Invalid metadata SHA-256 for {name}")
    path = directory / expected_file
    if not path.is_file():
        raise CaptureAuditError(f"Missing archived file: {expected_file}")
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise CaptureAuditError(f"SHA-256 mismatch: {expected_file}; archive may be modified/incomplete")
    return path


def _read_pm5020_csv(path: Path) -> BurstPowerData:
    ticks: list[int] = []
    ch1: list[float] = []
    ch2: list[float] = []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if header is None or tuple(header) != _RAW_HEADER:
                raise CaptureAuditError("Unexpected raw PM5020 CSV header")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 4:
                    raise CaptureAuditError(f"Invalid PM5020 CSV row {row_number}: expected four columns")
                index_text, tick_text, p1_text, p2_text = row
                if not index_text.isdecimal() or int(index_text) != row_number - 2:
                    raise CaptureAuditError(f"Nonconsecutive or invalid PM5020 buffer index at row {row_number}")
                if not tick_text.isdecimal():
                    raise CaptureAuditError(f"Invalid native uint32 timestamp at row {row_number}")
                ticks.append(int(tick_text))
                ch1.append(float(p1_text))
                ch2.append(float(p2_text))
        return BurstPowerData(tuple(ticks), tuple(ch1), tuple(ch2))
    except (ValueError, OverflowError) as exc:
        if isinstance(exc, CaptureAuditError):
            raise
        raise CaptureAuditError(f"Malformed PM5020 raw CSV: {exc}") from exc


def audit_pm5020_burst_run(run_directory: str | Path) -> BurstRunAudit:
    """Audit raw archive and report both mapping hypotheses, without choosing one."""
    directory = Path(run_directory)
    if not directory.is_dir():
        raise CaptureAuditError(f"Run directory does not exist: {directory}")
    metadata_path = directory / "acquisition_metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptureAuditError(f"Cannot read acquisition_metadata.json: {exc}") from exc
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise CaptureAuditError("Unsupported or missing M4 acquisition metadata schema")
    run_id = metadata.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise CaptureAuditError("Missing acquisition run_id")
    burst = _read_pm5020_csv(_verified_path(directory, "pm5020_raw_burst", metadata))
    sync_path = _verified_path(directory, "ct400_sync", metadata)
    try:
        pulses = parse_ct400_sync_text(sync_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TriggerSyncError) as exc:
        raise CaptureAuditError(f"Invalid CT400 sync archive: {exc}") from exc

    reported_count = metadata.get("pm5020_reported_burst_samples")
    if type(reported_count) is not int or reported_count != burst.sample_count:
        raise CaptureAuditError(
            f"Metadata PM5020 reported sample count {reported_count!r} does not match "
            f"archived sample count {burst.sample_count}"
        )

    candidates: list[IndexBaseCandidate] = []
    for base in (0, 1):
        try:
            aligned = align_ct400_burst(pulses, burst, index_base=base)
        except TriggerSyncError as exc:
            candidates.append(IndexBaseCandidate(base, False, str(exc)))
        else:
            candidates.append(
                IndexBaseCandidate(
                    base, True, "Provisional index correspondence only; no hardware timing certification",
                    sample_count=aligned.sample_count,
                    unmatched_prefix_samples=aligned.buffer_indices[0],
                    unmatched_suffix_samples=burst.sample_count - aligned.buffer_indices[-1] - 1,
                    min_tick_step=aligned.min_tick_step,
                    max_tick_step=aligned.max_tick_step,
                )
            )

    return BurstRunAudit(
        run_id=run_id,
        pm5020_samples=burst.sample_count,
        ct400_pulses=len(pulses),
        ct400_first_pulse_index=pulses[0].pulse_index,
        ct400_last_pulse_index=pulses[-1].pulse_index,
        wavelength_start_nm=pulses[0].wavelength_nm,
        wavelength_end_nm=pulses[-1].wavelength_nm,
        candidates=(candidates[0], candidates[1]),
    )
