"""Regression coverage for the Windows supervisor's optional CIM identity probe."""

from __future__ import annotations

import json
import subprocess
import sys

from tools import qt_stall_stress_supervisor as supervisor


def _run_script(monkeypatch, tmp_path, script: str, *, timeout_s: int = 10):
    monkeypatch.setattr(
        supervisor,
        "_pytest_command",
        lambda suite, *, suite_group=None: [sys.executable, "-c", script],
    )
    out = tmp_path / "diagnostics"
    status = supervisor.supervise(out, timeout_s, 1, None, suite=True)
    return status, json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def _identity_timeout(_pid: int):
    raise subprocess.TimeoutExpired(["powershell.exe", "Get-CimInstance"], timeout=15)


def test_optional_identity_probe_timeout_does_not_abort_successful_pytest(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "_process_identity", _identity_timeout)
    status, manifest = _run_script(monkeypatch, tmp_path, "print('pytest succeeded')")
    assert status == 0
    assert manifest["returncode"] == 0
    assert manifest["timed_out"] is False
    assert manifest["child_identity"] is None
    assert manifest["child_identity_probe_error"] == "timeout after 15s"
    assert manifest["stack_capture"]["status"] == "not_needed"
    assert "supervisor_error" not in manifest


def test_missing_identity_blocks_native_dump_but_still_times_out_child(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "_process_identity", _identity_timeout)
    monkeypatch.setattr(
        supervisor,
        "_capture_stack",
        lambda *args: (_ for _ in ()).throw(AssertionError("unverified PID must not be dumped")),
    )
    status, manifest = _run_script(monkeypatch, tmp_path, "import time; time.sleep(30)", timeout_s=1)
    assert status == 124
    assert manifest["timed_out"] is True
    assert manifest["stack_capture"]["status"] == "identity_unverified"
    assert "supervisor_error" not in manifest


def test_identity_mismatch_still_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        supervisor,
        "_process_identity",
        lambda pid: {"ExecutablePath": "C:/unrelated.exe", "CommandLine": "pytest"},
    )
    status, manifest = _run_script(monkeypatch, tmp_path, "import time; time.sleep(30)")
    assert status == 2
    assert "pytest process identity did not match" in manifest["supervisor_error"]


def test_dump_identity_timeout_preserves_true_hang_result(monkeypatch, tmp_path):
    monkeypatch.setattr(
        supervisor,
        "_process_identity",
        lambda pid: {
            "ExecutablePath": sys.executable,
            "CommandLine": "python -m pytest tests",
            "CreationDate": "2026-10-21T16:00:00Z",
        },
    )

    def dump_timeout(*args):
        raise subprocess.TimeoutExpired(["powershell.exe", "Get-CimInstance"], timeout=15)

    monkeypatch.setattr(supervisor, "_capture_stack", dump_timeout)
    status, manifest = _run_script(monkeypatch, tmp_path, "import time; time.sleep(30)", timeout_s=1)
    assert status == 124
    assert manifest["timed_out"] is True
    assert manifest["stack_capture"]["status"] == "identity_probe_timeout"
    assert "supervisor_error" not in manifest
