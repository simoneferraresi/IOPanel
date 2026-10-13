"""Measure first and repeated Auto Alignment tab-selection overhead safely."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _single_sample(settings_path: Path) -> dict[str, float]:
    repository = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository))
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from app_settings import AppSettings
    from config_model import AppConfig, InstrumentsConfig
    from ui import main_window as main_window_module
    from ui import plot_widgets

    def suppress_hardware_initialization(self: main_window_module.MainWindow) -> None:
        del self

    main_window_module.MainWindow._begin_lazy_init = suppress_hardware_initialization
    plot_widgets.MATLAB_ENGINE_AVAILABLE = False
    app = QApplication.instance() or QApplication(sys.argv[:1])
    settings = AppSettings(QSettings(str(settings_path), QSettings.Format.IniFormat))
    settings.set_active_tab(0)
    window = main_window_module.MainWindow(
        AppConfig(instruments=InstrumentsConfig(ct400_backend="simulation")),
        settings=settings,
    )
    window.setUpdatesEnabled(False)
    window.show()

    started = time.perf_counter_ns()
    window.tab_widget.setCurrentIndex(2)
    deadline = time.monotonic() + 5
    while window.alignment_tab._plot3d_widget is None and time.monotonic() < deadline:
        app.processEvents()
    if window.alignment_tab._plot3d_widget is None:
        raise TimeoutError("Auto Alignment viewer was not constructed within five seconds")
    first_ms = (time.perf_counter_ns() - started) / 1_000_000

    window.tab_widget.setCurrentIndex(0)
    app.processEvents()
    started = time.perf_counter_ns()
    window.tab_widget.setCurrentIndex(2)
    app.processEvents()
    repeated_ms = (time.perf_counter_ns() - started) / 1_000_000
    reused = window.alignment_tab._plot3d_widget is not None

    window.close()
    app.processEvents()
    if not reused:
        raise AssertionError("repeated selection did not retain the original viewer")
    return {"first_viewer_ready_ms": first_ms, "repeated_selection_ms": repeated_ms}


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _summarize(rows: list[dict[str, float]], key: str) -> dict[str, Any]:
    values = [row[key] for row in rows]
    return {
        "median_ms": statistics.median(values),
        "p95_ms_nearest_rank": _percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
        "samples_ms": values,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--_single", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")

    if args._single:
        output = Path(os.environ["IOPANEL_VIEWER_BENCHMARK_SETTINGS"])
        print(json.dumps(_single_sample(output), sort_keys=True))
        return 0

    script = Path(__file__).resolve()
    rows: list[dict[str, float]] = []
    with tempfile.TemporaryDirectory(prefix="iopanel-viewer-selection-") as temp_dir:
        settings_path = Path(temp_dir) / "settings.ini"
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "windows"
        env["IOPANEL_VIEWER_BENCHMARK_SETTINGS"] = str(settings_path)
        for _ in range(args.samples):
            result = subprocess.run(
                [sys.executable, str(script), "--_single"],
                cwd=script.parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(
                    f"benchmark child failed ({result.returncode}): {result.stdout[-1000:]} {result.stderr[-2000:]}"
                )
            rows.append(json.loads(result.stdout.strip().splitlines()[-1]))

    report = {
        "platform": sys.platform,
        "python": sys.version.replace("\n", " "),
        "qpa_platform": "windows",
        "samples": args.samples,
        "hardware_initialization": "suppressed before MainWindow initialization",
        "matlab_engine": "disabled in each child process",
        "paint_updates": "disabled; timing ends when viewer object is ready, not when a frame is presented",
        "first_viewer_ready": _summarize(rows, "first_viewer_ready_ms"),
        "repeated_selection": _summarize(rows, "repeated_selection_ms"),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
