"""Strict startup benchmark evidence, child failure and native Windows Qt smoke tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.performance_baseline import (
    STARTUP_CASE_KEYS,
    _capture_startup_one,
    _load_report,
    _validate_startup_case,
    _write_new,
    capture_startup,
    compare_reports,
    summarize_runs,
)


def _case(shift: float = 0.0) -> dict[str, object]:
    """Fully formed synthetic chronology; no actual latency is inferred."""
    parent_child = 0.010
    times = {
        "app_import_complete": 0.020 + shift,
        "mainwindow_constructed": 0.050 + shift,
        "show_return": 0.060 + shift,
        "first_qt_callback": 0.065 + shift,
        "first_paint_event": 0.067 + shift,
        "post_paint_callback": 0.075 + shift,
        "deferred_init_callback": 0.090 + shift,
    }
    return {
        "scenario": "main_window_deferred_hardware_suppressed",
        "qt_platform": "offscreen",
        "window_system": "win32",
        "vmbpy_import_available": False,
        "matlab_engine_import_available": False,
        "request_to_child_probe_s": parent_child,
        **{f"request_to_{name}_s": value for name, value in times.items()},
        **{f"entry_to_{name}_s": value - parent_child for name, value in times.items()},
        "app_module_import_s": times["app_import_complete"] - parent_child,
        "probe_post_app_import_s": 0.001,
        "qapplication_create_s": 0.003,
        "qt_configure_s": 0.002,
        "mainwindow_construct_s": 0.010,
        "show_call_s": 0.001,
        "event_loop_enter_to_first_qt_callback_s": 0.004,
        "event_loop_enter_to_post_paint_callback_s": 0.014,
    }


def _report(shift: float = 0.0) -> dict[str, object]:
    runs = [{"repetition": 1, "subprocess_wall_s": 0.2, "cases": [_case(shift)]}]
    return {
        "schema_version": 1,
        "kind": "iopanel-performance-baseline",
        "benchmark": "startup",
        "environment": {"commit": "head", "python": "3.12", "package_versions": {"vmbpy": None}},
        "configuration": {"scenario": "main_window_deferred_hardware_suppressed", "repetitions": 1},
        "runs": runs,
        "statistics": summarize_runs(runs, case_keys=STARTUP_CASE_KEYS),
    }


def test_startup_warmup_excluded_and_versioned_report_is_comparable(monkeypatch, tmp_path: Path):
    calls: list[int] = []

    def fake_child(_root, _output, *, qt_platform):
        assert qt_platform == "offscreen"
        calls.append(1)
        return 0.2, [_case(0.001 * len(calls))]

    monkeypatch.setattr("tools.performance_baseline._capture_startup_one", fake_child)
    monkeypatch.setattr("tools.performance_baseline._environment", lambda *_args: {"commit": "head"})
    report = capture_startup(tmp_path, repetitions=3, warmups=1, qt_platform="offscreen")
    assert len(calls) == 4
    assert report["benchmark"] == "startup"
    assert report["schema_version"] == 1
    assert len(report["runs"]) == 3
    assert report["statistics"] == summarize_runs(report["runs"], case_keys=STARTUP_CASE_KEYS)
    metric = report["statistics"][0]["metrics"]["entry_to_post_paint_callback_s"]
    assert metric["samples"] == pytest.approx([0.067, 0.068, 0.069])
    assert metric["median"] == pytest.approx(0.068)

    path = tmp_path / "baseline.json"
    _write_new(path, report)
    assert _load_report(path) == report
    with pytest.raises(FileExistsError):
        _write_new(path, report)

    candidate = json.loads(json.dumps(report))
    candidate["environment"]["commit"] = "next"
    candidate["runs"][0]["cases"][0]["entry_to_post_paint_callback_s"] += 0.001
    candidate["runs"][0]["cases"][0]["request_to_post_paint_callback_s"] += 0.001
    candidate["statistics"] = summarize_runs(candidate["runs"], case_keys=STARTUP_CASE_KEYS)
    assert compare_reports(report, candidate)["comparisons"][0]["metrics"]["entry_to_post_paint_callback_s"][
        "candidate_median"
    ] == pytest.approx(0.068)


@pytest.mark.parametrize(
    "repetitions,warmups,platform",
    [(0, 0, "offscreen"), (1, -1, "offscreen"), (1, 0, "linux")],
)
def test_startup_rejects_invalid_invocations_before_subprocess(tmp_path, repetitions, warmups, platform):
    with pytest.raises(ValueError):
        capture_startup(tmp_path, repetitions=repetitions, warmups=warmups, qt_platform=platform)


def test_startup_child_fail_closed_on_wrong_platform_or_process_failure(monkeypatch, tmp_path: Path):
    output = tmp_path / "child.json"

    def fake_run(args, **kwargs):
        assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
        assert "--parent-started" in args
        assert float(args[args.index("--parent-started") + 1]) > 0
        output.write_text(json.dumps([_case() | {"qt_platform": "windows"}]), encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="Qt platform differs"):
        _capture_startup_one(tmp_path, output, qt_platform="offscreen")
    output.unlink()

    def failed_run(_args, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="native startup failed")

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(RuntimeError, match="native startup failed"):
        _capture_startup_one(tmp_path, output, qt_platform="offscreen")


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda row: row.pop("entry_to_post_paint_callback_s"), "missing or nonnumeric timing"),
        (lambda row: row.update(entry_to_first_paint_event_s=float("nan")), "nonfinite or negative timing"),
        (lambda row: row.update(request_to_first_paint_event_s=-1.0), "nonfinite or negative timing"),
        (lambda row: row.update(vmbpy_import_available="yes"), "availability must be boolean"),
        (lambda row: row.update(request_to_post_paint_callback_s=0.055), "out of order"),
        (lambda row: row.update(entry_to_deferred_init_callback_s=0.001), "clock correlation mismatch"),
        (lambda row: row.update(request_to_deferred_init_callback_s=3.0), "subprocess wall time"),
    ],
)
def test_startup_rejects_missing_nonfinite_inconsistent_or_out_of_range_metrics(mutation, error):
    row = _case()
    mutation(row)
    expected_exception = TypeError if error in ("missing or nonnumeric timing", "availability must be boolean") else ValueError
    with pytest.raises(expected_exception, match=error):
        _validate_startup_case(row, subprocess_wall_s=0.2)


def test_raw_report_validation_catches_omission_even_with_recomputed_statistics(tmp_path: Path):
    report = _report()
    case = report["runs"][0]["cases"][0]
    case.pop("entry_to_first_qt_callback_s")
    report["statistics"] = summarize_runs(report["runs"], case_keys=STARTUP_CASE_KEYS)
    path = tmp_path / "forged.json"
    _write_new(path, report)
    with pytest.raises(TypeError, match="missing or nonnumeric timing"):
        _load_report(path)
    with pytest.raises(ValueError, match="missing or nonnumeric timing"):
        compare_reports(report, _report())


def test_sdk_availability_changes_block_unqualified_comparison():
    base, other = _report(), _report()
    other["runs"][0]["cases"][0]["vmbpy_import_available"] = True
    other["statistics"] = summarize_runs(other["runs"], case_keys=STARTUP_CASE_KEYS)
    with pytest.raises(ValueError, match="Case matrices differ"):
        compare_reports(base, other)


@pytest.mark.skipif(sys.platform != "win32", reason="Real Qt offscreen startup smoke belongs on Windows CI")
def test_windows_real_startup_capture_without_hardware(tmp_path: Path):
    """Exercise the actual MainWindow, with all deferred device entry points suppressed."""
    output = tmp_path / "startup.json"
    root = Path(__file__).resolve().parents[1]
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.performance_baseline",
            "capture-startup",
            "--output",
            str(output),
            "--repetitions",
            "2",
            "--warmups",
            "0",
            "--qt-platform",
            "offscreen",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-4000:]
    report = _load_report(output)
    assert len(report["runs"]) == 2
    assert report["configuration"]["ct400_backend"] == "simulation"
    assert report["configuration"]["cameras"] == "disabled"
    assert report["statistics"][0]["case"]["qt_platform"] == "offscreen"
    for repetition in report["runs"]:
        row = repetition["cases"][0]
        _validate_startup_case(row, subprocess_wall_s=repetition["subprocess_wall_s"])
        assert row["request_to_child_probe_s"] < row["request_to_app_import_complete_s"]
        assert row["entry_to_mainwindow_constructed_s"] < row["entry_to_first_qt_callback_s"]


@pytest.mark.skipif(sys.platform != "win32", reason="Qt failure-injection subprocess requires Windows CI")
@pytest.mark.parametrize(
    "record_paint,schedule_first_callback",
    [(False, True), (True, False)],
    ids=["missing-paint", "missing-first-callback"],
)
def test_windows_missing_qt_milestone_fails_closed(tmp_path: Path, record_paint, schedule_first_callback):
    output = tmp_path / "must-not-exist.json"
    script = (
        "import time\n"
        "from pathlib import Path\n"
        "from tools.benchmark_startup import characterize\n"
        f"characterize(Path({str(output)!r}), parent_started=time.perf_counter(), "
        f"watchdog_ms=250, record_paint={record_paint!r}, "
        f"schedule_first_callback={schedule_first_callback!r})\n"
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    run = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        check=False,
    )
    assert run.returncode != 0
    assert "Startup probe incomplete" in run.stderr
    assert not output.exists()
