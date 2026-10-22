"""Windows child-process supervisor for Qt stress and normal pytest CI."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

PROCESS_QUERY = r"""
$ErrorActionPreference = 'Stop'
$target = [int]$env:IOPANEL_QT_TARGET_PID
$p = Get-CimInstance Win32_Process -Filter "ProcessId = $target"
if ($null -eq $p) { '[]'; exit 0 }
$owner = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue
[pscustomobject]@{Name=$p.Name; PID=[int]$p.ProcessId; ParentPID=[int]$p.ParentProcessId; CreationDate=$p.CreationDate.ToUniversalTime().ToString('o'); ExecutablePath=$p.ExecutablePath; CommandLine=$p.CommandLine; Owner=if($owner.ReturnValue -eq 0){"$($owner.Domain)\$($owner.User)"}else{"UNVERIFIED"}} | ConvertTo-Json -Compress
"""

CHILDREN_QUERY = r"""
$ErrorActionPreference = 'Stop'
$target = [int]$env:IOPANEL_QT_TARGET_PID
@(Get-CimInstance Win32_Process -Filter "ParentProcessId = $target" | ForEach-Object {
    [pscustomobject]@{PID=[int]$_.ProcessId; ParentPID=[int]$_.ParentProcessId}
}) | ConvertTo-Json -Compress
"""


def _windows_command_arguments(command: str) -> list[str]:
    """Use Windows quoting rules; venv forwarding replaces only argv[0]."""
    if not command:
        return []
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(argv)


def _native_pytest_identity(pid: int, launcher: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Resolve CPython's Windows venv redirector without trusting arbitrary PIDs."""
    if os.name != "nt" or "PID" not in launcher:
        return pid, launcher
    env = os.environ.copy()
    env["IOPANEL_QT_TARGET_PID"] = str(pid)
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", CHILDREN_QUERY],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=True,
    )
    children = json.loads(result.stdout.strip() or "[]")
    if isinstance(children, dict):
        children = [children]
    candidates = []
    for child in children:
        if child.get("ParentPID") != pid:
            continue
        identity = _process_identity(int(child["PID"]))
        if identity is None:
            continue
        if (
            identity.get("ParentPID") == pid
            and str(identity.get("ExecutablePath", "")).casefold() == sys._base_executable.casefold()
            and _windows_command_arguments(str(identity.get("CommandLine") or ""))[1:]
            == _windows_command_arguments(str(launcher.get("CommandLine") or ""))[1:]
            and identity.get("CreationDate", "") >= launcher.get("CreationDate", "")
        ):
            candidates.append((int(child["PID"]), identity))
    if len(candidates) > 1:
        raise RuntimeError("Ambiguous native pytest child; refusing native capture")
    return candidates[0] if candidates else (pid, launcher)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """A venv launcher is not the interpreter: reap its descendants as well."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode and process.poll() is None:
            raise RuntimeError(f"Failed to terminate owned pytest process tree: {result.returncode}")
    else:
        process.terminate()
    process.wait(timeout=10)


def _process_identity(pid: int) -> dict[str, Any] | None:
    env = os.environ.copy()
    env["IOPANEL_QT_TARGET_PID"] = str(pid)
    completed = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", PROCESS_QUERY],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"process identity query failed: {completed.stderr.strip()}")
    return _parse_process_identity(completed.stdout)


def _parse_process_identity(output: str) -> dict[str, Any] | None:
    data = output.strip()
    if not data:
        return None
    parsed = json.loads(data)
    if isinstance(parsed, list):
        if not parsed:
            return None
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            return parsed[0]
        raise RuntimeError(f"process identity query returned an unexpected result: {parsed!r}")
    if not isinstance(parsed, dict):
        raise TypeError(f"process identity query returned an unexpected result: {parsed!r}")
    return parsed


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _tail(path: Path, offset: int) -> tuple[int, list[str]]:
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        stream.seek(offset)
        lines = stream.readlines()
        return stream.tell(), [line.rstrip("\r\n") for line in lines]


def _decode_tool_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    encoding = "utf-16-le" if len(value) > 1 and value[1::2].count(b"\x00") > len(value) // 4 else "utf-8"
    return value.decode(encoding, errors="replace").replace("\x00", "")


def _capture_stack(pid: int, identity: dict[str, Any], output: Path) -> dict[str, Any]:
    tool = os.environ.get("PROCDUMP_PATH")
    if not tool:
        return {"status": "unavailable", "reason": "PROCDUMP_PATH was not configured"}
    executable = Path(tool)
    if not executable.is_file():
        return {"status": "unavailable", "reason": f"configured ProcDump path does not exist: {tool}"}
    current = _process_identity(pid)
    if current is None or current.get("CreationDate") != identity.get("CreationDate"):
        return {"status": "identity_mismatch", "before": identity, "current": current}
    dump_path = output / f"pytest-{pid}-hang.dmp"
    try:
        result = subprocess.run(
            [str(executable), "-accepteula", "-mt", str(pid), str(dump_path)],
            capture_output=True,
            text=False,
            timeout=90,
            check=False,
        )
        current_after = _process_identity(pid)
        if current_after is None or current_after.get("CreationDate") != identity.get("CreationDate"):
            return {"status": "identity_changed_during_capture", "returncode": result.returncode}
        stdout = _decode_tool_output(result.stdout)
        stderr = _decode_tool_output(result.stderr)
        combined = (stdout + "\n" + stderr).strip()
        lower_output = combined.casefold()
        if result.returncode and any(token in lower_output for token in ("access is denied", "denied", "error 5")):
            status = "permission_denied"
        elif dump_path.is_file() and dump_path.stat().st_size > 0 and re.search(r"dump\s+\d+\s+complete", lower_output):
            status = "captured"
        else:
            status = "failed"
        return {
            "status": status,
            "returncode": result.returncode,
            "path": str(dump_path) if dump_path.is_file() else None,
            "bytes": dump_path.stat().st_size if dump_path.is_file() else 0,
            "stdout": stdout[-4000:],
            "stderr": stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "capture_timeout",
            "stdout": _decode_tool_output(exc.stdout)[-2000:],
            "stderr": _decode_tool_output(exc.stderr)[-2000:],
        }
    except OSError as exc:
        return {"status": "tool_error", "error": repr(exc)}


def _pytest_command(suite: bool, *, suite_group: str | None = None) -> list[str]:
    command = [sys.executable, "-u", "-m", "pytest", "-vv", "--tb=short", "-o", "faulthandler_timeout=30"]
    if suite:
        if suite_group is not None:
            from tools.ci_pytest_partition import paths_for_group

            return [*command, *paths_for_group(suite_group)]
        return [*command, "tests"]
    if suite_group is not None:
        raise ValueError("A pytest partition requires --suite")
    return [*command, "-s", "tests/test_qt_stall_stress.py"]


def supervise(
    output: Path,
    timeout_s: int,
    cycles: int,
    procdump_path: str | None,
    *,
    suite: bool = False,
    suite_group: str | None = None,
) -> int:
    if not 1 <= timeout_s <= 360:
        raise ValueError("timeout_s must be between 1 and 360 seconds")
    if not 1 <= cycles <= 1000:
        raise ValueError("cycles must be between 1 and 1000")
    if suite and cycles != 1:
        raise ValueError("suite mode must use exactly one run (cycles=1)")
    if suite_group is not None and not suite:
        raise ValueError("A pytest partition requires --suite")
    output.mkdir(parents=True, exist_ok=True)
    config_path = output / "simulation.ini"
    config_path.write_text(
        """[App]\nname = Qt stress diagnostic\n\n[Instruments]\nct400_backend = simulation\nct400_dll_path = unavailable-in-diagnostic.dll\n\n[Camera:Top]\nidentifier = simulated-top\nenabled = true\nname = Top camera\nbackend = simulation\nsimulation_width = 16\nsimulation_height = 12\n\n[Camera:Bottom]\nidentifier = simulated-bottom\nenabled = true\nname = Bottom camera\nbackend = simulation\nsimulation_width = 16\nsimulation_height = 12\n""",
        encoding="utf-8",
    )
    stdout_path = output / "pytest.stdout.log"
    stderr_path = output / "pytest.stderr.log"
    progress_path = output / "progress.jsonl"
    env = os.environ.copy()
    env.update(
        {
            "IOPANEL_QT_STRESS_CONFIG": str(config_path),
            "IOPANEL_QT_STRESS_CYCLES": str(cycles),
            "QT_QPA_PLATFORM": "windows",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(Path.cwd()),
        }
    )
    if suite:
        # The full suite must not accidentally enable manual-only Qt stress.
        env.pop("IOPANEL_QT_STRESS_CONFIG", None)
        env.pop("IOPANEL_QT_STRESS_CYCLES", None)
    if procdump_path:
        env["PROCDUMP_PATH"] = procdump_path
    command = _pytest_command(suite, suite_group=suite_group)
    manifest: dict[str, Any] = {
        "started_utc": dt.datetime.now(dt.UTC).isoformat(),
        "command": command,
        "python": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd()),
        "timeout_seconds": timeout_s,
        "cycles": cycles,
        "mode": "suite" if suite else "stress",
        "suite_group": suite_group,
        "qt_qpa_platform": env["QT_QPA_PLATFORM"],
        "stack_tool": procdump_path or os.environ.get("PROCDUMP_PATH"),
    }
    capture: dict[str, Any] = {"status": "not_needed"}
    stdout_offset = stderr_offset = 0
    progress_count = 0
    last_progress: str | None = None
    timed_out = False
    process: subprocess.Popen[bytes] | None = None
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, cwd=Path.cwd(), env=env, stdout=stdout, stderr=stderr)
            # CIM/PowerShell can stall independently of the supervised pytest
            # process. Do not kill healthy tests because optional identity
            # inspection timed out; fail closed for native dump capture instead.
            try:
                identity = _process_identity(process.pid)
            except subprocess.TimeoutExpired as exc:
                identity = None
                manifest["child_identity_probe_error"] = f"timeout after {exc.timeout}s"
            manifest["child_identity"] = identity
            if identity is not None and (
                identity.get("ExecutablePath", "").casefold() != sys.executable.casefold()
                or "pytest" not in str(identity.get("CommandLine", "")).casefold()
            ):
                raise RuntimeError(f"pytest process identity did not match launched pytest: {identity!r}")
            deadline = time.monotonic() + timeout_s
            while process.poll() is None:
                time.sleep(1)
                stdout_offset, lines = _tail(stdout_path, stdout_offset)
                stderr_offset, err_lines = _tail(stderr_path, stderr_offset)
                if err_lines:
                    with progress_path.open("a", encoding="utf-8") as progress:
                        for line in err_lines:
                            progress.write(json.dumps({"stream": "stderr", "line": line}) + "\n")
                if lines:
                    with progress_path.open("a", encoding="utf-8") as progress:
                        for line in lines:
                            progress.write(json.dumps({"stream": "stdout", "line": line}) + "\n")
                            last_progress = line
                            if line.startswith("{"):
                                try:
                                    event = json.loads(line)
                                    if event.get("event") in {
                                        "cycle_started",
                                        "cycle_completed",
                                        "cycle_failed",
                                        "stress_completed",
                                    }:
                                        progress_count += 1
                                except json.JSONDecodeError:
                                    pass
                if process.poll() is None and time.monotonic() >= deadline:
                    timed_out = True
                    if identity is None:
                        capture = {
                            "status": "identity_unverified",
                            "reason": "native capture skipped because child process identity could not be verified",
                        }
                    else:
                        try:
                            native_pid, native_identity = _native_pytest_identity(process.pid, identity)
                            manifest["native_child_identity"] = native_identity
                            capture = _capture_stack(native_pid, native_identity, output)
                        except subprocess.TimeoutExpired as exc:
                            capture = {"status": "identity_probe_timeout", "error": repr(exc)}
                    break
            if timed_out and process.poll() is None:
                _terminate_process_tree(process)
            returncode = process.wait(timeout=10)
    except BaseException:  # noqa: BLE001 - terminate the child even when the supervisor is interrupted.
        manifest["supervisor_error"] = traceback.format_exc()
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        returncode = 2
    stdout_offset, lines = _tail(stdout_path, stdout_offset)
    stderr_offset, err_lines = _tail(stderr_path, stderr_offset)
    if lines or err_lines:
        with progress_path.open("a", encoding="utf-8") as progress:
            for line in lines:
                progress.write(json.dumps({"stream": "stdout", "line": line}) + "\n")
                last_progress = line
            for line in err_lines:
                progress.write(json.dumps({"stream": "stderr", "line": line}) + "\n")
    manifest.update(
        {
            "finished_utc": dt.datetime.now(dt.UTC).isoformat(),
            "returncode": returncode,
            "timed_out": timed_out,
            "last_progress": last_progress,
            "progress_events": progress_count,
            "stack_capture": capture,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
    )
    _write_json(output / "manifest.json", manifest)
    if suite:
        print(
            f"Supervised pytest suite: returncode={returncode} timed_out={timed_out} "
            f"native_capture={capture.get('status')} last_output={last_progress!r}",
            flush=True,
        )
        print(f"Full stdout/stderr and manifest retained under {output}", flush=True)
    return 124 if timed_out else returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument(
        "--suite", action="store_true", help="supervise the normal pytest suite rather than opt-in stress"
    )
    parser.add_argument("--suite-group", choices=("core", "camera", "plot"))
    parser.add_argument("--procdump", type=str)
    args = parser.parse_args()
    return supervise(
        args.output,
        args.timeout_seconds,
        args.cycles,
        args.procdump,
        suite=args.suite,
        suite_group=args.suite_group,
    )


if __name__ == "__main__":
    raise SystemExit(main())
