"""Hardware-free scan schema-v2 I/O and Qt pending-callback latency proxy.

Only synthetic ScanMeasurement data, QApplication and temporary local files.
This does not instantiate MainWindow or initialize vendor hardware.
"""

from __future__ import annotations

import argparse
import ctypes
import io
import json
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtWidgets
from scipy.io import savemat

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_import import load_scan
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


def _synthetic_scan(points: int, detectors: tuple[Detector, ...]) -> ScanMeasurement:
    wavelengths = np.linspace(1520.0, 1580.0, points, dtype=np.float64)
    values = np.vstack(
        [-20.0 + index + np.sin(wavelengths * 0.123 + index) for index in range(len(detectors))]
    )
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


def _timed_on_qt_thread[T](
    app: QtWidgets.QApplication, operation: Callable[[], T]
) -> tuple[T, dict[str, float]]:
    """A queued zero-delay callback exposes how long synchronous work blocks Qt.

    This is a *pending-callback latency proxy*, not GUI paint rate, event-loop
    p95, or the actual MainWindow file-handler implementation.
    """
    callbacks: list[float] = []
    QtCore.QTimer.singleShot(0, lambda: callbacks.append(time.perf_counter()))
    started = time.perf_counter()
    cpu_started = time.process_time()
    result = operation()
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
        **_windows_memory(),
    }


def _verified_import(path: Path, expected: ScanMeasurement) -> None:
    scanned = load_scan(path)
    if scanned.detectors != expected.detectors or scanned.detector_data.shape != expected.detector_data.shape:
        raise AssertionError("Scan I/O benchmark round-trip changed detector identities or dimensions")
    if not np.allclose(scanned.wavelengths_nm, expected.wavelengths_nm, rtol=0, atol=1e-6):
        raise AssertionError("Scan I/O benchmark round-trip changed wavelength samples")
    if not np.allclose(scanned.detector_data, expected.detector_data, rtol=0, atol=1e-6):
        raise AssertionError("Scan I/O benchmark round-trip changed detector data")


def _characterize_case(
    app: QtWidgets.QApplication, folder: Path, count: int, num_detectors: int
) -> list[dict[str, object]]:
    """Keep operation callbacks scoped to one fixture instead of loop variables."""
    measurement = _synthetic_scan(count, tuple(Detector(i + 1) for i in range(num_detectors)))

    def build():
        return build_scan_export_v2(measurement, comment="synthetic I/O benchmark")

    payload, build_stats = _timed_on_qt_thread(app, build)
    csv_path = folder / f"scan-{count}-{num_detectors}.csv"
    mat_path = folder / f"scan-{count}-{num_detectors}.mat"

    def export_csv() -> None:
        buffer = io.StringIO()
        np.savetxt(
            buffer, payload.csv_data, delimiter=",",
            header=payload.csv_header, comments="", fmt="%.6f",
        )
        with csv_path.open("w", encoding="utf-8", newline="") as stream:
            stream.write(buffer.getvalue())

    def export_mat() -> None:
        savemat(str(mat_path), payload.mat_data, do_compression=True)

    operations: tuple[tuple[str, Callable[[], object]], ...] = (
        ("build_payload", build),
        ("csv_export", export_csv),
        ("mat_export", export_mat),
        ("csv_import", lambda: _verified_import(csv_path, measurement)),
        ("mat_import", lambda: _verified_import(mat_path, measurement)),
    )
    rows: list[dict[str, object]] = []
    for label, operation in operations:
        if label == "build_payload":
            stats = build_stats
        else:
            _, stats = _timed_on_qt_thread(app, operation)
        rows.append(
            {
                "points_per_scan": count,
                "detector_count": num_detectors,
                "operation": label,
                "qt_platform": app.platformName(),
                "window_system": sys.platform,
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
