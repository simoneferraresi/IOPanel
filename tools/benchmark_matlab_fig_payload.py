"""Driver-free before/after benchmark of FIG GUI-thread payload preparation.

Only measures payload construction, NOT real MATLAB conversion, worker
serialization, licensed MATLAB Engine usage, .fig output or GUI frame rate.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import tracemalloc
from pathlib import Path

import numpy as np

from hardware.ct400_types import Detector
from tools.benchmark_scan_io import _synthetic_scan
from ui.plot_widgets import build_matlab_fig_payload, build_prepared_matlab_fig_payload


def run(*, points: int, repetitions: int) -> dict[str, object]:
    if points < 2 or repetitions < 2:
        raise ValueError("Use at least two points and two repetitions")
    measurement = _synthetic_scan(
        points,
        (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4),
        include_nonfinite=True,
    )
    results: dict[str, dict[str, object]] = {}
    operations = {
        "json_gui_thread": lambda: build_matlab_fig_payload(measurement),
        "prepared_readonly_gui_thread": lambda: build_prepared_matlab_fig_payload(measurement),
    }
    for label, operation in operations.items():
        operation()  # warmup
        timings = []
        for _ in range(repetitions):
            started = time.perf_counter()
            payload = operation()
            timings.append(time.perf_counter() - started)
        tracemalloc.start()
        _, before_peak = tracemalloc.get_traced_memory()
        payload = operation()
        _, after_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        if label == "json_gui_thread":
            retained_bytes = len(payload.encode("utf-8"))
        else:
            if any(trace.values.flags.writeable for trace in payload.traces):
                raise AssertionError("Prepared arrays must be read-only")
            if not all(np.shares_memory(trace.values, measurement.detector_data) for trace in payload.traces):
                raise AssertionError("Prepared payload unexpectedly copied scan arrays")
            retained_bytes = 0  # no serialized numeric string is generated
        results[label] = {
            "samples_seconds": timings,
            "median_seconds": statistics.median(timings),
            "traced_peak_delta_bytes_approx": max(0, after_peak - before_peak),
            "serialized_numeric_string_bytes": retained_bytes,
        }
    return {
        "points": points,
        "detectors": 4,
        "repetitions": repetitions,
        "numpy_version": np.__version__,
        "scope": "GUI-thread payload preparation only; fake data, no MATLAB Engine",
        "measurements": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=100_000)
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(points=args.points, repetitions=args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for name, result in report["measurements"].items():
        print(f"{name}: {result['median_seconds']:.6f}s")


if __name__ == "__main__":
    main()
