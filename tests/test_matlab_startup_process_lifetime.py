"""Fresh-process probes for MATLAB Engine shutdown ownership, without MATLAB.

A daemon can keep cleaning up while Python is alive, but its completion is not
guaranteed after interpreter exit. These tests record that distinction; they
are not tests of a real MATLAB process or the MATLAB vendor FutureResult.
"""

import os
from pathlib import Path
import subprocess
import sys


_CHILD_PROBE = r"""
import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import QCoreApplication

from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState

trace = Path(sys.argv[1])
case = sys.argv[2]


def record(event):
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
        self.release_cancel.wait(timeout=20)
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
    record("disposal_joined")

record("main_return")
"""


def _assert_matlab_shutdown_process_exit_boundary(tmp_path: Path, case: str) -> None:
    """Distinguish early Python exit from a cleanup observed before exit."""
    trace = tmp_path / f"startup-{case}.events"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    process = subprocess.run(
        [sys.executable, "-c", _CHILD_PROBE, str(trace), case],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )

    assert process.returncode == 0, f"{process.stdout}\n{process.stderr}"
    events = trace.read_text(encoding="utf-8").splitlines()
    assert events.count("shutdown_returned") == 1
    assert events.count("cancel_entered") == 1
    assert events.count("main_return") == 1
    if case == "resolved":
        assert events.count("engine_quit") == 1
        assert events.count("disposal_joined") == 1
        assert events.index("engine_quit") < events.index("main_return")
    else:
        # The child exited successfully while fake cancellation was blocked.
        # This is evidence that Python exit can abandon daemon cleanup, not
        # evidence that MATLAB itself was orphaned or that its API was invoked.
        assert "engine_quit" not in events
        assert "disposal_joined" not in events


def test_matlab_shutdown_cleans_engine_before_interpreter_exit(tmp_path: Path) -> None:
    _assert_matlab_shutdown_process_exit_boundary(tmp_path, "resolved")


def test_matlab_shutdown_cannot_guarantee_disposal_after_interpreter_exit(tmp_path: Path) -> None:
    _assert_matlab_shutdown_process_exit_boundary(tmp_path, "blocked")
