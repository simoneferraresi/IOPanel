"""The Windows venv redirector must not hide or orphan its real interpreter."""

import ctypes
import os
import subprocess
import sys

import pytest

from tools import qt_stall_stress_supervisor as supervisor


@pytest.mark.skipif(os.name != "nt", reason="CPython Windows virtual environment redirector")
def test_terminate_process_tree_reaps_real_interpreter_child():
    inner = "import os,time; print(os.getpid(), flush=True); time.sleep(60)"
    script = f"import subprocess,sys; subprocess.run([sys.executable, '-c', {inner!r}])"
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=False)
    handle = None
    try:
        assert process.stdout is not None
        pid = int(process.stdout.readline().strip())
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.restype = ctypes.c_void_p
        api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        assert handle
        supervisor._terminate_process_tree(process)
        assert api.WaitForSingleObject(handle, 5000) == 0
    finally:
        if process.poll() is None:
            supervisor._terminate_process_tree(process)
        if handle:
            api.CloseHandle(handle)
        if process.stdout is not None:
            process.stdout.close()


def test_native_capture_resolves_only_matching_interpreter_descendant(monkeypatch):
    launcher = {"PID": 100, "CommandLine": "python -m pytest tests", "CreationDate": "2026-10-22T10:00:00Z"}
    native = {
        "PID": 101,
        "ParentPID": 100,
        "CommandLine": launcher["CommandLine"],
        "CreationDate": "2026-10-22T10:00:01Z",
        "ExecutablePath": sys._base_executable,
    }
    monkeypatch.setattr(supervisor.os, "name", "nt")
    monkeypatch.setattr(
        supervisor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, '[{"PID":101,"ParentPID":100}]')
    )
    monkeypatch.setattr(supervisor, "_process_identity", lambda pid: native)
    assert supervisor._native_pytest_identity(100, launcher) == (101, native)
    native["CommandLine"] = "unrelated process"
    assert supervisor._native_pytest_identity(100, launcher) == (100, launcher)
