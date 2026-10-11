"""Issue #152: startup capture/report protocol and Windows Qt-offscreen smoke."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.performance_baseline import (
    STARTUP_CASE_KEYS,
    _capture_startup_one,
    _load_report,
    _write_new,
    capture_startup,
    compare_reports,
    summarize_runs,
)


def _case(value: float) -> dict[str, object]:
    return {
        "scenario": "main_window_deferred_hardware_suppressed",
        "qt_platform": "offscreen",
        "window_system": "win32",
        "entry_to_post_paint_callback_s": value,
        "mainwindow_import_s": value / 2,
    }


def test_startup_warmup_excluded_and_versioned_report_is_comparable(monkeypatch, tmp_path: Path):
    calls: list[int] = []

    def fake_child(_root, _output, *, qt_platform):
        assert qt_platform == "offscreen"
        calls.append(1)
        return 0.1, [_case(float(len(calls)))]

    monkeypatch.setattr("tools.performance_baseline._capture_startup_one", fake_child)
    monkeypatch.setattr("tools.performance_baseline._environment", lambda *_args: {"commit": "head"})
    report = capture_startup(tmp_path, repetitions=3, warmups=1, qt_platform="offscreen")
    assert len(calls) == 4
    assert report["benchmark"] == "startup"
    assert report["schema_version"] == 1
    assert len(report["runs"]) == 3
    assert report["runs"][0]["cases"][0]["entry_to_post_paint_callback_s"] == 2
    assert report["statistics"] == summarize_runs(report["runs"], case_keys=STARTUP_CASE_KEYS)
    metric = report["statistics"][0]["metrics"]["entry_to_post_paint_callback_s"]
    assert metric["samples"] == [2.0, 3.0, 4.0]
    assert metric["median"] == 3.0

    path = tmp_path / "baseline.json"
    _write_new(path, report)
    assert _load_report(path) == report
    with pytest.raises(FileExistsError):
        _write_new(path, report)

    candidate = json.loads(json.dumps(report))
    candidate["environment"]["commit"] = "next"
    candidate["runs"][0]["cases"][0]["entry_to_post_paint_callback_s"] = 1.5
    candidate["statistics"] = summarize_runs(candidate["runs"], case_keys=STARTUP_CASE_KEYS)
    assert compare_reports(report, candidate)["comparisons"][0]["metrics"]["entry_to_post_paint_callback_s"][
        "candidate_median"
    ] == 3.0


@pytest.mark.parametrize(
    "repetitions,warmups,platform",
    [(0, 0, "offscreen"), (1, -1, "offscreen"), (1, 0, "linux")],
)
def test_startup_rejects_invalid_invocations_before_subprocess(tmp_path, repetitions, warmups, platform):
    with pytest.raises(ValueError):
        capture_startup(tmp_path, repetitions=repetitions, warmups=warmups, qt_platform=platform)


def test_startup_child_fail_closed_on_wrong_platform_or_missing_data(monkeypatch, tmp_path: Path):
    output = tmp_path / "child.json"

    def fake_run(_args, **kwargs):
        assert kwargs["env"]["QT_QPA_PLATFORM"] == "offscreen"
        output.write_text(json.dumps([_case(0.2) | {"qt_platform": "windows"}]), encoding="utf-8")
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


@pytest.mark.skipif(sys.platform != "win32", reason="Real Qt offscreen startup smoke belongs on Windows CI")
def test_windows_real_startup_capture_without_hardware(tmp_path: Path):
    """Exercise production MainWindow layout but suppress all deferred hardware init."""
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
        case = repetition["cases"][0]
        assert case["entry_to_first_paint_event_s"] <= case["entry_to_post_paint_callback_s"]
        assert case["entry_to_mainwindow_constructed_s"] <= case["entry_to_show_return_s"]
        assert case["entry_to_first_qt_callback_s"] > 0
        assert case["entry_to_deferred_init_callback_s"] > 0
