"""Protect the hardware-free GUI import path from eager MAT-only SciPy imports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


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
