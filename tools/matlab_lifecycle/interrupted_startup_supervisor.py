"""Independent Windows process supervisor for the interrupted-startup child."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(r"C:\Users\bellanca\Desktop\IOPanel-dev")
sys.path.insert(0, str(REPOSITORY))

MATLAB_EXE = Path(r"C:\Program Files\MATLAB\R2024b\bin\win64\MATLAB.exe")
PROCESS_QUERY = r"""
$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(MATLAB|MATLABWindow|python|pythonw)\.exe$' } | ForEach-Object {
  $p = $_
  $owner = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue
  [pscustomobject]@{Name=$p.Name; PID=[int]$p.ProcessId; ParentPID=[int]$p.ParentProcessId; CreationDate=$p.CreationDate; ExecutablePath=$p.ExecutablePath; Owner=if($owner.ReturnValue -eq 0){"$($owner.Domain)\$($owner.User)"}else{"UNVERIFIED"}}
} | Sort-Object Name,PID | ConvertTo-Json -Depth 3 -Compress
"""


def process_snapshot() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", PROCESS_QUERY],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"process snapshot failed ({result.returncode}): {result.stderr.strip()}")
    output = result.stdout.strip()
    if not output:
        return []
    parsed = json.loads(output)
    return parsed if isinstance(parsed, list) else [parsed]


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPOSITORY, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _shutdown_monotonic(child_log: Path) -> float | None:
    for row in reversed(_events(child_log)):
        if row.get("event") == "manager_shutdown_requested":
            value = row.get("monotonic_ns")
            return value / 1_000_000_000 if isinstance(value, int) else None
    return None


def main() -> int:
    local_app_data = Path(os.environ["LOCALAPPDATA"])
    diagnostic_root = local_app_data / "IOPanel" / "diagnostics" / "matlab-lifecycle"
    diagnostic_root.mkdir(parents=True, exist_ok=True)
    output = diagnostic_root / dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%z")
    output.mkdir()

    from tools.matlab_lifecycle.diagnostic_harness import (
        JsonlEventWriter,
        diagnostic_exit_code,
        launch_child,
        monitor_child,
    )

    supervisor_events = JsonlEventWriter(output / "supervisor.jsonl")
    child_script = Path(__file__).with_name("interrupted_startup_child.py")
    shutil_copy = output / child_script.name
    shutil_copy.write_bytes(child_script.read_bytes())
    (output / "diagnostic_harness.py").write_bytes(Path(__file__).with_name("diagnostic_harness.py").read_bytes())
    (output / "supervisor.py").write_bytes(Path(__file__).read_bytes())

    engine_dist = importlib.metadata.distribution("matlabengine")
    manifest = {
        "utc": dt.datetime.now(dt.UTC).isoformat(timespec="microseconds"),
        "python": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "matlab_engine_version": engine_dist.version,
        "matlab_engine_module": str(engine_dist.locate_file("matlab/engine/__init__.py")),
        "pyside6_version": importlib.metadata.version("PySide6"),
        "matlab_executable": str(MATLAB_EXE),
        "matlab_file_version": "24.2.0.2824689",
        "repository": {
            "path": str(REPOSITORY),
            "branch": _git("branch", "--show-current"),
            "head": _git("rev-parse", "HEAD"),
            "status": _git("status", "--short", "--branch"),
            "manager_sha256": hashlib.sha256(
                (REPOSITORY / "logic" / "matlab_engine_manager.py").read_bytes()
            ).hexdigest(),
        },
    }
    (output / "environment_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    baseline = process_snapshot()
    (output / "baseline_processes.json").write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    protected = [p for p in baseline if p["Name"].lower() == "matlab.exe"]
    previous_identity = next(
        (p for p in protected if p["PID"] == 22312 and p["CreationDate"] == "/Date(1791550257476)/"), None
    )
    supervisor_events.emit("preflight", manifest=manifest, baseline_processes=baseline)
    supervisor_events.emit(
        "previous_pid_22312_identity",
        status="MATCH" if previous_identity else "NOT_MATCHED_OR_ABSENT",
        process=previous_identity,
    )

    stdout_path = output / "child.stdout.txt"
    stderr_path = output / "child.stderr.txt"
    process = launch_child(
        [sys.executable, str(shutil_copy), str(output)],
        cwd=REPOSITORY,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    supervisor_events.emit("child_started", pid=process.pid)
    result = monitor_child(
        process,
        writer=supervisor_events,
        snapshot=process_snapshot,
        snapshot_path=output / "process_snapshots.jsonl",
        shutdown_requested=lambda: _shutdown_monotonic(output / "child.jsonl"),
    )
    final = process_snapshot()
    (output / "final_processes.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
    baseline_matlab = {(p["PID"], p.get("CreationDate")) for p in protected}
    candidates: dict[tuple[int, Any], dict[str, Any]] = {}
    for row in _events(output / "process_snapshots.jsonl"):
        for proc in row.get("processes", []):
            key = (proc.get("PID"), proc.get("CreationDate"))
            if proc.get("Name", "").lower() == "matlab.exe" and key not in baseline_matlab:
                candidates[key] = {
                    **proc,
                    "direct_child_of_diagnostic_child": proc.get("ParentPID") == process.pid,
                    "attribution": "candidate; direct parent match"
                    if proc.get("ParentPID") == process.pid
                    else "UNVERIFIED",
                }
    (output / "candidate_processes.json").write_text(json.dumps(list(candidates.values()), indent=2), encoding="utf-8")

    child_events = _events(output / "child.jsonl")

    def first(name: str) -> dict[str, Any] | None:
        return next((row for row in child_events if row.get("event") == name), None)

    start = first("startup_state_observed") or {}
    shutdown = first("manager_shutdown_returned") or {}
    cancel_returns = [
        r for r in child_events if r.get("event") == "engine_api_call_return" and r.get("api") == "cancel"
    ]
    future_results = [r for r in child_events if r.get("event") == "engine_api_call_enter" and r.get("api") == "result"]
    quit_calls = [r for r in child_events if r.get("event") == "engine_api_call_enter" and r.get("api") == "quit"]
    protected_final = [p for p in final if p["PID"] == 22312 and p["CreationDate"] == "/Date(1791550257476)/"]
    report = [
        "# MATLAB Engine interrupted-startup diagnostic",
        "",
        f"Diagnostic directory: `{output}`",
        "",
        "## Environment",
        "",
        f"- Python: `{sys.version.split()[0]}` — `{sys.executable}`",
        f"- MATLAB Engine: `{engine_dist.version}` — `{manifest['matlab_engine_module']}`",
        f"- PySide6: `{manifest['pyside6_version']}`",
        f"- Repository: `{manifest['repository']['branch']}` / `{manifest['repository']['head']}`",
        f"- Manager source SHA256: `{manifest['repository']['manager_sha256']}`",
        "",
        "## Lifecycle observations",
        "",
        f"- Manager state: `{start.get('manager_state', 'UNVERIFIED')}`; pending future: `{start.get('future_pending', 'UNVERIFIED')}`",
        f"- `shutdown()` returned: `{bool(shutdown)}`; duration ns: `{shutdown.get('duration_ns', 'UNVERIFIED')}`; state: `{shutdown.get('manager_state_after', 'UNVERIFIED')}`",
        f"- `FutureResult.cancel()` returns recorded: `{len(cancel_returns)}`; values: `{[r.get('result') for r in cancel_returns]}`",
        f"- `FutureResult.result()` calls observed: `{len(future_results)}`",
        f"- Engine `quit()` calls observed: `{len(quit_calls)}`",
        f"- Child exit code: `{result.return_code}`; deadline reached: `{result.deadline_reached}`",
        f"- Process snapshot errors: `{list(result.snapshot_errors)}`",
        "",
        "## Process inventory",
        "",
        f"- Baseline MATLAB sessions: `{json.dumps(protected)}`",
        f"- Candidate MATLAB processes: `{json.dumps(list(candidates.values()))}`",
        f"- PID 22312 preserved with original creation time: `{bool(protected_final)}`",
        f"- Post-exit samples: `{result.snapshots_after_exit}`",
        "",
        "Artifacts include child stdout/stderr, both JSONL event traces, process snapshots, the environment manifest, boundary inventories, candidate inventory, and copied diagnostic sources.",
    ]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    supervisor_events.emit(
        "diagnostic_complete", report=str(output / "REPORT.md"), child_return_code=result.return_code
    )
    writer_errors = supervisor_events.close()
    if writer_errors:
        report.extend(["", f"- Supervisor JSONL writer errors: `{list(writer_errors)}`"])
        (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(output)
    return diagnostic_exit_code(
        result.return_code,
        snapshot_errors=result.snapshot_errors,
        writer_errors=writer_errors,
    )


if __name__ == "__main__":
    raise SystemExit(main())
