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
