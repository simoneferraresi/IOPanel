"""Driver-free regression tests for the *actual* scan GUI handler benchmark."""

import subprocess
import sys
from pathlib import Path

import pytest

from tools.performance_baseline import IO_CASE_KEYS, _load_report, capture_io, compare_reports, summarize_runs


def _case(operation: str, delay: float) -> dict:
    return {
        "points_per_scan": 10_000,
        "detector_count": 1,
        "operation": operation,
        "qt_platform": "offscreen",
        "window_system": "win32",
        "wall_s": delay,
        "qt_pending_callback_delay_s": delay + 0.00001,
    }


def test_scan_gui_statistics_and_comparisons():
    def report(commit: str, values: tuple[float, ...]) -> dict:
        runs = [
            {"repetition": i, "subprocess_wall_s": 2.0, "cases": [_case("import_csv_foreground", value)]}
            for i, value in enumerate(values, start=1)
        ]
        return {
            "kind": "iopanel-performance-baseline",
            "schema_version": 1,
            "benchmark": "scan_gui",
            "environment": {"commit": commit, "python": "3.12.10", "qt_platform_requested": "offscreen"},
            "configuration": {"points": [10_000], "detectors": [1], "repetitions": len(values)},
            "runs": runs,
            "statistics": summarize_runs(runs, case_keys=IO_CASE_KEYS),
        }

    before = report("a", (0.3, 0.4))
    after = report("b", (0.2, 0.3))
    comparison = compare_reports(before, after)
    assert comparison["benchmark"] == "scan_gui"
    metric = comparison["comparisons"][0]["metrics"]["wall_s"]
    assert metric["delta"] == pytest.approx(-0.1)
    with pytest.raises(ValueError, match="Benchmark and configuration"):
        compare_reports(before, {**after, "benchmark": "scan_io"})


def test_scan_gui_capture_warmup_and_validation(tmp_path: Path, monkeypatch):
    counts = []

    def fake_capture(*_args, **_kwargs):
        counts.append(1)
        return 1.0, [_case("export_csv", float(len(counts)))]

    monkeypatch.setattr("tools.performance_baseline._capture_gui_one", fake_capture)
    monkeypatch.setattr("tools.performance_baseline._environment", lambda *_args: {"commit": "test"})
    report = capture_io(
        tmp_path,
        points=[10_000],
        detectors=[1],
        repetitions=2,
        warmups=1,
        qt_platform="offscreen",
        benchmark="scan_gui",
    )
    assert counts == [1, 1, 1]
    assert report["benchmark"] == "scan_gui"
    assert report["statistics"][0]["metrics"]["wall_s"]["samples"] == [2.0, 3.0]
    with pytest.raises(ValueError, match="Unsupported"):
        capture_io(
            tmp_path,
            points=[10_000],
            detectors=[1],
            repetitions=1,
            warmups=0,
            qt_platform="offscreen",
            benchmark="bad",
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Validated Windows offscreen MainWindow characterization")
def test_real_mainwindow_gui_handlers_offscreen(tmp_path: Path):
    output = tmp_path / "actual-handler.json"
    command = [
        sys.executable,
        "-m",
        "tools.performance_baseline",
        "capture-gui",
        "--points",
        "10000",
        "--detectors",
        "1",
        "--repetitions",
        "1",
        "--warmups",
        "0",
        "--qt-platform",
        "offscreen",
        "--output",
        str(output),
    ]
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4500:]
    report = _load_report(output)
    assert report["benchmark"] == "scan_gui"
    assert len(report["statistics"]) == 4
    assert {row["case"]["operation"] for row in report["statistics"]} == {
        "import_csv_foreground",
        "import_mat_overlay",
        "export_csv",
        "export_mat",
    }
    for case in report["statistics"]:
        assert case["case"]["qt_platform"] == "offscreen"
        assert case["metrics"]["qt_pending_callback_delay_s"]["median"] >= 0
        assert case["metrics"]["file_bytes"]["median"] > 0
