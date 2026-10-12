"""Hardware-free scan schema-v2 I/O and Qt pending-callback latency proxy.

Only synthetic ScanMeasurement data, QApplication and temporary local files.
This does not instantiate MainWindow or initialize vendor hardware.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtWidgets

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_export_worker import _write_staged_format
from logic.scan_import import ScanImportError, load_scan
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement


def _windows_memory() -> dict[str, float]:
    """Current/peak process working sets in MiB; absent on other OSes."""
    if sys.platform != "win32":
        return {}

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set", ctypes.c_size_t),
            ("working_set", ctypes.c_size_t),
            ("quota_peak_paged_pool", ctypes.c_size_t),
            ("quota_paged_pool", ctypes.c_size_t),
            ("quota_peak_nonpaged_pool", ctypes.c_size_t),
            ("quota_nonpaged_pool", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        return {}
    return {
        "working_set_mb": counters.working_set / (1024**2),
        "peak_working_set_mb": counters.peak_working_set / (1024**2),
    }


def _synthetic_scan(
    points: int, detectors: tuple[Detector, ...], *, include_nonfinite: bool = False
) -> ScanMeasurement:
    wavelengths = np.linspace(1520.0, 1580.0, points, dtype=np.float64)
    values = np.vstack([-20.0 + index + np.sin(wavelengths * 0.123 + index) for index in range(len(detectors))])
    if include_nonfinite:
        # Include representative nonfinite detector readings; the schema preserves them.
        values[0, points // 3] = np.nan
        values[-1, 2 * points // 3] = np.inf
    settings = ScanAcquisitionSettings(1520.0, 1580.0, 1, "5", "1", "mW", 1.0, LaserInput.LI_1, detectors)
    return ScanMeasurement(
        settings,
        wavelengths,
        values,
        detectors,
        None,
        CT400ScanResultKind.SUCCESS,
        0,
        "synthetic I/O benchmark",
        "synthetic.benchmark",
        True,
        datetime(2026, 10, 1, tzinfo=UTC),
    )


def _timed_on_qt_thread[T](app: QtWidgets.QApplication, operation: Callable[[], T]) -> tuple[T, dict[str, float]]:
    """A queued zero-delay callback exposes how long synchronous work blocks Qt.

    This is a *pending-callback latency proxy*, not GUI paint rate, event-loop
    p95, or the actual MainWindow file-handler implementation.
    """
    callbacks: list[float] = []
    QtCore.QTimer.singleShot(0, lambda: callbacks.append(time.perf_counter()))
    started = time.perf_counter()
    cpu_started = time.process_time()
    tracemalloc.start()
    try:
        result = operation()
        traced_current_bytes, traced_peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    elapsed = time.perf_counter() - started
    cpu = time.process_time() - cpu_started
    deadline = time.perf_counter() + 3.0
    while not callbacks and time.perf_counter() < deadline:
        app.processEvents()
    if not callbacks:
        raise RuntimeError("Qt pending-callback probe did not fire")
    return result, {
        "wall_s": elapsed,
        "cpu_s": cpu,
        "qt_pending_callback_delay_s": callbacks[0] - started,
        "python_traced_current_bytes_after_operation": traced_current_bytes,
        "python_traced_peak_bytes_during_operation": traced_peak_bytes,
        "python_traced_memory_scope": "Python allocations tracked during operation; excludes native NumPy/SciPy/Qt allocations",
        **_windows_memory(),
    }


def _verified_import(path: Path, expected: ScanMeasurement, expected_comment: str) -> None:
    scanned = load_scan(path)
    if scanned.detectors != expected.detectors or scanned.detector_data.shape != expected.detector_data.shape:
        raise AssertionError("Scan I/O benchmark round-trip changed detector identities or dimensions")
    if not np.allclose(scanned.wavelengths_nm, expected.wavelengths_nm, rtol=0, atol=1e-6):
        raise AssertionError("Scan I/O benchmark round-trip changed wavelength samples")
    if not np.allclose(scanned.detector_data, expected.detector_data, rtol=0, atol=1e-6, equal_nan=True):
        raise AssertionError("Scan I/O benchmark round-trip changed detector data")
    if scanned.comment != expected_comment:
        raise AssertionError("Scan I/O benchmark round-trip changed comment metadata")


def _characterize_case(
    app: QtWidgets.QApplication, folder: Path, count: int, num_detectors: int
) -> list[dict[str, object]]:
    """Keep operation callbacks scoped to one fixture instead of loop variables."""
    measurement = _synthetic_scan(count, tuple(Detector(i + 1) for i in range(num_detectors)), include_nonfinite=True)
    comment = "synthetic I/O benchmark metadata; " + "\n".join(
        "calibration note, unicode lambda, and escaped line" for _ in range(1800)
    )

    def build():
        return build_scan_export_v2(measurement, comment=comment)

    payload, build_stats = _timed_on_qt_thread(app, build)
    csv_path = folder / f"scan-{count}-{num_detectors}.csv"
    mat_path = folder / f"scan-{count}-{num_detectors}.mat"
    malformed_csv_path = folder / f"scan-{count}-{num_detectors}-malformed.csv"

    def export_csv() -> None:
        _write_staged_format("CSV", csv_path, payload, overwrite_confirmed=False)

    def export_mat() -> None:
        _write_staged_format("MAT", mat_path, payload, overwrite_confirmed=False)

    def create_malformed_fixture() -> None:
        with csv_path.open(encoding="utf-8") as source:
            lines = source.readlines()
        with malformed_csv_path.open("w", encoding="utf-8", newline="") as target:
            target.writelines(lines[:-1])
            target.write("malformed,row\n")

    def reject_malformed_csv() -> None:
        try:
            load_scan(malformed_csv_path)
        except ScanImportError:
            return
        raise AssertionError("Malformed scan CSV was accepted")

    def read_decode_csv() -> int:
        # Isolate filesystem read plus UTF-8-sig decoding from CSV parsing.
        # The fixture was just written in this subprocess, so this is a
        # warm-cache local-temp-filesystem measurement, not cold-disk latency.
        return len(csv_path.read_bytes().decode("utf-8-sig"))

    operations: tuple[tuple[str, Callable[[], object]], ...] = (
        ("build_payload", build),
        ("csv_export", export_csv),
        ("mat_export", export_mat),
        ("csv_file_read_decode", read_decode_csv),
        ("csv_import", lambda: _verified_import(csv_path, measurement, comment)),
        ("mat_import", lambda: _verified_import(mat_path, measurement, comment)),
        ("prepare_malformed_csv", create_malformed_fixture),
        ("csv_malformed_rejection", reject_malformed_csv),
    )
    rows: list[dict[str, object]] = []
    for label, operation in operations:
        if label == "build_payload":
            stats = build_stats
        elif label == "prepare_malformed_csv":
            _, stats = _timed_on_qt_thread(app, operation)
        else:
            _, stats = _timed_on_qt_thread(app, operation)
        artifact = {
            "csv_export": csv_path,
            "csv_import": csv_path,
            "csv_file_read_decode": csv_path,
            "mat_export": mat_path,
            "mat_import": mat_path,
            "prepare_malformed_csv": malformed_csv_path,
            "csv_malformed_rejection": malformed_csv_path,
        }.get(label)
        file_bytes = artifact.stat().st_size if artifact is not None and artifact.exists() else 0
        wall_s = float(stats["wall_s"])
        rows.append(
            {
                "points_per_scan": count,
                "detector_count": num_detectors,
                "operation": label,
                "qt_platform": app.platformName(),
                "window_system": sys.platform,
                "comment_characters": len(comment),
                "file_bytes": file_bytes,
                "throughput_mib_s": file_bytes / wall_s / (1024**2) if file_bytes and wall_s > 0 else 0.0,
                **stats,
            }
        )
    return rows


def characterize(points: list[int], detectors: list[int], output: Path) -> list[dict[str, object]]:
    if not points or any(p < 2 for p in points):
        raise ValueError("points must be >= 2")
    if not detectors or any(d not in (1, 4) for d in detectors):
        raise ValueError("detectors must be 1 or 4")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="iopanel-scan-io-") as temp:
        folder = Path(temp)
        for count in points:
            for num_detectors in detectors:
                rows.extend(_characterize_case(app, folder, count, num_detectors))
    output.write_text(json.dumps(rows, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", nargs="+", type=int, default=[10_000, 50_000, 100_000])
    parser.add_argument("--detectors", nargs="+", type=int, default=[1, 4])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = characterize(args.points, args.detectors, args.output)
    print(f"Measured {len(result)} synthetic scan I/O cases")


if __name__ == "__main__":
    main()
