"""Failure-transparent JSONL logging and child supervision helpers.

These helpers do not import MATLAB or application modules. Live diagnostic
entry points import those dependencies only in their isolated child process.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import queue
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

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._io_errors: list[str] = []
        self._closed = False
        self._thread = threading.Thread(target=self._write_loop, name="JsonlEventWriter", daemon=False)
        self._thread.start()

    @property
    def io_errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._io_errors)

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
    worker.join(timeout=remaining_s)
    alive = bool(worker.is_alive())
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


def diagnostic_exit_code(
    child_return_code: int | None,
    *,
    snapshot_errors: Sequence[str] = (),
    writer_errors: Sequence[str] = (),
) -> int:
    """Return success only when the child and evidence collection both succeeded."""
    return 0 if child_return_code == 0 and not snapshot_errors and not writer_errors else 1


def monitor_child(
    process: ProcessLike,
    *,
    writer: EventWriter,
    snapshot: Callable[[], Sequence[Mapping[str, Any]]],
    snapshot_path: Path,
    shutdown_requested: Callable[[], float | None],
    max_after_shutdown_s: float = 150.0,
    post_exit_observation_s: float = 60.0,
    interval_s: float = 1.0,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], Any] | None = None,
) -> MonitorResult:
    """Monitor without terminating the child; continue monitoring after failure."""
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    wait_for_event = wait or _default_wait
    snapshot_errors: list[str] = []
    snapshots_after_exit = 0
    return_code: int | None = None
    deadline_reached = False

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
        if requested_at is not None and monotonic() - requested_at >= max_after_shutdown_s:
            deadline_reached = True
            writer.emit("child_deadline_reached_left_running", pid=process.pid, return_code=None)
            take_snapshot("deadline")
            return MonitorResult(process.pid, None, True, snapshots_after_exit, tuple(snapshot_errors))
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
    stdout_stream = stdout_path.open("wb")
    stderr_stream = stderr_path.open("wb")
    try:
        return subprocess.Popen(
            list(command),
            cwd=cwd,
            stdout=stdout_stream,
            stderr=stderr_stream,
            close_fds=True,
        )
    finally:
        # Popen inherits the OS handles; close the supervisor's copies immediately.
        stdout_stream.close()
        stderr_stream.close()
