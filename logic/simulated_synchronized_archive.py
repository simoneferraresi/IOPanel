"""Transactional archival of simulated M4-E1 coordinated acquisitions.

All data retain native units. Exports are explicitly simulated and timing-
unqualified; this writer never opens instrument drivers or changes lasers.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from logic.simulated_synchronized_acquisition import SimulatedSynchronizedCapture

_PM_HEADER = (
    "pm5020_buffer_index_0_based",
    "timestamp_raw_uint32_ticks",
    "ch1_power_W",
    "ch2_power_W",
)


def archive_simulated_synchronized_capture(
    capture: SimulatedSynchronizedCapture,
    destination_root: str | Path,
) -> Path:
    """Write an immutable, checksum-linked run directory; never overwrite.

    A temporary sibling is populated first, then renamed into the selected
    root. Interrupted/failed preparation is removed without leaving a
    plausible completed run. Save the processed CT400 data independently of
    the original PM5020 native-rate samples.
    """
    if not capture.simulated or capture.timing_qualified or capture.aligned.timing_qualified:
        raise ValueError("M4-E1 archive supports simulated, timing-unqualified captures only")
    root = Path(destination_root)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    run_id = f"sim_m4e1_{uuid4().hex}"
    target = root / run_id
    stage = Path(tempfile.mkdtemp(prefix=".m4e1_staging_", dir=root))
    try:
        raw_path = stage / "pm5020_raw_burst.csv"
        with raw_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(_PM_HEADER)
            for index, (tick, ch1, ch2) in enumerate(
                zip(
                    capture.pm_burst.timestamps_ticks,
                    capture.pm_burst.power_ch1_w,
                    capture.pm_burst.power_ch2_w,
                    strict=True,
                )
            ):
                writer.writerow((index, tick, ch1, ch2))

        sync_path = stage / "sync_wavelength.txt"
        sync_path.write_text(capture.sync_text, encoding="utf-8")

        processed = stage / "ct400_processed.csv"
        detector_ids = [detector.value for detector in capture.settings.detectors]
        if len(capture.ct400_wavelengths_nm) == 0:
            raise ValueError("Cannot archive an empty CT400 processed scan")
        if len(capture.ct400_detector_data) != len(detector_ids):
            raise ValueError("CT400 processed detector count mismatch")
        if any(len(row) != len(capture.ct400_wavelengths_nm) for row in capture.ct400_detector_data):
            raise ValueError("CT400 processed data lengths are inconsistent")
        with processed.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("wavelength_nm", *(f"det{index}_vendor_native" for index in detector_ids)))
            for index, wavelength in enumerate(capture.ct400_wavelengths_nm):
                writer.writerow((wavelength, *(channel[index] for channel in capture.ct400_detector_data)))

        paths = {
            "pm5020_raw_burst": raw_path,
            "ct400_sync": sync_path,
            "ct400_processed": processed,
        }
        metadata = {
            "schema_version": 1,
            "milestone": "M4-E1",
            "run_id": run_id,
            "simulated": True,
            "timing_qualified": False,
            "pm5020_backend": "DummyPM5020",
            "ct400_backend": "DummyCT400",
            "pm5020_timestamp_unit": "native_uint32_ticks_unqualified",
            "pm5020_power_unit": "W",
            "ct400_processed_detector_unit": "vendor_native_unqualified",
            "index_base": 0,
            "index_base_status": "manufacturer_supported_provisional_cross_instrument",
            "completed_at_utc": capture.completed_at_utc.isoformat(),
            "ct400_scan_result_code": capture.status.raw_code,
            "ct400_scan_result_kind": capture.status.kind.name,
            "ct400_scan_message": capture.status.error_message,
            "ct400_settings": {
                **asdict(capture.settings),
                "detectors": detector_ids,
            },
            "pm5020_reported_burst_samples": capture.pm_burst.sample_count,
            "ct400_synchronization_pulses": len(capture.pulses),
            "ct400_processed_points": len(capture.ct400_wavelengths_nm),
            "files": {
                key: {
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for key, path in paths.items()
            },
        }
        (stage / "acquisition_metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if target.exists():
            raise FileExistsError(str(target))
        os.rename(stage, target)
        return target
    finally:
        if stage.exists():
            shutil.rmtree(stage)
