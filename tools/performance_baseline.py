"""Capture and compare hardware-free IOPanel scan-overlay performance profiles.

This standard-library CLI delegates to the existing scan-overlay benchmark in
separate subprocesses. It never opens hardware; reports keep raw per-run data.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
REPORT_KIND = "iopanel-performance-baseline"
CASE_KEYS = (
    "points_per_scan",
    "detector_count",
    "overlay_count",
    "nonfinite_pattern",
    "finite_mode",
    "pen_width",
    "cosmetic",
    "antialias",
    "qt_platform",
    "window_system",
)
IO_CASE_KEYS = ("points_per_scan", "detector_count", "operation", "qt_platform", "window_system")
STARTUP_CASE_KEYS = (
    "scenario",
    "qt_platform",
    "window_system",
    "vmbpy_import_available",
    "matlab_engine_import_available",
)
STARTUP_METRICS = (
    "request_to_child_probe_s",
    "request_to_app_import_complete_s",
    "request_to_mainwindow_constructed_s",
    "request_to_show_return_s",
    "request_to_first_qt_callback_s",
    "request_to_first_paint_event_s",
    "request_to_post_paint_callback_s",
    "request_to_deferred_init_callback_s",
    "app_module_import_s",
    "probe_post_app_import_s",
    "qapplication_create_s",
    "qt_configure_s",
    "mainwindow_construct_s",
    "show_call_s",
    "entry_to_app_import_complete_s",
    "entry_to_mainwindow_constructed_s",
    "entry_to_show_return_s",
    "entry_to_first_qt_callback_s",
    "entry_to_first_paint_event_s",
    "entry_to_post_paint_callback_s",
    "entry_to_deferred_init_callback_s",
    "event_loop_enter_to_first_qt_callback_s",
    "event_loop_enter_to_post_paint_callback_s",
)
BENCHMARK_CASE_KEYS = {
    "scan_overlays": CASE_KEYS,
    "scan_io": IO_CASE_KEYS,
    "scan_gui": IO_CASE_KEYS,
    "startup": STARTUP_CASE_KEYS,
}
PACKAGES = ("PySide6", "numpy", "pyqtgraph", "scipy", "vmbpy", "matlabengine")


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5, check=True
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _lock_hash(root: Path) -> str | None:
    path = root / "uv.lock"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _environment(root: Path, qt_platform: str) -> dict[str, Any]:
    versions: dict[str, str | None] = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "commit": _git_commit(root),
        "lock_sha256": _lock_hash(root),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "cpu": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "qt_platform_requested": qt_platform,
        "package_versions": versions,
    }


def _case_key(row: dict[str, Any], case_keys: tuple[str, ...] = CASE_KEYS) -> tuple[Any, ...]:
    if any(key not in row for key in case_keys):
        raise ValueError("Benchmark case missing required context keys")
    return tuple(row[key] for key in case_keys)


def _percentile(values: list[float], percentile: float) -> float:
    """Nearest-rank percentile; defined for even a single repetition."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def summarize_runs(runs: list[dict[str, Any]], *, case_keys: tuple[str, ...] = CASE_KEYS) -> list[dict[str, Any]]:
    if not runs:
        raise ValueError("A baseline requires at least one recorded repetition")
    by_case: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    expected: set[tuple[Any, ...]] | None = None
    for run in runs:
        cases = run.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("Every repetition must contain nonempty case data")
        keys = [_case_key(row, case_keys) for row in cases]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate benchmark case within a repetition")
        if expected is None:
            expected = set(keys)
        elif set(keys) != expected:
            raise ValueError("Repetitions have different case matrices")
        for key, row in zip(keys, cases, strict=True):
            by_case.setdefault(key, []).append(row)

    summaries = []
    for key, records in sorted(by_case.items(), key=lambda item: repr(item[0])):
        available = set.intersection(*(set(record) for record in records))
        metrics: dict[str, Any] = {}
        for name in sorted(available - set(case_keys)):
            vals = [record[name] for record in records]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in vals):
                continue
            if not all(math.isfinite(v) for v in vals):
                raise ValueError(f"Nonfinite metric {name} in benchmark run")
            values = [float(v) for v in vals]
            metrics[name] = {
                "samples": values,
                "median": statistics.median(values),
                "p95": _percentile(values, 0.95),
                "min": min(values),
                "max": max(values),
            }
        summaries.append({"case": dict(zip(case_keys, key, strict=True)), "metrics": metrics})
    return summaries


def _capture_one(
    root: Path,
    output: Path,
    *,
    points: list[int],
    detectors: list[int],
    overlays: list[int],
    qt_platform: str,
    mouse_events: bool,
) -> tuple[float, list[dict[str, Any]]]:
    args = [
        sys.executable,
        "-m",
        "tools.benchmark_scan_overlays",
        "--points",
        *map(str, points),
        "--detectors",
        *map(str, detectors),
        "--overlay-counts",
        *map(str, overlays),
        "--nonfinite-pattern",
        "mixed",
        "--output",
        str(output),
    ]
    if mouse_events:
        args.append("--mouse-events")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = qt_platform
    start = time.perf_counter()
    result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - start
    if result.returncode != 0:
        raise RuntimeError(f"Overlay benchmark exited {result.returncode}: {result.stderr[-2500:]}")
    try:
        rows = json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Overlay benchmark did not produce a valid JSON report") from exc
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Overlay benchmark returned no valid cases")
    for row in rows:
        if row.get("qt_platform") != qt_platform:
            raise ValueError("Qt platform differs from the explicitly requested platform")
    return elapsed, rows


def capture(
    root: Path,
    *,
    points: list[int],
    detectors: list[int],
    overlays: list[int],
    repetitions: int,
    warmups: int,
    qt_platform: str,
    mouse_events: bool,
) -> dict[str, Any]:
    if repetitions < 1 or warmups < 0:
        raise ValueError("Require >=1 repetition and >=0 warmups")
    if not points or any(p < 2 for p in points) or not detectors or any(d not in (1, 4) for d in detectors):
        raise ValueError("Require points >=2 and detector counts of 1 or 4")
    if not overlays or any(o not in (0, 1, 2, 4, 8) for o in overlays):
        raise ValueError("Overlay counts must be 0, 1, 2, 4 or 8")
    if qt_platform not in ("offscreen", "windows"):
        raise ValueError("Only explicitly selected Qt offscreen or Windows platforms are supported")
    runs = []
    with tempfile.TemporaryDirectory(prefix="iopanel-perf-baseline-") as temp:
        for index in range(warmups + repetitions):
            elapsed, cases = _capture_one(
                root,
                Path(temp) / f"run-{index}.json",
                points=points,
                detectors=detectors,
                overlays=overlays,
                qt_platform=qt_platform,
                mouse_events=mouse_events,
            )
            if index >= warmups:
                runs.append({"repetition": index - warmups + 1, "subprocess_wall_s": elapsed, "cases": cases})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "benchmark": "scan_overlays",
        "recorded_utc": datetime.now(UTC).isoformat(),
        "evidence_scope": "synthetic Qt operation-level; NOT physical instrumentation or interactive frame rate",
        "environment": _environment(root, qt_platform),
        "configuration": {
            "points": points,
            "detectors": detectors,
            "overlays": overlays,
            "repetitions": repetitions,
            "warmup_subprocesses": warmups,
            "nonfinite_pattern": "mixed",
            "mouse_events": mouse_events,
        },
        "runs": runs,
        "statistics": summarize_runs(runs),
    }


def _capture_io_one(
    root: Path, output: Path, *, points: list[int], detectors: list[int], qt_platform: str
) -> tuple[float, list[dict[str, Any]]]:
    args = [
        sys.executable,
        "-m",
        "tools.benchmark_scan_io",
        "--points",
        *map(str, points),
        "--detectors",
        *map(str, detectors),
        "--output",
        str(output),
    ]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = qt_platform
    started = time.perf_counter()
    result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=240)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        raise RuntimeError(f"Scan I/O benchmark exited {result.returncode}: {result.stderr[-2500:]}")
    rows = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Scan I/O benchmark returned no valid cases")
    if any(row.get("qt_platform") != qt_platform for row in rows):
        raise ValueError("Qt platform differs from the explicitly requested platform")
    return elapsed, rows


def _capture_gui_one(
    root: Path, output: Path, *, points: list[int], detectors: list[int], qt_platform: str
) -> tuple[float, list[dict[str, Any]]]:
    """Invoke the real MainWindow and PlotWidget handlers with synthetic data."""
    args = [
        sys.executable,
        "-m",
        "tools.benchmark_scan_gui",
        "--points",
        *map(str, points),
        "--detectors",
        *map(str, detectors),
        "--output",
        str(output),
    ]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = qt_platform
    start = time.perf_counter()
    result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=240, check=False)
    elapsed = time.perf_counter() - start
    if result.returncode:
        raise RuntimeError(f"Real scan GUI benchmark exited {result.returncode}: {result.stderr[-3500:]}")
    rows = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows or not all(isinstance(item, dict) for item in rows):
        raise ValueError("Real scan GUI benchmark returned no valid cases")
    if any(item.get("qt_platform") != qt_platform for item in rows):
        raise ValueError("Qt platform differs from the explicitly requested platform")
    return elapsed, rows


def capture_io(
    root: Path,
    *,
    points: list[int],
    detectors: list[int],
    repetitions: int,
    warmups: int,
    qt_platform: str,
    benchmark: str = "scan_io",
) -> dict[str, Any]:
    if benchmark not in ("scan_io", "scan_gui"):
        raise ValueError("Unsupported scan file-I/O benchmark")
    if repetitions < 1 or warmups < 0:
        raise ValueError("Require >=1 repetition and >=0 warmups")
    if not points or any(p < 2 for p in points) or not detectors or any(d not in (1, 4) for d in detectors):
        raise ValueError("Require points >=2 and detector counts of 1 or 4")
    if qt_platform not in ("offscreen", "windows"):
        raise ValueError("Only explicitly selected Qt offscreen or Windows platforms are supported")
    runs = []
    capture_one = _capture_io_one if benchmark == "scan_io" else _capture_gui_one
    with tempfile.TemporaryDirectory(prefix="iopanel-scan-io-baseline-") as temp:
        for index in range(warmups + repetitions):
            elapsed, cases = capture_one(
                root,
                Path(temp) / f"run-{index}.json",
                points=points,
                detectors=detectors,
                qt_platform=qt_platform,
            )
            if index >= warmups:
                runs.append({"repetition": index - warmups + 1, "subprocess_wall_s": elapsed, "cases": cases})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "benchmark": benchmark,
        "recorded_utc": datetime.now(UTC).isoformat(),
        "evidence_scope": (
            "Actual MainWindow and PlotWidget synchronous handlers with synthetic scans; Qt pending callback proxy, no hardware"
            if benchmark == "scan_gui"
            else "synthetic QApplication queued-timer latency proxy; NOT actual GUI handler or hardware"
        ),
        "environment": _environment(root, qt_platform),
        "configuration": {
            "points": points,
            "detectors": detectors,
            "repetitions": repetitions,
            "warmup_subprocesses": warmups,
            "synthetic_backend": "synthetic.benchmark",
            "csv_format": "%.6f",
        },
        "runs": runs,
        "statistics": summarize_runs(runs, case_keys=IO_CASE_KEYS),
    }


def _validate_startup_case(row: dict[str, Any], *, subprocess_wall_s: float) -> None:
    """Fail closed on incomplete, inconsistent or misleading startup measurements."""
    if isinstance(subprocess_wall_s, bool) or not isinstance(subprocess_wall_s, (int, float)):
        raise TypeError("Startup subprocess wall time must be numeric")
    if not math.isfinite(subprocess_wall_s) or subprocess_wall_s <= 0:
        raise ValueError("Startup subprocess wall time must be positive and finite")
    for key in STARTUP_CASE_KEYS:
        if key not in row:
            raise ValueError(f"Startup case missing required context: {key}")
    for flag in ("vmbpy_import_available", "matlab_engine_import_available"):
        if not isinstance(row[flag], bool):
            raise TypeError(f"Startup optional SDK availability must be boolean: {flag}")
    for name in STARTUP_METRICS:
        value = row.get(name)
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise TypeError(f"Startup missing or nonnumeric timing: {name}")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Startup nonfinite or negative timing: {name}")

    def ordered(*keys: str) -> None:
        for first, next_key in pairwise(keys):
            if row[first] > row[next_key]:
                raise ValueError(f"Startup timeline out of order: {first} > {next_key}")

    ordered(
        "request_to_child_probe_s",
        "request_to_app_import_complete_s",
        "request_to_mainwindow_constructed_s",
        "request_to_show_return_s",
        "request_to_first_qt_callback_s",
    )
    ordered(
        "request_to_app_import_complete_s",
        "request_to_mainwindow_constructed_s",
        "request_to_first_paint_event_s",
        "request_to_post_paint_callback_s",
    )
    ordered("request_to_show_return_s", "request_to_deferred_init_callback_s")
    ordered("entry_to_first_paint_event_s", "entry_to_post_paint_callback_s")
    ordered("entry_to_app_import_complete_s", "entry_to_mainwindow_constructed_s", "entry_to_show_return_s")
    for name in STARTUP_METRICS:
        if name.startswith("request_to_") and row[name] > subprocess_wall_s:
            raise ValueError(f"Startup milestone exceeds subprocess wall time: {name}")
    # Both clocks are perf_counter() and originate from the same system clock.
    # Differences must agree with the parent-to-child probe offset.
    for suffix in (
        "app_import_complete",
        "mainwindow_constructed",
        "show_return",
        "first_qt_callback",
        "first_paint_event",
        "post_paint_callback",
        "deferred_init_callback",
    ):
        if abs(row[f"request_to_{suffix}_s"] - row[f"entry_to_{suffix}_s"] - row["request_to_child_probe_s"]) > 0.002:
            raise ValueError(f"Startup parent/child clock correlation mismatch: {suffix}")
    if abs(row["app_module_import_s"] - row["entry_to_app_import_complete_s"]) > 0.002:
        raise ValueError("Startup production app import interval does not match the timeline")


def _validate_startup_runs(report: dict[str, Any]) -> None:
    if report.get("benchmark") != "startup":
        return
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Startup report requires nonempty raw repetitions")
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("cases"), list) or len(run["cases"]) != 1:
            raise ValueError("Startup repetition requires exactly one case")
        case = run["cases"][0]
        if not isinstance(case, dict):
            raise TypeError("Startup case must be a mapping")
        wall_s = run.get("subprocess_wall_s")
        if isinstance(wall_s, bool) or not isinstance(wall_s, (float, int)):
            raise TypeError("Startup subprocess wall time must be numeric")
        _validate_startup_case(case, subprocess_wall_s=float(wall_s))


def _capture_startup_one(root: Path, output: Path, *, qt_platform: str) -> tuple[float, list[dict[str, Any]]]:
    """Measure a fresh Python interpreter constructing a MainWindow without device startup."""
    args = [sys.executable, "-m", "tools.benchmark_startup", "--output", str(output)]
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = qt_platform
    # Timestamp the launch request BEFORE Popen; Qt child returns milestones
    # on the same perf_counter clock. This is not an OS process-creation timestamp.
    started = time.perf_counter()
    args.extend(("--parent-started", repr(started)))
    result = subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=45)
    elapsed = time.perf_counter() - started
    if result.returncode:
        raise RuntimeError(f"Startup benchmark exited {result.returncode}: {result.stderr[-3500:]}")
    rows = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("Startup benchmark did not return exactly one case")
    if rows[0].get("qt_platform") != qt_platform:
        raise ValueError("Qt platform differs from the explicitly requested platform")
    if rows[0].get("scenario") != "main_window_deferred_hardware_suppressed":
        raise ValueError("Unexpected startup benchmark scenario")
    _validate_startup_case(rows[0], subprocess_wall_s=elapsed)
    return elapsed, rows


def capture_startup(root: Path, *, repetitions: int, warmups: int, qt_platform: str) -> dict[str, Any]:
    """Capture per-process startup stages using the existing versioned report protocol."""
    if repetitions < 1 or warmups < 0:
        raise ValueError("Require >=1 repetition and >=0 warmups")
    if qt_platform not in ("offscreen", "windows"):
        raise ValueError("Only explicitly selected Qt offscreen or Windows platforms are supported")
    runs = []
    with tempfile.TemporaryDirectory(prefix="iopanel-startup-baseline-") as temp:
        for index in range(warmups + repetitions):
            elapsed, cases = _capture_startup_one(root, Path(temp) / f"run-{index}.json", qt_platform=qt_platform)
            if len(cases) != 1:
                raise ValueError("Startup benchmark must contain one case")
            _validate_startup_case(cases[0], subprocess_wall_s=elapsed)
            if index >= warmups:
                runs.append({"repetition": index - warmups + 1, "subprocess_wall_s": elapsed, "cases": cases})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REPORT_KIND,
        "benchmark": "startup",
        "recorded_utc": datetime.now(UTC).isoformat(),
        "evidence_scope": (
            "fresh Python child process, real MainWindow constructed with deferred hardware initialization "
            "suppressed; Qt paint dispatch and queued callbacks, NOT physical hardware or a displayed frame"
        ),
        "environment": _environment(root, qt_platform),
        "configuration": {
            "repetitions": repetitions,
            "warmup_subprocesses": warmups,
            "scenario": "main_window_deferred_hardware_suppressed",
            "cameras": "disabled",
            "ct400_backend": "simulation",
        },
        "runs": runs,
        "statistics": summarize_runs(runs, case_keys=STARTUP_CASE_KEYS),
    }


def _load_report(path: Path) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("kind") != REPORT_KIND or report.get("schema_version") != 1:
        raise ValueError(f"Unsupported performance report: {path}")
    if not isinstance(report.get("runs"), list):
        raise TypeError("Report has no raw runs")
    benchmark = report.get("benchmark")
    if not isinstance(benchmark, str):
        raise TypeError("Missing performance benchmark kind")
    case_keys = BENCHMARK_CASE_KEYS.get(benchmark)
    if case_keys is None:
        raise ValueError("Unknown performance benchmark")
    _validate_startup_runs(report)
    if report.get("statistics") != summarize_runs(report["runs"], case_keys=case_keys):
        raise ValueError("Report statistics do not match preserved raw runs")
    return report


def compare_reports(
    base: dict[str, Any], candidate: dict[str, Any], *, allow_environment_mismatch: bool = False
) -> dict[str, Any]:
    if base.get("kind") != REPORT_KIND or candidate.get("kind") != REPORT_KIND:
        raise ValueError("Both inputs must be IOPanel baseline reports")
    if base.get("schema_version") != SCHEMA_VERSION or candidate.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Incompatible report schema")
    if base.get("benchmark") != candidate.get("benchmark") or base.get("configuration") != candidate.get(
        "configuration"
    ):
        raise ValueError("Benchmark and configuration must match exactly")
    _validate_startup_runs(base)
    _validate_startup_runs(candidate)
    # Git commit is intentionally excluded: comparing revisions is the purpose.
    before_env = {k: v for k, v in base["environment"].items() if k != "commit"}
    after_env = {k: v for k, v in candidate["environment"].items() if k != "commit"}
    mismatch = before_env != after_env
    if mismatch and not allow_environment_mismatch:
        raise ValueError("Environments differ; rerun on equivalent systems or explicitly allow a contextual comparison")
    case_keys = BENCHMARK_CASE_KEYS.get(base["benchmark"])
    if case_keys is None:
        raise ValueError("Unknown performance benchmark")
    baseline_rows = {tuple(item["case"].get(k) for k in case_keys): item for item in base["statistics"]}
    candidate_rows = {tuple(item["case"].get(k) for k in case_keys): item for item in candidate["statistics"]}
    if baseline_rows.keys() != candidate_rows.keys():
        raise ValueError("Case matrices differ")
    comparisons = []
    for case in sorted(baseline_rows, key=repr):
        first = baseline_rows[case]["metrics"]
        second = candidate_rows[case]["metrics"]
        if first.keys() != second.keys():
            raise ValueError("Metric sets differ between reports")
        metrics: dict[str, Any] = {}
        for name in sorted(first):
            previous = first[name]["median"]
            current = second[name]["median"]
            metrics[name] = {
                "baseline_median": previous,
                "candidate_median": current,
                "delta": current - previous,
                "relative_delta_percent": (current / previous - 1) * 100 if previous else None,
                "baseline_p95": first[name]["p95"],
                "candidate_p95": second[name]["p95"],
            }
        comparisons.append({"case": baseline_rows[case]["case"], "metrics": metrics})
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "iopanel-performance-comparison",
        "benchmark": base["benchmark"],
        "environment_mismatch": mismatch,
        "warning": "Cross-environment numbers are not attributable to code changes" if mismatch else None,
        "baseline_commit": base["environment"].get("commit"),
        "candidate_commit": candidate["environment"].get("commit"),
        "comparisons": comparisons,
    }


def _write_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("capture", help="run the existing hardware-free overlay benchmark")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--points", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    run.add_argument("--detectors", type=int, nargs="+", default=[1, 4])
    run.add_argument("--overlays", type=int, nargs="+", default=[0, 1, 2, 4, 8])
    run.add_argument("--repetitions", type=int, default=3)
    run.add_argument("--warmups", type=int, default=1)
    run.add_argument("--qt-platform", choices=("offscreen", "windows"), default="offscreen")
    run.add_argument("--mouse-events", action="store_true")
    io_run = commands.add_parser("capture-io", help="capture hardware-free schema-v2 import/export")
    io_run.add_argument("--output", type=Path, required=True)
    io_run.add_argument("--points", type=int, nargs="+", default=[10_000, 50_000, 100_000])
    io_run.add_argument("--detectors", type=int, nargs="+", default=[1, 4])
    io_run.add_argument("--repetitions", type=int, default=3)
    io_run.add_argument("--warmups", type=int, default=1)
    io_run.add_argument("--qt-platform", choices=("offscreen", "windows"), default="offscreen")
    gui_run = commands.add_parser("capture-gui", help="characterize real scan UI handlers using synthetic data")
    gui_run.add_argument("--output", type=Path, required=True)
    gui_run.add_argument("--points", type=int, nargs="+", default=[10_000, 50_000])
    gui_run.add_argument("--detectors", type=int, nargs="+", default=[1, 4])
    gui_run.add_argument("--repetitions", type=int, default=3)
    gui_run.add_argument("--warmups", type=int, default=1)
    gui_run.add_argument("--qt-platform", choices=("offscreen", "windows"), default="offscreen")
    startup_run = commands.add_parser("capture-startup", help="capture fresh-process Qt MainWindow startup")
    startup_run.add_argument("--output", type=Path, required=True)
    startup_run.add_argument("--repetitions", type=int, default=3)
    startup_run.add_argument("--warmups", type=int, default=0)
    startup_run.add_argument("--qt-platform", choices=("offscreen", "windows"), default="offscreen")
    cmp = commands.add_parser("compare", help="compare two versioned JSON baselines")
    cmp.add_argument("baseline", type=Path)
    cmp.add_argument("candidate", type=Path)
    cmp.add_argument("--output", type=Path)
    cmp.add_argument("--allow-environment-mismatch", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "capture":
        root = Path(__file__).resolve().parents[1]
        report = capture(
            root,
            points=args.points,
            detectors=args.detectors,
            overlays=args.overlays,
            repetitions=args.repetitions,
            warmups=args.warmups,
            qt_platform=args.qt_platform,
            mouse_events=args.mouse_events,
        )
        _write_new(args.output, report)
        print(f"Wrote {len(report['statistics'])} cases x {args.repetitions} repetitions to {args.output}")
    elif args.command in ("capture-io", "capture-gui"):
        root = Path(__file__).resolve().parents[1]
        report = capture_io(
            root,
            points=args.points,
            detectors=args.detectors,
            repetitions=args.repetitions,
            warmups=args.warmups,
            qt_platform=args.qt_platform,
            benchmark="scan_gui" if args.command == "capture-gui" else "scan_io",
        )
        _write_new(args.output, report)
        print(f"Wrote {len(report['statistics'])} I/O cases x {args.repetitions} repetitions to {args.output}")
    elif args.command == "capture-startup":
        root = Path(__file__).resolve().parents[1]
        report = capture_startup(root, repetitions=args.repetitions, warmups=args.warmups, qt_platform=args.qt_platform)
        _write_new(args.output, report)
        print(f"Wrote {len(report['statistics'])} startup cases x {args.repetitions} repetitions to {args.output}")
    else:
        result = compare_reports(
            _load_report(args.baseline),
            _load_report(args.candidate),
            allow_environment_mismatch=args.allow_environment_mismatch,
        )
        if args.output:
            _write_new(args.output, result)
        print(f"Environment mismatch: {result['environment_mismatch']}")
        for item in result["comparisons"]:
            context = item["case"]
            print(f"\nCase: {', '.join(f'{key}={value}' for key, value in context.items())}")
            for key, metric in item["metrics"].items():
                if (
                    key
                    in (
                        "cpu_time_s",
                        "working_set_mb",
                        "csv_parse_s",
                        "mat_parse_s",
                        "wall_s",
                        "cpu_s",
                        "qt_pending_callback_delay_s",
                        "peak_working_set_mb",
                    )
                    or key.endswith("_s")
                    or "repaint" in key
                    or "range" in key
                ):
                    delta = metric["relative_delta_percent"]
                    change = f"{delta:+.1f}%" if delta is not None else "relative N/A"
                    print(f"  {key}: {metric['baseline_median']:.4g} -> {metric['candidate_median']:.4g} ({change})")
        if result["warning"]:
            print(f"WARNING: {result['warning']}", file=sys.stderr)


if __name__ == "__main__":
    main()
