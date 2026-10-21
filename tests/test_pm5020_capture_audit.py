"""Offline-only archive integrity and index-base hypotheses (synthetic data)."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from logic.pm5020_capture_audit import CaptureAuditError, audit_pm5020_burst_run


def _fixture(directory: Path, *, indices=(9, 10, 11), samples=14) -> Path:
    directory.mkdir(exist_ok=True)
    raw = directory / "pm5020_raw_burst.csv"
    with raw.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("pm5020_buffer_index_0_based", "timestamp_raw_uint32_ticks", "ch1_power_W", "ch2_power_W"))
        for i in range(samples):
            writer.writerow((i, 1000 + i * 90, 0.01 + 0.0001 * i, 1e-7 * (i + 1)))
    sync = directory / "sync_wavelength.txt"
    sync.write_text("".join(f"{i} {1550 + (i - 9) * 0.03:.5f}\n" for i in indices), encoding="utf-8")
    metadata = {
        "schema_version": 1,
        "run_id": "synthetic_123",
        "pm5020_reported_burst_samples": samples,
        "files": {
            "pm5020_raw_burst": {"file": raw.name, "sha256": hashlib.sha256(raw.read_bytes()).hexdigest()},
            "ct400_sync": {"file": sync.name, "sha256": hashlib.sha256(sync.read_bytes()).hexdigest()},
        },
    }
    (directory / "acquisition_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    return directory


def test_both_index_base_hypotheses_can_be_structurally_valid(tmp_path):
    audit = audit_pm5020_burst_run(_fixture(tmp_path / "run"))
    assert audit.pm5020_samples == 14
    assert audit.ct400_pulses == 3
    assert (audit.ct400_first_pulse_index, audit.ct400_last_pulse_index) == (9, 11)
    assert all(candidate.structurally_valid for candidate in audit.candidates)
    assert (audit.candidates[0].unmatched_prefix_samples, audit.candidates[1].unmatched_prefix_samples) == (9, 8)
    assert (audit.candidates[0].unmatched_suffix_samples, audit.candidates[1].unmatched_suffix_samples) == (2, 3)
    assert audit.candidates[0].min_tick_step == 90
    assert audit.candidates[0].max_tick_step == 90
    assert not audit.timing_qualified
    assert not audit.candidates[0].timing_qualified


def test_index_base_one_exceeds_buffer_zero_still_valid(tmp_path):
    audit = audit_pm5020_burst_run(_fixture(tmp_path / "run", indices=(13, 14), samples=14))
    assert not audit.candidates[0].structurally_valid
    assert audit.candidates[1].structurally_valid


def test_hash_mismatch_is_reported_before_alignment(tmp_path):
    path = _fixture(tmp_path / "run")
    with (path / "sync_wavelength.txt").open("a", encoding="utf-8") as handle:
        handle.write("12 1551\n")
    with pytest.raises(CaptureAuditError, match="SHA-256 mismatch"):
        audit_pm5020_burst_run(path)


def test_missing_or_inconsistent_raw_buffer_is_rejected(tmp_path):
    path = _fixture(tmp_path / "run")
    metadata_path = path / "acquisition_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["pm5020_reported_burst_samples"] = 999
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(CaptureAuditError, match="reported sample count"):
        audit_pm5020_burst_run(path)


def test_nonconsecutive_indices_rejected_even_with_matching_checksum(tmp_path):
    path = _fixture(tmp_path / "run")
    raw = path / "pm5020_raw_burst.csv"
    rows = list(csv.reader(raw.open(newline="", encoding="utf-8")))
    rows[2][0] = "19"
    with raw.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)
    metadata_path = path / "acquisition_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["files"]["pm5020_raw_burst"]["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(CaptureAuditError, match="Nonconsecutive"):
        audit_pm5020_burst_run(path)


def test_missing_run_directory_fails_readably(tmp_path):
    with pytest.raises(CaptureAuditError, match="does not exist"):
        audit_pm5020_burst_run(tmp_path / "absent")
