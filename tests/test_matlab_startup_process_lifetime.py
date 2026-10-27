"""Fresh-process probes for MATLAB Engine shutdown ownership, without MATLAB.

Required cleanup must keep Python alive until its controlled fake native call
returns. These probes observe real interpreter exit; they never launch MATLAB.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.qt_stall_stress_supervisor import _terminate_process_tree

_CHILD_PROBE = r"""
import sys
from pathlib import Path
from threading import Event, Lock

from PySide6.QtCore import QCoreApplication

from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState

trace = Path(sys.argv[1])
case = sys.argv[2]
trace_lock = Lock()


def record(event):
    # Main and disposer threads can write simultaneously. Event records must
    # be complete and ordered under a lock, even when Python exits promptly.
    with trace_lock:
        with trace.open("a", encoding="utf-8") as output:
            output.write(event + "\n")
            output.flush()


class Engine:
    def quit(self):
        record("engine_quit")


class Future:
    def __init__(self):
        self.cancel_entered = Event()
        self.release_cancel = Event()

    def cancel(self):
        record("cancel_entered")
        self.cancel_entered.set()
        if case == "blocked":
            assert sys.stdin.readline().strip() == "release"
        else:
            assert self.release_cancel.wait(timeout=4)
        return False

    def result(self):
        record("result_called")
        return engine

    def done(self):
        return False


app = QCoreApplication([])
engine = Engine()
future = Future()
manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: future)
manager.prewarm()
assert manager.state is MatlabEngineState.STARTING
manager.shutdown()
assert manager.state is MatlabEngineState.SHUTTING_DOWN
record("shutdown_returned")

if not future.cancel_entered.wait(timeout=4):
    raise RuntimeError("Disposal thread did not enter cancel()")

if case == "resolved":
    future.release_cancel.set()
    disposer = manager._startup_disposal_thread
    assert disposer is not None
    disposer.join(timeout=4)
    if disposer.is_alive():
        raise RuntimeError("Released disposal thread did not finish")
    manager._poll_disposals()
    assert manager.shutdown_finished
    record("disposal_joined")

record("main_return")
"""


def _assert_matlab_shutdown_process_exit_boundary(tmp_path: Path, case: str, qtbot) -> None:
    """Observe that even main-return cannot abandon required cleanup."""
    trace = tmp_path / f"startup-{case}.events"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with subprocess.Popen(
        [sys.executable, "-c", _CHILD_PROBE, str(trace), case],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            qtbot.waitUntil(
                lambda: (
                    (trace.exists() and "main_return" in trace.read_text(encoding="utf-8"))
                    or process.poll() is not None
                ),
                timeout=6000,
            )
            if case == "blocked":
                assert process.poll() is None
                with pytest.raises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.25)
            stdout, stderr = process.communicate(input="release\n" if case == "blocked" else None, timeout=8)
        finally:
            if process.poll() is None:
                _terminate_process_tree(process)
                process.communicate(timeout=5)
    assert process.returncode == 0, stdout + stderr
    events = trace.read_text(encoding="utf-8").splitlines()
    assert events.count("shutdown_returned") == 1
    assert events.count("cancel_entered") == 1
    assert events.count("main_return") == 1
    assert events.count("engine_quit") == 1
    if case == "resolved":
        assert events.count("disposal_joined") == 1
        assert events.index("engine_quit") < events.index("main_return")
    else:
        assert events.index("main_return") < events.index("engine_quit")


def test_matlab_shutdown_cleans_engine_before_interpreter_exit(tmp_path: Path, qtbot) -> None:
    _assert_matlab_shutdown_process_exit_boundary(tmp_path, "resolved", qtbot)


def test_matlab_shutdown_keeps_process_alive_until_disposal_returns(tmp_path: Path, qtbot) -> None:
    _assert_matlab_shutdown_process_exit_boundary(tmp_path, "blocked", qtbot)
