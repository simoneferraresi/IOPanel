"""Exercise collection after the original test returns, inside native paint."""

import os
import subprocess
import sys
from pathlib import Path

from tools.qt_stall_stress_supervisor import _terminate_process_tree


def test_registered_owner_survives_post_call_paint_collection(tmp_path):
    program = """
import gc
import sys
import pytest
import pyqtgraph as pg

original = pg.ViewBox.updateMatrix
armed = False
collections = []
def update_matrix(self, *args, **kwargs):
    global armed
    if armed:
        armed = False
        gc.collect()
        collections.append(True)
    return original(self, *args, **kwargs)
pg.ViewBox.updateMatrix = update_matrix

class PaintCollection:
    @pytest.hookimpl(wrapper=True, trylast=True)
    def pytest_runtest_call(self, item):
        global armed
        result = yield
        armed = True
        return result

result = pytest.main([
    '-q', '-o', 'cache_dir=' + sys.argv[1], '--basetemp', sys.argv[2],
    'tests/test_power_monitor_trace_widget.py::test_mainwindow_completion_respects_manual_trace_visibility[False]',
], plugins=[PaintCollection()])
assert collections, 'The post-call native paint boundary was not exercised'
raise SystemExit(result)
"""
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("IOPANEL_QT_STRESS_CONFIG", None)
    env.pop("IOPANEL_QT_STRESS_CYCLES", None)
    env["PYTHONPATH"] = str(root)
    command = [sys.executable, "-X", "faulthandler", "-c", program, str(tmp_path / "cache"), str(tmp_path / "pytest")]
    stdout_path, stderr_path = tmp_path / "child.stdout.log", tmp_path / "child.stderr.log"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(command, cwd=root, env=env, stdout=stdout, stderr=stderr)
        try:
            returncode = process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process)
            raise
    output = stdout_path.read_text(encoding="utf-8") + stderr_path.read_text(encoding="utf-8")
    assert returncode == 0, f"Native paint child exited {returncode}:\n{output}"
    assert "1 passed" in output
