"""Independent parent supervisor for the normal Python-exit scenario."""

from __future__ import annotations

import datetime as dt
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))

from tools.matlab_lifecycle.diagnostic_harness import (
    JsonlEventWriter,
    compare_process_inventories,
    diagnostic_exit_code,
    is_matlab_process,
    launch_child,
    monitor_child,
    normalize_creation_time,
    read_jsonl,
    summarize_matlab_candidates,
    validate_normal_exit_child_evidence,
)
from tools.matlab_lifecycle.interrupted_startup_supervisor import (
    _report,
    _shutdown_monotonic,
    _write_json,
    environment_manifest,
    process_snapshot,
)


def run_normal_exit_diagnostic(
    output: Path,
    *,
    snapshot_fn: Callable[[], list[dict[str, Any]]] = process_snapshot,
    launch_fn: Callable[..., Any] | None = None,
    environment_fn: Callable[[Path], dict[str, Any]] = environment_manifest,
    repository: Path = REPOSITORY,
    max_before_shutdown_s: float = 120.0,
    max_after_shutdown_s: float = 150.0,
    post_exit_observation_s: float = 60.0,
    interval_s: float = 1.0,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], Any] | None = None,
) -> int:
    """Run one child and observe it without waiting on its daemon disposer."""
    output.mkdir(parents=True, exist_ok=True)
    events = JsonlEventWriter(output / "supervisor.jsonl")
    exit_code = 1
    writer_errors: tuple[str, ...] = ()
    evidence_errors: list[str] = []
    report_lines = [
        "# MATLAB Engine normal-interpreter-exit diagnostic",
        "",
        f"Diagnostic directory: `{output}`",
        "",
        "The child returns naturally after requesting manager shutdown. Its daemon disposer is not joined.",
        "The parent polls the child process and writes snapshots to files; it does not wait on child threads.",
    ]
    child_return_code: int | None = None
    snapshot_errors: tuple[str, ...] = ()
    baseline: list[dict[str, Any]] = []
    final: list[dict[str, Any]] = []
    comparison: dict[str, list[dict[str, Any]]] = {"preserved": [], "missing": [], "pid_reused": [], "unverified": []}
    candidates: list[dict[str, Any]] = []
    monitor_result: Any | None = None

    try:
        events.emit("normal_exit_supervisor_started", python=sys.version, pid=os.getpid())
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
        events.emit("normal_exit_preflight", manifest=manifest, baseline_processes=baseline)

        source_dir = Path(__file__).parent
        child_source = source_dir / "normal_exit_child.py"
        child_copy = output / child_source.name
        child_copy.write_bytes(child_source.read_bytes())
        (output / "diagnostic_harness.py").write_bytes((source_dir / "diagnostic_harness.py").read_bytes())
        (output / "normal_exit_supervisor.py").write_bytes(Path(__file__).read_bytes())

        stdout_path = output / "child.stdout.txt"
        stderr_path = output / "child.stderr.txt"
        launch = launch_child if launch_fn is None else launch_fn
        child_started_at = monotonic()
        process = launch(
            [sys.executable, str(child_copy), str(output), str(repository)],
            cwd=repository,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        events.emit("normal_exit_child_started", pid=process.pid)
        monitor_result = monitor_child(
            process,
            writer=events,
            snapshot=snapshot_fn,
            snapshot_path=output / "process_snapshots.jsonl",
            shutdown_requested=lambda: _shutdown_monotonic(output / "child.jsonl"),
            max_before_shutdown_s=max_before_shutdown_s,
            max_after_shutdown_s=max_after_shutdown_s,
            post_exit_observation_s=post_exit_observation_s,
            interval_s=interval_s,
            monitor_started_at=child_started_at,
            monotonic=monotonic,
            wait=wait,
        )
        child_return_code = monitor_result.return_code
        snapshot_errors = monitor_result.snapshot_errors

        final = snapshot_fn()
        _write_json(output / "final_processes.json", final)
        comparison = compare_process_inventories(baseline, final)
        _write_json(output / "baseline_process_comparison.json", comparison)
        for key in ("missing", "pid_reused", "unverified"):
            if comparison[key]:
                evidence_errors.append(f"baseline MATLAB process comparison {key}: {comparison[key]!r}")

        child_log = read_jsonl(output / "child.jsonl")
        evidence_errors.extend(validate_normal_exit_child_evidence(child_log.rows, child_log.errors))
        snapshot_log = read_jsonl(output / "process_snapshots.jsonl")
        evidence_errors.extend(snapshot_log.errors)
        evidence_errors.extend(snapshot_errors)
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        if stderr.strip():
            evidence_errors.append("child stderr is nonempty; inspect child.stderr.txt")

        candidates = summarize_matlab_candidates(
            baseline,
            snapshot_log.rows,
            final,
            child_pid=monitor_result.child_pid,
        )
        if any(
            not candidate.get("PID")
            or normalize_creation_time(candidate.get("CreationDate", candidate.get("CreationUTC"))) is None
            for candidate in candidates
        ):
            evidence_errors.append("a new MATLAB candidate lacked a verifiable PID or creation time")
        _write_json(output / "candidate_processes.json", candidates)

        fallback_count = getattr(events, "serialization_failures", 0)
        if fallback_count:
            evidence_errors.append(f"supervisor JSONL serialization failures: {fallback_count}")
        exit_code = diagnostic_exit_code(
            child_return_code,
            snapshot_errors=snapshot_errors,
            evidence_errors=evidence_errors,
            serialization_failures=fallback_count,
        )
        events.emit(
            "normal_exit_diagnostic_complete",
            exit_code=exit_code,
            evidence_errors=evidence_errors,
            candidate_count=len(candidates),
        )
        report_lines.extend(
            [
                "",
                "## Lifecycle observations",
                "",
                f"- Parent deadlines: `{max_before_shutdown_s}` seconds before shutdown; `{max_after_shutdown_s}` seconds after shutdown; `{post_exit_observation_s}` seconds after child exit",
                f"- Child PID: `{monitor_result.child_pid}`; return code: `{child_return_code}`",
                f"- Parent deadline reached: `{monitor_result.deadline_reached}`; phase: `{monitor_result.deadline_reason or 'none'}`",
                f"- Post-exit samples: `{monitor_result.snapshots_after_exit}` of `{post_exit_observation_s}` seconds",
                f"- Protected MATLAB processes preserved: `{len(comparison['preserved'])}/{len(baseline_matlab)}`",
                f"- New MATLAB candidates: `{candidates!r}`",
                f"- Evidence errors: `{evidence_errors!r}`",
                f"- Diagnostic result: `{'PASS' if exit_code == 0 else 'FAIL'}`",
            ]
        )
    except BaseException as exc:  # noqa: BLE001 - parent errors must be preserved and reported.
        traceback_text = traceback.format_exc()
        print(traceback_text, file=sys.stderr)
        try:
            events.emit(
                "normal_exit_supervisor_exception",
                exception_type=type(exc).__name__,
                exception=str(exc),
                traceback=traceback_text,
            )
        except Exception as logging_error:  # noqa: BLE001 - preserve original failure.
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
        except BaseException as exc:  # noqa: BLE001 - logger shutdown failure is a diagnostic failure.
            writer_errors = (f"{type(exc).__name__}: {exc}",)
        if writer_errors:
            report_lines.extend(["", f"Supervisor JSONL writer errors: `{writer_errors!r}`"])
            exit_code = 1
        supervisor_log = read_jsonl(output / "supervisor.jsonl")
        if supervisor_log.errors:
            report_lines.extend(["", f"Supervisor JSONL parse errors: `{supervisor_log.errors!r}`"])
            exit_code = 1
        for index, line in enumerate(report_lines):
            if line.startswith("- Diagnostic result:"):
                report_lines[index] = f"- Diagnostic result: `{'PASS' if exit_code == 0 else 'FAIL'}`"
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
        output = diagnostic_root / f"normal_exit_{dt.datetime.now().astimezone():%Y%m%d_%H%M%S_%z}"
        output.mkdir()
        return run_normal_exit_diagnostic(output)
    except BaseException:  # noqa: BLE001 - setup failures must be visible and nonzero.
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
