"""Independent Windows process supervisor for the interrupted-startup child."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
MATLAB_EXE = Path(r"C:\Program Files\MATLAB\R2024b\bin\win64\MATLAB.exe")
sys.path.insert(0, str(REPOSITORY))

PROCESS_QUERY = r"""
$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(MATLAB|MATLABWindow|python|pythonw)\.exe$' } | ForEach-Object {
  $p = $_
  $owner = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue
  [pscustomobject]@{Name=$p.Name; PID=[int]$p.ProcessId; ParentPID=[int]$p.ParentProcessId; CreationDate=$p.CreationDate.ToUniversalTime().ToString('o'); ExecutablePath=$p.ExecutablePath; Owner=if($owner.ReturnValue -eq 0){"$($owner.Domain)\$($owner.User)"}else{"UNVERIFIED"}}
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


def _git(*args: str, repository: Path = REPOSITORY) -> str:
    result = subprocess.run(["git", *args], cwd=repository, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _shutdown_monotonic(child_log: Path) -> float | None:
    from tools.matlab_lifecycle.diagnostic_harness import read_jsonl

    result = read_jsonl(child_log, allow_incomplete_final_line=True)
    for row in reversed(result.rows):
        if row.get("event") == "manager_shutdown_requested":
            value = row.get("monotonic_ns")
            return value / 1_000_000_000 if isinstance(value, int) else None
    return None


def environment_manifest(repository: Path = REPOSITORY) -> dict[str, Any]:
    engine_dist = importlib.metadata.distribution("matlabengine")
    return {
        "utc": dt.datetime.now(dt.UTC).isoformat(timespec="microseconds"),
        "python": sys.version.replace("\n", " "),
        "python_executable": sys.executable,
        "matlab_engine_version": engine_dist.version,
        "matlab_engine_module": str(engine_dist.locate_file("matlab/engine/__init__.py")),
        "pyside6_version": importlib.metadata.version("PySide6"),
        "matlab_executable": str(MATLAB_EXE),
        "matlab_file_version": "24.2.0.2824689",
        "repository": {
            "path": str(repository),
            "branch": _git("branch", "--show-current", repository=repository),
            "head": _git("rev-parse", "HEAD", repository=repository),
            "status": _git("status", "--short", "--branch", repository=repository),
            "manager_sha256": hashlib.sha256(
                (repository / "logic" / "matlab_engine_manager.py").read_bytes()
            ).hexdigest(),
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def _report(output: Path, lines: list[str]) -> None:
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_diagnostic(
    output: Path,
    *,
    snapshot_fn: Callable[[], list[dict[str, Any]]] = process_snapshot,
    launch_fn: Callable[..., Any] | None = None,
    environment_fn: Callable[[Path], dict[str, Any]] = environment_manifest,
    repository: Path = REPOSITORY,
    max_before_shutdown_s: float = 120.0,
    max_after_shutdown_s: float = 150.0,
    post_exit_observation_s: float = 60.0,
) -> int:
    """Run with guaranteed event-writer closure, including every failure path."""
    from tools.matlab_lifecycle.diagnostic_harness import (
        JsonlEventWriter,
        compare_process_inventories,
        diagnostic_exit_code,
        is_matlab_process,
        launch_child,
        monitor_child,
        normalize_creation_time,
        read_jsonl,
        same_process_identity,
        validate_child_evidence,
    )

    output.mkdir(parents=True, exist_ok=True)
    events = JsonlEventWriter(output / "supervisor.jsonl")
    exit_code = 1
    writer_errors: tuple[str, ...] = ()
    report_lines = ["# MATLAB Engine interrupted-startup diagnostic", "", f"Diagnostic directory: `{output}`"]
    evidence_errors: list[str] = []
    child_return_code: int | None = None
    snapshot_errors: tuple[str, ...] = ()
    try:
        events.emit("supervisor_started", python=sys.version, pid=os.getpid())
        manifest = environment_fn(repository)
        _write_json(output / "environment_manifest.json", manifest)

        baseline = snapshot_fn()
        _write_json(output / "baseline_processes.json", baseline)
        baseline_matlab = [row for row in baseline if is_matlab_process(row)]
        unknown_baseline = [
            row
            for row in baseline_matlab
            if normalize_creation_time(row.get("CreationDate", row.get("CreationUTC"))) is None
        ]
        if unknown_baseline:
            evidence_errors.append(f"baseline MATLAB creation time unrecognized: {unknown_baseline!r}")
            raise RuntimeError(evidence_errors[-1])
        events.emit("preflight", manifest=manifest, baseline_processes=baseline)

        child_source = Path(__file__).with_name("interrupted_startup_child.py")
        child_copy = output / child_source.name
        child_copy.write_bytes(child_source.read_bytes())
        (output / "diagnostic_harness.py").write_bytes(Path(__file__).with_name("diagnostic_harness.py").read_bytes())
        (output / "supervisor.py").write_bytes(Path(__file__).read_bytes())

        stdout_path = output / "child.stdout.txt"
        stderr_path = output / "child.stderr.txt"
        launch = launch_child if launch_fn is None else launch_fn
        child_started_at = time.monotonic()
        process = launch(
            [sys.executable, str(child_copy), str(output)],
            cwd=repository,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        events.emit("child_started", pid=process.pid)
        result = monitor_child(
            process,
            writer=events,
            snapshot=snapshot_fn,
            snapshot_path=output / "process_snapshots.jsonl",
            shutdown_requested=lambda: _shutdown_monotonic(output / "child.jsonl"),
            max_before_shutdown_s=max_before_shutdown_s,
            max_after_shutdown_s=max_after_shutdown_s,
            post_exit_observation_s=post_exit_observation_s,
            monitor_started_at=child_started_at,
        )
        child_return_code = result.return_code
        snapshot_errors = result.snapshot_errors

        final = snapshot_fn()
        _write_json(output / "final_processes.json", final)
        comparison = compare_process_inventories(baseline, final)
        _write_json(output / "baseline_process_comparison.json", comparison)
        evidence_errors.extend(snapshot_errors)
        for key in ("missing", "pid_reused", "unverified"):
            if comparison[key]:
                evidence_errors.append(f"baseline MATLAB process comparison {key}: {comparison[key]!r}")

        child_log = read_jsonl(output / "child.jsonl")
        evidence_errors.extend(validate_child_evidence(child_log.rows, child_log.errors))
        snapshot_log = read_jsonl(output / "process_snapshots.jsonl")
        evidence_errors.extend(snapshot_log.errors)
        supervisor_log = read_jsonl(output / "supervisor.jsonl")
        evidence_errors.extend(supervisor_log.errors)
        fallback_count = getattr(events, "serialization_failures", 0)
        if fallback_count:
            evidence_errors.append(f"supervisor JSONL serialization failures: {fallback_count}")

        baseline_identities = [row for row in baseline if is_matlab_process(row)]
        remaining_new = [
            row
            for row in final
            if is_matlab_process(row)
            and not any(same_process_identity(row, original) for original in baseline_identities)
        ]
        if remaining_new:
            evidence_errors.append(f"new MATLAB processes remain after child exit: {remaining_new!r}")
        candidates: dict[tuple[str, int | None], dict[str, Any]] = {}
        for inventory_row in snapshot_log.rows:
            for process_row in inventory_row.get("processes", []):
                if not is_matlab_process(process_row):
                    continue
                if any(same_process_identity(process_row, original) for original in baseline_identities):
                    continue
                identity_time = normalize_creation_time(process_row.get("CreationDate", process_row.get("CreationUTC")))
                candidates[(str(process_row.get("PID")), identity_time)] = {
                    **process_row,
                    "direct_child_of_diagnostic_child": process_row.get("ParentPID") == process.pid,
                    "attribution": "candidate; direct parent match"
                    if process_row.get("ParentPID") == process.pid
                    else "UNVERIFIED",
                }
        candidate_rows = list(candidates.values())
        _write_json(output / "candidate_processes.json", candidate_rows)

        report_lines.extend(
            [
                "",
                "## Environment",
                "",
                f"- Python: `{manifest.get('python', 'UNVERIFIED')}`",
                f"- MATLAB Engine: `{manifest.get('matlab_engine_version', 'UNVERIFIED')}` — `{manifest.get('matlab_engine_module', 'UNVERIFIED')}`",
                f"- Repository: `{manifest.get('repository', {}).get('branch', 'UNVERIFIED')}` / `{manifest.get('repository', {}).get('head', 'UNVERIFIED')}`",
                "",
                "## Lifecycle observations",
                "",
                f"- Child PID: `{process.pid}`; exit code: `{child_return_code}`",
                f"- Parent startup deadline: `{max_before_shutdown_s}` seconds from child launch",
                f"- Deadline reached: `{result.deadline_reached}`; phase: `{result.deadline_reason or 'none'}`",
                f"- Post-exit samples: `{result.snapshots_after_exit}`",
                f"- Existing MATLAB processes preserved: `{len(comparison['preserved'])}/{len(baseline_matlab)}`",
                f"- Candidate MATLAB processes observed: `{candidate_rows!r}`",
                f"- New MATLAB processes remaining: `{remaining_new!r}`",
                f"- Evidence errors: `{evidence_errors!r}`",
            ]
        )
        exit_code = diagnostic_exit_code(
            child_return_code,
            snapshot_errors=snapshot_errors,
            evidence_errors=evidence_errors,
            serialization_failures=fallback_count,
        )
        events.emit("diagnostic_complete", exit_code=exit_code, evidence_errors=evidence_errors)
    except BaseException as exc:  # noqa: BLE001 - supervisor failures must become a report and nonzero exit.
        traceback_text = traceback.format_exc()
        print(traceback_text, file=sys.stderr)
        try:
            events.emit(
                "supervisor_exception",
                exception_type=type(exc).__name__,
                exception=str(exc),
                traceback=traceback_text,
            )
        except Exception as logging_error:  # noqa: BLE001 - preserve original supervisor failure, surface log failure.
            print(f"could not log supervisor failure: {logging_error!r}", file=sys.stderr)
        try:
            (output / "supervisor_failure.txt").write_text(traceback_text, encoding="utf-8")
        except OSError as write_error:
            print(f"could not preserve supervisor traceback: {write_error!r}", file=sys.stderr)
        report_lines.extend(["", "## Supervisor failure", "", "```text", traceback_text.rstrip(), "```"])
        exit_code = 1
    finally:
        try:
            writer_errors = events.close()
        except BaseException as exc:  # noqa: BLE001 - always return failure if logger shutdown fails.
            writer_errors = (f"{type(exc).__name__}: {exc}",)
            print(f"supervisor JSONL close failed: {writer_errors!r}", file=sys.stderr)
        if writer_errors:
            exit_code = 1
            report_lines.extend(["", f"Supervisor JSONL writer errors: `{writer_errors!r}`"])
        try:
            _report(output, report_lines)
        except OSError as exc:
            print(f"could not write REPORT.md: {exc!r}", file=sys.stderr)
            exit_code = 1

    return exit_code


def main() -> int:
    try:
        local_app_data = Path(os.environ["LOCALAPPDATA"])
        diagnostic_root = local_app_data / "IOPanel" / "diagnostics" / "matlab-lifecycle"
        diagnostic_root.mkdir(parents=True, exist_ok=True)
        output = diagnostic_root / dt.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%z")
        output.mkdir()
        return run_diagnostic(output)
    except BaseException:  # noqa: BLE001 - setup failures must be visible and nonzero.
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
