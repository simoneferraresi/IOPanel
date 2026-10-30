"""Protect GUI startup from inactive views and optional operation dependencies."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_first_functional_window_defers_inactive_panels_and_dialogs(tmp_path):
    """Cold app import and a real monitor window must both retain the boundary."""
    code = f"""
import sys
import time
from pathlib import Path
import app

deferred = (
    'ui.alignment_panel', 'ui.pm5020_panel', 'ui.edfa_panel',
    'ui.shg_synchronized_panel', 'ui.appearance_dialog',
    'ui.ct400_calibration_dialog', 'ui.ct400_calibration_campaign',
    'ui.discovery_dialog', 'hardware.alignment_worker',
    'logic.pm5020_monitor', 'logic.edfa_monitor',
    'logic.simulated_synchronized_acquisition',
)
assert not set(deferred).intersection(sys.modules), set(deferred).intersection(sys.modules)
# The production monitor is immediately functional and still pays for PyQtGraph.
assert 'pyqtgraph' in sys.modules
from tools.benchmark_startup import characterize
characterize(Path({str(tmp_path / "startup.json")!r}), parent_started=time.perf_counter())
assert not set(deferred).intersection(sys.modules), set(deferred).intersection(sys.modules)
"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.mark.parametrize(
    "index,attribute,expected",
    [
        (2, "alignment_tab", {"ui.alignment_panel"}),
        (3, "pm5020_panel", {"ui.pm5020_panel"}),
        (4, "edfa_panel", {"ui.edfa_panel"}),
        (5, "shg_tab", {"ui.shg_synchronized_panel", "ui.pm5020_panel"}),
    ],
)
def test_first_panel_access_imports_only_requested_view(tmp_path, index, attribute, expected):
    """Use the bounded startup probe to own the native window and its shutdown."""
    code = f"""
import sys
import time
from pathlib import Path
import app
from tools.benchmark_startup import characterize

panels = {{'ui.alignment_panel', 'ui.pm5020_panel', 'ui.edfa_panel', 'ui.shg_synchronized_panel'}}
assert not panels.intersection(sys.modules)
class ProbeWindow(app.MainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        manager = self.matlab_engine_manager
        panel = getattr(self, {attribute!r})
        assert panel is self.tab_widget.widget({index})
        assert getattr(self, {attribute!r}) is panel
        assert self.matlab_engine_manager is manager
        assert panels.intersection(sys.modules) == {expected!r}
        assert 'matlab.engine' not in sys.modules
        assert 'pyqtgraph.opengl' not in sys.modules
app.MainWindow = ProbeWindow
characterize(Path({str(tmp_path / "startup.json")!r}), parent_started=time.perf_counter())
"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.mark.skipif(sys.platform != "win32", reason="Production cold-start import boundary validated in Windows CI")
def test_application_import_does_not_eagerly_load_scipy_io():
    """An unused MAT reader/writer must not pay import cost on every GUI launch."""
    code = (
        "import sys\n"
        "import app\n"
        "assert 'scipy.io' not in sys.modules, "
        "'SciPy MAT IO imported during app import'\n"
        "assert 'TLPMX' not in sys.modules, "
        "'Thorlabs wrapper imported during app import'\n"
        "assert 'logic.scan_import' in sys.modules\n"
        "assert 'logic.scan_export_worker' in sys.modules\n"
        "assert 'logic.power_monitor_export_worker' in sys.modules\n"
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.mark.skipif(sys.platform != "win32", reason="Production cold-start import boundary validated in Windows CI")
def test_application_import_defers_opengl_until_3d_widget_creation():
    """Ordinary plot imports stay light; explicit 3D viewer construction loads GL."""
    code = (
        "import sys\n"
        "import app\n"
        "assert 'pyqtgraph.opengl' not in sys.modules\n"
        "assert 'OpenGL.GL' not in sys.modules\n"
        "from ui.plot_widgets import PlotWidget, HistogramWidget\n"
        "assert PlotWidget and HistogramWidget\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app_qt = QApplication([])\n"
        "from ui.plot_3d_widget import Plot3DWidget\n"
        "widget = Plot3DWidget()\n"
        "assert 'pyqtgraph.opengl' in sys.modules\n"
        "assert 'OpenGL.GL' in sys.modules\n"
        "widget.close()\n"
        "app_qt.processEvents()\n"
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.mark.skipif(sys.platform != "win32", reason="Lab SDK startup boundary validated in Windows CI")
def test_matlab_engine_is_not_imported_with_regular_app_module():
    """MATLAB remains optional and is not loaded before first GUI paint."""
    code = (
        "import sys\n"
        "import app\n"
        "assert 'matlab.engine' not in sys.modules, "
        "'MATLAB Engine imported eagerly during app import'\n"
        "from ui.plot_widgets import PlotWidget\n"
        "assert PlotWidget\n"
        "assert 'matlab.engine' not in sys.modules, "
        "'MATLAB Engine imported eagerly during PlotWidget module import'\n"
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]


def test_alignment_module_import_does_not_force_scan_plotting_dependencies():
    """The inactive alignment tab must not preload the Wavelength Scan module."""
    code = (
        "import sys\n"
        "from ui.alignment_panel import AlignmentPanel\n"
        "assert AlignmentPanel is not None\n"
        "assert 'ui.plot_widgets' not in sys.modules, "
        "'AlignmentPanel still eagerly imports scan plotting'\n"
        "assert 'pyqtgraph' not in sys.modules, "
        "'AlignmentPanel still eagerly imports PyQtGraph'\n"
        "assert 'matlab.engine' not in sys.modules\n"
        "assert 'pyqtgraph.opengl' not in sys.modules\n"
    )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-4000:]
