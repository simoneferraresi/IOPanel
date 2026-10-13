from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools import qt_stall_stress_supervisor as supervisor


def test_supervisor_bounds_cycle_and_deadline_inputs(tmp_path):
    with pytest.raises(ValueError, match="timeout_s"):
        supervisor.supervise(tmp_path / "bad-timeout", 361, 1, None)
    with pytest.raises(ValueError, match="cycles"):
        supervisor.supervise(tmp_path / "bad-cycles", 10, 0, None)


def test_process_identity_parser_accepts_powershell_singleton_arrays():
    identity = {"PID": 123, "CreationDate": "creation-time"}
    assert supervisor._parse_process_identity(json.dumps([identity])) == identity
    assert supervisor._parse_process_identity("[]") is None


def test_stack_capture_reports_unconfigured_or_missing_tool(monkeypatch, tmp_path):
    monkeypatch.delenv("PROCDUMP_PATH", raising=False)
    assert supervisor._capture_stack(123, {}, tmp_path)["status"] == "unavailable"

    monkeypatch.setenv("PROCDUMP_PATH", str(tmp_path / "missing-procdump.exe"))
    result = supervisor._capture_stack(123, {}, tmp_path)
    assert result["status"] == "unavailable"
    assert "does not exist" in result["reason"]


def test_stack_capture_checks_process_identity_before_using_tool(monkeypatch, tmp_path):
    tool = tmp_path / "procdump.exe"
    tool.write_bytes(b"test stub")
    monkeypatch.setenv("PROCDUMP_PATH", str(tool))
    monkeypatch.setattr(supervisor, "_process_identity", lambda _pid: {"CreationDate": "new-process"})

    result = supervisor._capture_stack(123, {"CreationDate": "original-process"}, tmp_path)

    assert result["status"] == "identity_mismatch"


def test_stack_capture_preserves_permission_denial(monkeypatch, tmp_path):
    tool = tmp_path / "procdump.exe"
    tool.write_bytes(b"test stub")
    monkeypatch.setenv("PROCDUMP_PATH", str(tool))
    identity = {"CreationDate": "same-process"}
    monkeypatch.setattr(supervisor, "_process_identity", lambda _pid: identity)
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 5, "", "Access is denied"),
    )

    result = supervisor._capture_stack(123, identity, tmp_path)

    assert result["status"] == "permission_denied"
    assert result["returncode"] == 5


def test_stack_capture_accepts_proc_dump_completion_marker_with_nonzero_exit(monkeypatch, tmp_path):
    tool = tmp_path / "procdump.exe"
    tool.write_bytes(b"test stub")
    monkeypatch.setenv("PROCDUMP_PATH", str(tool))
    identity = {"CreationDate": "same-process"}
    monkeypatch.setattr(supervisor, "_process_identity", lambda _pid: identity)

    def run_proc_dump(args, **kwargs):
        Path(args[-1]).write_bytes(b"triage dump")
        output = "[time] Dump 1 complete: 64 KB written\nDump count reached.".encode("utf-16-le")
        return subprocess.CompletedProcess(args, 1, output, b"")

    monkeypatch.setattr(supervisor.subprocess, "run", run_proc_dump)

    result = supervisor._capture_stack(123, identity, tmp_path)

    assert result["status"] == "captured"
    assert result["bytes"] == len(b"triage dump")
    assert "Dump 1 complete" in result["stdout"]


def test_stack_capture_rejects_identity_change_after_dump_command(monkeypatch, tmp_path):
    tool = tmp_path / "procdump.exe"
    tool.write_bytes(b"test stub")
    monkeypatch.setenv("PROCDUMP_PATH", str(tool))
    identities = iter(({"CreationDate": "same-process"}, {"CreationDate": "reused-pid"}))
    monkeypatch.setattr(supervisor, "_process_identity", lambda _pid: next(identities))
    monkeypatch.setattr(
        supervisor.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "dumped", ""),
    )

    result = supervisor._capture_stack(123, {"CreationDate": "same-process"}, tmp_path)

    assert result["status"] == "identity_changed_during_capture"
