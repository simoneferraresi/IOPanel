"""Hardware-free scan-I/O baseline schema and Windows subprocess regression."""

import subprocess
import sys
from pathlib import Path

import pytest

from tools.performance_baseline import (
    IO_CASE_KEYS,
    _load_report,
    _write_new,
    capture_io,
    compare_reports,
    summarize_runs,
)


def _io_case(operation: str, wall_s: float) -> dict:
    return {
        "points_per_scan": 10_000,
        "detector_count": 1,
        "operation": operation,
        "qt_platform": "offscreen",
        "window_system": "win32",
        "wall_s": wall_s,
        "cpu_s": wall_s / 2,
        "qt_pending_callback_delay_s": wall_s + 0.0001,
    }


def _io_report(commit: str, durations: tuple[float, ...]) -> dict:
    runs = [
        {"repetition": index, "subprocess_wall_s": 1.0, "cases": [_io_case("csv_import", value)]}
        for index, value in enumerate(durations, start=1)
    ]
    return {
        "schema_version": 1,
        "kind": "iopanel-performance-baseline",
        "benchmark": "scan_io",
        "environment": {"commit": commit, "python": "3.12.10", "qt_platform_requested": "offscreen"},
        "configuration": {"points": [10_000], "detectors": [1], "repetitions": len(durations)},
        "runs": runs,
        "statistics": summarize_runs(runs, case_keys=IO_CASE_KEYS),
    }


def test_io_statistics_and_compare_keep_separate_case_keys():
    before = _io_report("abc", (0.2, 0.3, 0.4))
    after = _io_report("def", (0.1, 0.2, 0.3))
    comparison = compare_reports(before, after)
    assert comparison["benchmark"] == "scan_io"
    case = comparison["comparisons"][0]
    assert case["case"]["operation"] == "csv_import"
    assert case["metrics"]["wall_s"]["baseline_median"] == pytest.approx(0.3)
    assert case["metrics"]["wall_s"]["candidate_median"] == pytest.approx(0.2)


def test_io_report_raw_data_integrity_and_strict_environment(tmp_path: Path):
    baseline = _io_report("abc", (0.2, 0.3))
    path = tmp_path / "io.json"
    _write_new(path, baseline)
    assert _load_report(path) == baseline
    with pytest.raises(FileExistsError):
        _write_new(path, baseline)
    baseline["runs"][0]["cases"][0]["wall_s"] = 999.0
    path.write_text(__import__("json").dumps(baseline), encoding="utf-8")
    with pytest.raises(ValueError, match="statistics"):
        _load_report(path)

    other = _io_report("def", (0.1, 0.2))
    other["environment"]["python"] = "3.13.0"
    with pytest.raises(ValueError, match="Environments differ"):
        compare_reports(_io_report("abc", (0.2, 0.3)), other)


def test_capture_io_warmups_and_validation(monkeypatch, tmp_path: Path):
    calls: list[int] = []

    def fake_capture(*_args, **_kwargs):
        calls.append(1)
        return 0.1, [_io_case("csv_import", float(len(calls)))]

    monkeypatch.setattr("tools.performance_baseline._capture_io_one", fake_capture)
    monkeypatch.setattr("tools.performance_baseline._environment", lambda *_args: {"commit": "abc"})
    captured = capture_io(
        tmp_path, points=[10_000], detectors=[1], warmups=1, repetitions=2, qt_platform="offscreen"
    )
    assert len(calls) == 3
    assert captured["benchmark"] == "scan_io"
    assert captured["statistics"][0]["metrics"]["wall_s"]["samples"] == [2.0, 3.0]
    for invalid in ({"points": [1]}, {"detectors": [2]}, {"repetitions": 0}):
        options = {"points": [10_000], "detectors": [1], "repetitions": 2, "warmups": 0, "qt_platform": "offscreen"}
        options.update(invalid)
        with pytest.raises(ValueError):
            capture_io(tmp_path, **options)


@pytest.mark.skipif(sys.platform != "win32", reason="Qualified Windows Qt offscreen benchmark smoke")
def test_scan_io_windows_offscreen_subprocess(tmp_path: Path):
    """Exercise schema-v2 CSV/MAT export/import + pending Qt callback on Windows."""
    target = tmp_path / "scan-io-baseline.json"
    command = [
        sys.executable, "-m", "tools.performance_baseline", "capture-io",
        "--points", "10000", "--detectors", "1",
        "--repetitions", "2", "--warmups", "0",
        "--qt-platform", "offscreen", "--output", str(target),
    ]
    completed = subprocess.run(
        command, cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = _load_report(target)
    assert len(result["runs"]) == 2
    assert len(result["statistics"]) == 5
    assert {item["case"]["operation"] for item in result["statistics"]} == {
        "build_payload", "csv_export", "mat_export", "csv_import", "mat_import",
    }
    assert all("qt_pending_callback_delay_s" in item["metrics"] for item in result["statistics"])
    assert all(item["case"]["qt_platform"] == "offscreen" for item in result["statistics"])
