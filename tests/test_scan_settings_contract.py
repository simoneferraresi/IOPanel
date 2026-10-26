"""Preserve lightweight scan settings and legacy GUI imports."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from logic.scan_settings import ScanSettings

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )


def test_scan_settings_defaults_and_mutability():
    settings = ScanSettings()
    assert vars(settings) == {
        "resolution": "N/A",
        "motor_speed": "N/A",
        "laser_power": "N/A",
        "power_unit": "",
    }
    settings.resolution = "5"
    settings.motor_speed = "10"
    settings.laser_power = "3"
    settings.power_unit = "mW"
    assert vars(settings) == {
        "resolution": "5",
        "motor_speed": "10",
        "laser_power": "3",
        "power_unit": "mW",
    }
    assert ScanSettings() is not settings


def test_legacy_and_plot_imports_reexport_same_class():
    from ui.control_panel import ScanSettings as Legacy
    from ui.plot_widgets import ScanSettings as Plot

    assert ScanSettings is Legacy is Plot
    assert isinstance(Legacy(), ScanSettings)
    assert isinstance(ScanSettings(), Plot)


def test_control_panel_and_plot_widget_share_mutable_settings(qtbot):
    from config_model import AppConfig
    from ui.control_panel import CT400ControlPanel
    from ui.plot_widgets import PlotWidget

    settings = ScanSettings()
    panel = CT400ControlPanel(settings, None, AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(panel)
    plot = PlotWidget(settings)
    qtbot.addWidget(plot)
    assert panel.shared_settings is settings
    assert plot.shared_settings is settings

    panel.resolution.setText("10")
    panel.motor_speed.setText("2")
    panel.laser_power.setText("4")
    panel.update_shared_settings()
    assert (settings.resolution, settings.motor_speed, settings.laser_power) == ("10", "2", "4")
    assert settings.power_unit == panel.power_unit.currentText()
    assert plot.shared_settings.resolution == "10"


def test_plot_widget_invalid_settings_preserves_default_fallback(qtbot):
    from ui.plot_widgets import PlotWidget

    widget = PlotWidget(object())
    qtbot.addWidget(widget)
    assert type(widget.shared_settings) is ScanSettings
    assert widget.shared_settings.resolution == "N/A"


def test_canonical_import_does_not_load_gui_or_hardware():
    code = (
        "import sys\n"
        "import logic.scan_settings\n"
        "assert not any(name == 'PySide6' or name.startswith('PySide6.') for name in sys.modules)\n"
        "assert 'ui.control_panel' not in sys.modules\n"
        "assert 'ui.plot_widgets' not in sys.modules\n"
        "assert 'hardware.ct400' not in sys.modules\n"
        "assert 'vmbpy' not in sys.modules\n"
        "assert 'matlab.engine' not in sys.modules\n"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr[-4000:]


@pytest.mark.parametrize("order", ["plot-first", "control-first"])
def test_import_order_preserves_class_identity(order):
    if order == "plot-first":
        first, second = "ui.plot_widgets", "ui.control_panel"
    else:
        first, second = "ui.control_panel", "ui.plot_widgets"
    code = (
        f"import {first}\n"
        f"import {second}\n"
        "from logic.scan_settings import ScanSettings\n"
        "from ui.control_panel import ScanSettings as Legacy\n"
        "from ui.plot_widgets import ScanSettings as Plot\n"
        "assert ScanSettings is Legacy is Plot\n"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr[-4000:]


def test_plot_import_propagates_canonical_model_import_failure():
    code = (
        "import builtins\n"
        "original = builtins.__import__\n"
        "def blocked(name, *args, **kwargs):\n"
        "    if name == 'logic.scan_settings':\n"
        "        raise ImportError('injected ScanSettings dependency failure')\n"
        "    return original(name, *args, **kwargs)\n"
        "builtins.__import__ = blocked\n"
        "try:\n"
        "    import ui.plot_widgets\n"
        "except ImportError as error:\n"
        "    assert 'injected ScanSettings dependency failure' in str(error), error\n"
        "else:\n"
        "    raise AssertionError('PlotWidget swallowed the model import error')\n"
    )
    result = _run_isolated(code)
    assert result.returncode == 0, result.stderr[-4000:]
