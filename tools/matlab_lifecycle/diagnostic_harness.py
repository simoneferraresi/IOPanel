"""Failure-transparent JSONL logging and child supervision helpers.

These helpers do not import MATLAB or application modules. Live diagnostic
entry points import those dependencies only in their isolated child process.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class EventWriter(Protocol):
    def emit(self, event_name: str, **fields: Any) -> bool: ...


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception as exc:  # noqa: BLE001 - user-provided repr can raise arbitrary exceptions.
        return f"<repr failed: {type(exc).__name__}>"


def _safe_text(value: Any) -> str:
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - user-provided str can raise arbitrary exceptions.
        return _safe_repr(value)


class JsonlEventWriter:
    """Serialize event records before enqueueing and preserve serialization errors."""

    def __init__(self, path: Path, *, daemon: bool = False) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._io_errors: list[str] = []
        self._serialization_failures = 0
        self._closed = False
        self._thread = threading.Thread(target=self._write_loop, name="JsonlEventWriter", daemon=daemon)
        self._thread.start()

    @property
    def io_errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._io_errors)

    @property
    def serialization_failures(self) -> int:
        with self._lock:
            return self._serialization_failures

    @staticmethod
    def _timestamp() -> dict[str, Any]:
        return {
            "utc": dt.datetime.now(dt.UTC).isoformat(timespec="microseconds"),
            "monotonic_ns": time.monotonic_ns(),
        }

    def emit(self, event_name: str, **fields: Any) -> bool:
        """Queue one JSONL row; return false and log a fallback row on bad data."""
        with self._lock:
            if self._closed or self._io_errors:
                return False
            row = {"event": event_name, **self._timestamp(), **fields}
            try:
                encoded = json.dumps(row, ensure_ascii=True, allow_nan=False)
            except (TypeError, ValueError, OverflowError) as exc:
                self._serialization_failures += 1
                fallback = {
                    "event": "event_serialization_failure",
                    **self._timestamp(),
                    "attempted_event": event_name,
                    "exception_type": type(exc).__name__,
                    "exception": _safe_text(exc),
                    "field_names": sorted(fields),
                    "field_values_repr": {key: _safe_repr(value) for key, value in fields.items()},
                }
                encoded = json.dumps(fallback, ensure_ascii=True, allow_nan=False)
                self._queue.put(encoded)
                return False
            self._queue.put(encoded)
        return True

    def _write_loop(self) -> None:
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                while True:
                    encoded = self._queue.get()
                    if encoded is None:
                        stream.flush()
                        os.fsync(stream.fileno())
                        return
                    stream.write(encoded + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            with self._lock:
                self._io_errors.append(f"{type(exc).__name__}: {exc}")

    def close(self) -> tuple[str, ...]:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._queue.put(None)
        self._thread.join()
        return self.io_errors


def record_startup_state(writer: EventWriter, manager_state: str, future: Any | None) -> bool:
    """Record manager state and whether its startup future remains pending."""
    pending = future is not None and not bool(future.done())
    writer.emit(
        "startup_state_observed",
        manager_state=manager_state,
        future_pending=pending,
        future_type=type(future).__qualname__ if future is not None else None,
        future_module=type(future).__module__ if future is not None else None,
    )
    return pending


def record_child_exception(writer: EventWriter, exception: BaseException, traceback_text: str) -> int:
    """Persist an uncaught child failure and return a nonzero exit status."""
    writer.emit(
        "child_exception",
        exception_type=type(exception).__name__,
        exception=_safe_text(exception),
        traceback=traceback_text,
    )
    return 1


def wait_for_disposer(
    writer: EventWriter,
    worker: Any,
    *,
    shutdown_started_ns: int,
    deadline_s: float = 150.0,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> bool:
    """Join a disposal worker only through the shared bounded deadline."""
    remaining_s = max(0.0, deadline_s - (monotonic_ns() - shutdown_started_ns) / 1_000_000_000)
    writer.emit("disposal_wait_started", worker_name=worker.name, remaining_s=remaining_s)
    try:
        worker.join(timeout=remaining_s)
        alive = bool(worker.is_alive())
    except BaseException as exc:
        writer.emit(
            "disposal_wait_exception",
            worker_name=worker.name,
            exception_type=type(exc).__name__,
            exception=_safe_text(exc),
        )
        raise
    writer.emit("disposal_wait_complete", worker_name=worker.name, alive=alive)
    return alive


def record_call(writer: EventWriter, event_name: str, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Observe a call used with fakes/offline tests; record and re-raise failures."""
    started = time.monotonic_ns()
    writer.emit(f"{event_name}_call", started_monotonic_ns=started)
    try:
        result = operation(*args, **kwargs)
    except BaseException as exc:
        writer.emit(
            f"{event_name}_exception",
            exception_type=type(exc).__name__,
            exception=str(exc),
            duration_ns=time.monotonic_ns() - started,
        )
        raise
    writer.emit(
        f"{event_name}_return",
        result=result if isinstance(result, (bool, int, float, str)) or result is None else None,
        result_type=type(result).__qualname__,
        duration_ns=time.monotonic_ns() - started,
    )
    return result


class ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...


_WAIT_EVENT = threading.Event()


def _default_wait(duration: float) -> None:
    _WAIT_EVENT.wait(duration)


@dataclass(frozen=True)
class MonitorResult:
    child_pid: int
    return_code: int | None
    deadline_reached: bool
    snapshots_after_exit: int
    snapshot_errors: tuple[str, ...]
    deadline_reason: str | None = None


def diagnostic_exit_code(
    child_return_code: int | None,
    *,
    snapshot_errors: Sequence[str] = (),
    writer_errors: Sequence[str] = (),
    evidence_errors: Sequence[str] = (),
    serialization_failures: int = 0,
) -> int:
    """Return success only when the child and evidence collection both succeeded."""
    return (
        0
        if child_return_code == 0
        and not snapshot_errors
        and not writer_errors
        and not evidence_errors
        and serialization_failures == 0
        else 1
    )


def monitor_child(
    process: ProcessLike,
    *,
    writer: EventWriter,
    snapshot: Callable[[], Sequence[Mapping[str, Any]]],
    snapshot_path: Path,
    shutdown_requested: Callable[[], float | None],
    max_before_shutdown_s: float = 120.0,
    max_after_shutdown_s: float = 150.0,
    post_exit_observation_s: float = 60.0,
    interval_s: float = 1.0,
    monotonic: Callable[[], float] = time.monotonic,
    monitor_started_at: float | None = None,
    wait: Callable[[float], Any] | None = None,
) -> MonitorResult:
    """Monitor without terminating the child; continue monitoring after failure."""
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    wait_for_event = wait or _default_wait
    snapshot_errors: list[str] = []
    snapshots_after_exit = 0
    return_code: int | None = None
    deadline_reached = False
    startup_deadline = (monotonic() if monitor_started_at is None else monitor_started_at) + max_before_shutdown_s

    def take_snapshot(phase: str) -> None:
        nonlocal snapshots_after_exit
        try:
            processes = list(snapshot())
            row = {
                "event": "process_snapshot",
                "phase": phase,
                "utc": dt.datetime.now(dt.UTC).isoformat(timespec="microseconds"),
                "monotonic_ns": time.monotonic_ns(),
                "processes": processes,
            }
            with snapshot_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            if phase == "post_exit":
                snapshots_after_exit += 1
        except Exception as exc:  # noqa: BLE001 - supervisor must preserve monitoring after query failures.
            error = f"{type(exc).__name__}: {_safe_repr(exc)}"
            snapshot_errors.append(error)
            writer.emit("process_snapshot_exception", phase=phase, exception=error)

    take_snapshot("running")
    while True:
        return_code = process.poll()
        if return_code is not None:
            break
        requested_at = shutdown_requested()
        current = monotonic()
        if (requested_at is None and current >= startup_deadline) or (
            requested_at is not None and requested_at > startup_deadline
        ):
            deadline_reached = True
            writer.emit("child_startup_deadline_reached_left_running", pid=process.pid, return_code=None)
            take_snapshot("startup_deadline")
            return MonitorResult(
                process.pid,
                None,
                True,
                snapshots_after_exit,
                tuple(snapshot_errors),
                "before_shutdown_requested",
            )
        if requested_at is not None and current - requested_at >= max_after_shutdown_s:
            deadline_reached = True
            writer.emit("child_deadline_reached_left_running", pid=process.pid, return_code=None)
            take_snapshot("deadline")
            return MonitorResult(
                process.pid,
                None,
                True,
                snapshots_after_exit,
                tuple(snapshot_errors),
                "after_shutdown_requested",
            )
        take_snapshot("running")
        wait_for_event(interval_s)

    writer.emit("child_exited", pid=process.pid, return_code=return_code)
    if return_code != 0:
        writer.emit("child_failed", pid=process.pid, return_code=return_code)

    post_exit_deadline = monotonic() + post_exit_observation_s
    while monotonic() < post_exit_deadline:
        take_snapshot("post_exit")
        wait_for_event(min(interval_s, max(0.0, post_exit_deadline - monotonic())))
    writer.emit("post_exit_observation_complete", pid=process.pid, duration_s=post_exit_observation_s)
    return MonitorResult(process.pid, return_code, deadline_reached, snapshots_after_exit, tuple(snapshot_errors))


@dataclass(frozen=True)
class JsonlReadResult:
    rows: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]


def read_jsonl(path: Path, *, allow_incomplete_final_line: bool = False) -> JsonlReadResult:
    """Read JSONL without silently discarding malformed or incomplete evidence."""
    try:
        content = path.read_bytes()
    except OSError as exc:
        return JsonlReadResult((), (f"cannot read {path}: {type(exc).__name__}: {exc}",))
    if not content:
        return JsonlReadResult((), (f"empty JSONL evidence: {path}",))

    chunks = content.splitlines(keepends=True)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        is_final = index == len(chunks)
        complete = chunk.endswith((b"\n", b"\r"))
        if is_final and not complete and allow_incomplete_final_line:
            errors.append(f"incomplete trailing JSONL record in {path} line {index} (write may still be active)")
            continue
        if not complete:
            errors.append(f"incomplete JSONL record in {path} line {index}")
        try:
            value = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSONL record in {path} line {index}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"JSONL record in {path} line {index} is {type(value).__name__}, expected object")
            continue
        rows.append(value)
    return JsonlReadResult(tuple(rows), tuple(errors))


REQUIRED_SUCCESS_EVENTS = (
    "child_environment",
    "prewarm_requested",
    "startup_state_observed",
    "manager_shutdown_requested",
    "manager_shutdown_returned",
    "disposal_thread_observed",
    "disposal_wait_started",
    "disposal_wait_complete",
)
REQUIRED_NORMAL_EXIT_EVENTS = (
    "child_environment",
    "prewarm_requested",
    "startup_state_observed",
    "manager_shutdown_requested",
    "manager_shutdown_returned",
    "disposal_thread_observed",
    "python_interpreter_atexit",
)
MATLAB_PROCESS_NAMES = frozenset({"matlab.exe", "matlabwindow.exe"})


def is_matlab_process(row: Mapping[str, Any]) -> bool:
    return str(row.get("Name", "")).lower() in MATLAB_PROCESS_NAMES


def validate_child_evidence(rows: Sequence[Mapping[str, Any]], parse_errors: Sequence[str] = ()) -> tuple[str, ...]:
    """Require a complete, failure-free startup/shutdown chronology for success."""
    errors = list(parse_errors)
    events = [row.get("event") for row in rows]
    if "event_serialization_failure" in events:
        errors.append("child JSONL contains event_serialization_failure")
    if "child_exception" in events:
        errors.append("child JSONL contains child_exception")
    positions: list[int] = []
    for required in REQUIRED_SUCCESS_EVENTS:
        count = events.count(required)
        if count == 0:
            errors.append(f"missing critical child event: {required}")
        elif count > 1:
            errors.append(f"critical child event occurred {count} times: {required}")
        else:
            positions.append(events.index(required))
    if len(positions) == len(REQUIRED_SUCCESS_EVENTS) and positions != sorted(positions):
        errors.append("critical child events are out of lifecycle order")
    startup = next((row for row in rows if row.get("event") == "startup_state_observed"), None)
    if startup is not None and (
        startup.get("manager_state") != "STARTING" or startup.get("future_pending") is not True
    ):
        errors.append("startup observation did not confirm a pending STARTING future")
    disposal = next((row for row in reversed(rows) if row.get("event") == "disposal_wait_complete"), None)
    if disposal is not None and disposal.get("alive") is not False:
        errors.append("disposal worker was still alive after its bounded observation")
    return tuple(errors)


def validate_normal_exit_child_evidence(
    rows: Sequence[Mapping[str, Any]], parse_errors: Sequence[str] = ()
) -> tuple[str, ...]:
    """Validate normal-exit chronology without requiring daemon disposal to finish."""
    errors = list(parse_errors)
    events = [row.get("event") for row in rows]
    if "event_serialization_failure" in events:
        errors.append("child JSONL contains event_serialization_failure")
    if "child_exception" in events:
        errors.append("child JSONL contains child_exception")

    positions: list[int] = []
    for required in REQUIRED_NORMAL_EXIT_EVENTS:
        count = events.count(required)
        if count == 0:
            errors.append(f"missing critical normal-exit event: {required}")
        elif count > 1:
            errors.append(f"normal-exit event occurred {count} times: {required}")
        else:
            positions.append(events.index(required))
    if len(positions) == len(REQUIRED_NORMAL_EXIT_EVENTS) and positions != sorted(positions):
        errors.append("critical normal-exit events are out of lifecycle order")

    startup = next((row for row in rows if row.get("event") == "startup_state_observed"), None)
    if startup is not None and (
        startup.get("manager_state") != "STARTING" or startup.get("future_pending") is not True
    ):
        errors.append("normal-exit startup observation did not confirm a pending STARTING future")
    shutdown = next((row for row in rows if row.get("event") == "manager_shutdown_requested"), None)
    if shutdown is not None and (
        shutdown.get("manager_state_before") != "STARTING" or shutdown.get("future_pending") is not True
    ):
        errors.append("normal-exit shutdown was not requested during pending STARTING")
    worker = next((row for row in rows if row.get("event") == "disposal_thread_observed"), None)
    if worker is not None and (worker.get("worker_exists") is not True or worker.get("daemon") is not True):
        errors.append("normal-exit disposal worker was not confirmed as an existing daemon thread")

    at_exit = next((row for row in rows if row.get("event") == "python_interpreter_atexit"), None)
    if at_exit is not None:
        if at_exit.get("disposer_joined") is not False:
            errors.append("normal-exit child joined or did not record that it skipped joining the disposer")
        if not isinstance(at_exit.get("disposer_alive"), bool):
            errors.append("normal-exit event lacks the disposal worker's interpreter-exit state")
        if not isinstance(at_exit.get("cancel_completed"), bool):
            errors.append("normal-exit event lacks the cancellation completion state")
        if not isinstance(at_exit.get("cancel_started"), bool):
            errors.append("normal-exit event lacks the cancellation-start state")
        if at_exit.get("qt_application_alive") is not True:
            errors.append("Qt application was not confirmed alive at the interpreter-exit observation")
        if at_exit.get("cancel_completed") is True and not isinstance(at_exit.get("cancel_result"), bool):
            errors.append("completed cancellation lacks its boolean return value")
    cancel_returns = [row for row in rows if row.get("event") == "future_cancel_return"]
    cancel_starts = [row for row in rows if row.get("event") == "future_cancel_entered"]
    if at_exit is not None and at_exit.get("cancel_started") is True and len(cancel_starts) != 1:
        errors.append(f"future cancellation entry occurred {len(cancel_starts)} times, expected once")
    if at_exit is not None and at_exit.get("cancel_started") is False and cancel_starts:
        errors.append("future cancellation entry was logged but interpreter-exit state says it never started")
    if len(cancel_returns) > 1:
        errors.append(f"future cancellation return occurred {len(cancel_returns)} times, expected at most once")
    if cancel_returns and not isinstance(cancel_returns[0].get("result"), bool):
        errors.append("future cancellation return did not record a boolean result")
    if (
        at_exit is not None
        and at_exit.get("cancel_completed") is True
        and not cancel_returns
        and not isinstance(at_exit.get("cancel_result"), bool)
    ):
        errors.append("interpreter-exit event claims cancellation completed without a recorded return")
    if at_exit is not None and at_exit.get("cancel_completed") is True and at_exit.get("cancel_started") is not True:
        errors.append("interpreter-exit event says cancellation completed before it started")
    if cancel_returns and at_exit is not None:
        exit_time = at_exit.get("monotonic_ns")
        latest_return = max((row.get("monotonic_ns", 0) for row in cancel_returns), default=0)
        if isinstance(exit_time, int) and latest_return > exit_time:
            errors.append("cancellation return was recorded after the interpreter-exit observation")
        if at_exit.get("cancel_completed") is not True:
            errors.append("cancellation returned but interpreter-exit event says it was incomplete")
    if at_exit is not None and at_exit.get("cancel_completed") is False and at_exit.get("disposer_alive") is False:
        errors.append("disposer ended before interpreter exit without a recorded cancellation completion")
    return tuple(errors)


def summarize_matlab_candidates(
    baseline: Sequence[Mapping[str, Any]],
    snapshot_rows: Sequence[Mapping[str, Any]],
    final: Sequence[Mapping[str, Any]],
    *,
    child_pid: int,
    final_after_child_exit: bool = False,
) -> list[dict[str, Any]]:
    """Summarize new MATLAB identities without claiming confirmed Engine ownership."""
    baseline_matlab = [row for row in baseline if is_matlab_process(row)]
    candidates: dict[tuple[str, int | None], dict[str, Any]] = {}
    observations = [*snapshot_rows, {"phase": "final", "processes": final}]
    for snapshot in observations:
        phase = snapshot.get("phase", "unknown")
        process_rows = snapshot.get("processes", [])
        if not isinstance(process_rows, Sequence):
            continue
        for row in process_rows:
            if not isinstance(row, Mapping) or not is_matlab_process(row):
                continue
            if any(same_process_identity(row, previous) for previous in baseline_matlab):
                continue
            pid = row.get("PID")
            creation = normalize_creation_time(row.get("CreationDate", row.get("CreationUTC")))
            key = (str(pid), creation)
            candidate = candidates.setdefault(
                key,
                {
                    **dict(row),
                    "attribution": "candidate; direct parent match"
                    if row.get("ParentPID") == child_pid
                    else "UNVERIFIED",
                    "direct_child_of_diagnostic_child": row.get("ParentPID") == child_pid,
                    "observed_phases": [],
                    "first_observed_utc": snapshot.get("utc"),
                    "last_observed_utc": snapshot.get("utc"),
                    "identity_verified": pid is not None and creation is not None,
                },
            )
            candidate["last_observed_utc"] = snapshot.get("utc")
            if phase not in candidate["observed_phases"]:
                candidate["observed_phases"].append(phase)

    for candidate in candidates.values():
        present_final = any(same_process_identity(candidate, row) for row in final if is_matlab_process(row))
        candidate["present_in_final_inventory"] = present_final
        candidate["observed_after_child_exit"] = bool(
            "post_exit" in candidate["observed_phases"]
            or (final_after_child_exit and "final" in candidate["observed_phases"])
        )
        if present_final and candidate["observed_after_child_exit"]:
            candidate["disposition"] = "survived_child_exit"
        elif present_final:
            candidate["disposition"] = "present_in_final_inventory"
        elif candidate["observed_after_child_exit"]:
            candidate["disposition"] = "disappeared_during_post_exit_observation"
        else:
            candidate["disposition"] = "absent_by_first_post_exit_inventory"
    return list(candidates.values())


def normalize_creation_time(value: Any) -> int | None:
    """Normalize common PowerShell/CIM timestamp encodings to UTC nanoseconds."""
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        # Windows PowerShell ConvertTo-Json serializes DateTime as epoch milliseconds.
        return int(value * 1_000_000) if abs(value) < 100_000_000_000 else int(value) * 1_000_000
    elif isinstance(value, str):
        match = re.fullmatch(r"/?Date\((-?\d+)(?:[+-]\d+)?\)/?", value)
        if match:
            return int(match.group(1)) * 1_000_000
        dmtf = re.fullmatch(r"(\d{14})\.(\d{6})([+-])(\d{3})", value)
        if dmtf:
            parsed = dt.datetime.strptime(dmtf.group(1), "%Y%m%d%H%M%S").replace(
                microsecond=int(dmtf.group(2))
            )
            offset_minutes = int(dmtf.group(4)) * (1 if dmtf.group(3) == "+" else -1)
            parsed = parsed.replace(tzinfo=dt.timezone(dt.timedelta(minutes=offset_minutes)))
        else:
            iso = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                parsed = dt.datetime.fromisoformat(iso)
            except ValueError:
                return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return int(parsed.timestamp() * 1_000_000_000)


def same_process_identity(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """Compare PID and creation time, allowing sub-millisecond encoding precision."""
    first_pid = first.get("PID")
    second_pid = second.get("PID")
    if first_pid is None or second_pid is None:
        return False
    try:
        if int(first_pid) != int(second_pid):
            return False
    except (TypeError, ValueError):
        return False
    first_time = normalize_creation_time(first.get("CreationDate", first.get("CreationUTC")))
    second_time = normalize_creation_time(second.get("CreationDate", second.get("CreationUTC")))
    return first_time is not None and second_time is not None and abs(first_time - second_time) <= 1_000_000


def compare_process_inventories(
    baseline: Sequence[Mapping[str, Any]],
    final: Sequence[Mapping[str, Any]],
    *,
    image_names: Sequence[str] = tuple(MATLAB_PROCESS_NAMES),
) -> dict[str, list[dict[str, Any]]]:
    """Compare every baseline process by PID plus creation time, including PID reuse."""
    accepted_names = {name.lower() for name in image_names}
    initial = [row for row in baseline if str(row.get("Name", "")).lower() in accepted_names]
    current = [row for row in final if str(row.get("Name", "")).lower() in accepted_names]
    preserved: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    reused: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for previous in initial:
        if normalize_creation_time(previous.get("CreationDate", previous.get("CreationUTC"))) is None:
            unverified.append({"baseline": dict(previous), "reason": "unrecognized creation time"})
            continue
        match = next((row for row in current if same_process_identity(previous, row)), None)
        if match is not None:
            preserved.append({"baseline": dict(previous), "final": dict(match)})
            continue
        same_pid = [row for row in current if row.get("PID") == previous.get("PID")]
        if same_pid:
            reused.append({"baseline": dict(previous), "final": dict(same_pid[0])})
        else:
            missing.append(dict(previous))
    return {"preserved": preserved, "missing": missing, "pid_reused": reused, "unverified": unverified}


def launch_child(
    command: Sequence[str],
    *,
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> subprocess.Popen[bytes]:
    """Start an isolated child while preserving its unmodified stdout and stderr."""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_stream: Any | None = None
    stderr_stream: Any | None = None
    try:
        stdout_stream = stdout_path.open("wb")
        stderr_stream = stderr_path.open("wb")
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            stdout=stdout_stream,
            stderr=stderr_stream,
            close_fds=True,
        )
    finally:
        # Popen inherits the OS handles; close the supervisor's copies immediately.
        if stdout_stream is not None:
            stdout_stream.close()
        if stderr_stream is not None:
            stderr_stream.close()
