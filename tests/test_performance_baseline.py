"""Pure, driver-free checks for the versioned performance-reporting protocol."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.performance_baseline import (
    CASE_KEYS,
    _load_report,
    _write_new,
    capture,
    compare_reports,
    summarize_runs,
)


def _case(repaint: float = 5.0, points: int = 10_000):
    row = dict.fromkeys(CASE_KEYS)
    row.update(
        points_per_scan=points,
        detector_count=1,
        overlay_count=0,
        nonfinite_pattern="mixed",
        finite_mode="safe-auto",
        pen_width=1.0,
        cosmetic=True,
        antialias=False,
        qt_platform="offscreen",
        window_system="win32",
        repaint_ms=repaint,
        cpu_time_s=1.0,
        working_set_mb=120.0,
    )
    return row


def _report(repaints=(3.0, 5.0, 9.0), commit="aaa", qt="offscreen"):
    runs = [
        {"repetition": i + 1, "subprocess_wall_s": 2.0, "cases": [_case(value)]} for i, value in enumerate(repaints)
    ]
    return {
        "schema_version": 1,
        "kind": "iopanel-performance-baseline",
        "benchmark": "scan_overlays",
        "environment": {"commit": commit, "qt_platform_requested": qt, "python": "3.12.8", "lock_sha256": "same"},
        "configuration": {"points": [10000], "repetitions": len(repaints)},
        "runs": runs,
        "statistics": summarize_runs(runs),
    }


def test_summary_preserves_raw_samples_and_p95():
    report = _report()
    metric = report["statistics"][0]["metrics"]["repaint_ms"]
    assert metric == {"samples": [3.0, 5.0, 9.0], "median": 5.0, "p95": 9.0, "min": 3.0, "max": 9.0}
    assert "cosmetic" not in report["statistics"][0]["metrics"]
    assert report["runs"][0]["cases"][0]["repaint_ms"] == 3.0


def test_compare_reports_supports_different_revisions():
    before = _report((3.0, 5.0, 9.0), "aaa")
    after = _report((2.0, 4.0, 6.0), "bbb")
    comparison = compare_reports(before, after)
    metric = comparison["comparisons"][0]["metrics"]["repaint_ms"]
    assert comparison["environment_mismatch"] is False
    assert comparison["baseline_commit"] == "aaa"
    assert metric["delta"] == -1.0
    assert metric["relative_delta_percent"] == pytest.approx(-20.0)
    assert metric["baseline_p95"] == 9.0
    assert metric["candidate_p95"] == 6.0


def test_zero_baseline_yields_unavailable_relative_delta():
    a, b = _report((0.0,)), _report((2.0,), "bbb")
    result = compare_reports(a, b)
    assert result["comparisons"][0]["metrics"]["repaint_ms"]["relative_delta_percent"] is None


def test_environment_guard_cannot_silently_compare_offscreen_to_native():
    before, after = _report(), _report(commit="bbb", qt="windows")
    with pytest.raises(ValueError, match="Environments differ"):
        compare_reports(before, after)
    result = compare_reports(before, after, allow_environment_mismatch=True)
    assert result["environment_mismatch"] is True
    assert result["warning"]


def test_configuration_and_matrix_mismatches_are_rejected():
    before, after = _report(), _report(commit="bbb")
    after["configuration"]["points"] = [50000]
    with pytest.raises(ValueError, match="configuration"):
        compare_reports(before, after)
    after = _report(commit="bbb")
    after["statistics"][0]["case"]["points_per_scan"] = 50000
    with pytest.raises(ValueError, match="Case matrices"):
        compare_reports(before, after)


def test_reject_missing_or_duplicate_cases_and_nonfinite_metrics():
    with pytest.raises(ValueError, match="different case matrices"):
        summarize_runs([{"cases": [_case()]}, {"cases": [_case(points=50000)]}])
    with pytest.raises(ValueError, match="Duplicate"):
        summarize_runs([{"cases": [_case(), _case()]}])
    with pytest.raises(ValueError, match="Nonfinite"):
        summarize_runs([{"cases": [_case(float("nan"))]}])
    with pytest.raises(ValueError, match="context keys"):
        summarize_runs([{"cases": [{"repaint_ms": 1.0}]}])


def test_reports_are_not_overwritten_and_raw_integrity_is_checked(tmp_path: Path):
    path = tmp_path / "baseline.json"
    report = _report()
    _write_new(path, report)
    assert _load_report(path) == report
    with pytest.raises(FileExistsError):
        _write_new(path, report)
    report["runs"][0]["cases"][0]["repaint_ms"] = 9999.0
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match"):
        _load_report(path)


def test_capture_keeps_warmups_out_of_recorded_samples(monkeypatch, tmp_path: Path):
    calls = []

    def fake_capture(*_args, **_kwargs):
        calls.append(1)
        return 0.01, [_case(float(len(calls)))]

    monkeypatch.setattr("tools.performance_baseline._capture_one", fake_capture)
    monkeypatch.setattr("tools.performance_baseline._environment", lambda *_args: {"commit": "abc"})
    result = capture(
        tmp_path,
        points=[10000],
        detectors=[1],
        overlays=[0],
        repetitions=3,
        warmups=1,
        qt_platform="offscreen",
        mouse_events=False,
    )
    assert len(calls) == 4
    assert len(result["runs"]) == 3
    assert result["statistics"][0]["metrics"]["repaint_ms"]["samples"] == [2.0, 3.0, 4.0]
    assert "physical" in result["evidence_scope"]


@pytest.mark.parametrize("points,detectors,overlays", [([1], [1], [0]), ([10000], [2], [0]), ([10000], [1], [99])])
def test_capture_rejects_invalid_matrix_before_invocation(tmp_path, points, detectors, overlays):
    with pytest.raises(ValueError):
        capture(
            tmp_path,
            points=points,
            detectors=detectors,
            overlays=overlays,
            repetitions=1,
            warmups=0,
            qt_platform="offscreen",
            mouse_events=False,
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Qualified Windows Qt offscreen benchmark smoke")
def test_windows_offscreen_capture_subprocess(tmp_path: Path):
    """Exercise actual synthetic Qt benchmark capture, not just mocked subprocesses."""
    output = tmp_path / "offscreen-smoke.json"
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "tools.performance_baseline",
        "capture",
        "--points",
        "10000",
        "--detectors",
        "1",
        "--overlays",
        "0",
        "4",
        "--repetitions",
        "3",
        "--warmups",
        "0",
        "--qt-platform",
        "offscreen",
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=180, check=False)
    assert completed.returncode == 0, completed.stderr[-4000:]
    report = _load_report(output)
    assert report["configuration"]["repetitions"] == 3
    assert report["environment"]["qt_platform_requested"] == "offscreen"
    assert len(report["runs"]) == 3
    assert {row["case"]["overlay_count"] for row in report["statistics"]} == {0, 4}
    for case in report["statistics"]:
        assert case["case"]["qt_platform"] == "offscreen"
        assert case["metrics"]
        for metric in case["metrics"].values():
            assert len(metric["samples"]) == 3
            assert metric["min"] <= metric["median"] <= metric["max"]
