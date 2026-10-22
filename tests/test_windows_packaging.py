import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

import app
from app_settings import AppSettings
from config_model import AppConfig
from tools.qt_stall_stress_supervisor import _terminate_process_tree


def test_source_paths_keep_working_directory_semantics(tmp_path, monkeypatch):
    monkeypatch.delattr(app.sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)

    assert app.resolve_config_path(None) == Path("config.ini")
    assert app.resolve_config_path(Path("custom.ini")) == Path("custom.ini")
    assert app.resolve_log_path(Path("lab_app.log")) == Path("lab_app.log")


def test_frozen_config_defaults_beside_executable_and_honors_absolute_override(tmp_path, monkeypatch):
    executable = tmp_path / "dist" / "IOPanel.exe"
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "executable", str(executable))

    assert app.resolve_config_path(None) == executable.parent / "config.ini"
    assert app.resolve_config_path(Path("alternate.ini")) == executable.parent / "alternate.ini"
    absolute = tmp_path / "approved.ini"
    assert app.resolve_config_path(absolute) == absolute


def test_frozen_relative_logs_use_per_user_directory_and_absolute_override_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    assert app.resolve_log_path(Path("lab_app.log")) == tmp_path / "LocalAppData" / "IOPanel" / "lab_app.log"
    absolute = tmp_path / "custom.log"
    assert app.resolve_log_path(absolute) == absolute


def test_frozen_export_defaults_use_writable_per_user_output_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr("app_settings.sys.frozen", True, raising=False)
    monkeypatch.setattr("app_settings.sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))

    assert settings.scan_export_directory() == tmp_path / "LocalAppData" / "IOPanel" / "output"
    assert settings.scan_export_directory().is_dir()

    # The normal backend is redirected by the test fixture, so this checks the
    # smoke boundary without touching the operator's actual Windows registry.
    normal = app.create_application_settings(smoke_test=False, log_file=tmp_path / "normal.log")
    normal.set_application_icon("prism_spectrum")
    normal.sync()
    original = {key: normal._settings.value(key) for key in normal._settings.allKeys()}
    smoke = app.create_application_settings(smoke_test=True, log_file=tmp_path / "smoke.log")
    assert smoke.application_icon() == "optical_burst"
    assert smoke._settings.format() == QSettings.Format.IniFormat
    assert Path(smoke._settings.fileName()) == tmp_path / "smoke-settings.ini"
    assert smoke._settings.fileName() != normal._settings.fileName()
    smoke.set_active_tab(2)
    smoke.sync()
    assert {key: normal._settings.value(key) for key in normal._settings.allKeys()} == original


def test_smoke_mode_requires_simulation_for_ct400_and_enabled_cameras():
    simulation = AppConfig.from_ini_dict(
        {
            "Instruments": {"ct400_backend": "simulation"},
            "Camera:Sim": {"identifier": "sim-1", "name": "Sim", "enabled": "true", "backend": "simulation"},
        }
    )
    physical_ct400 = AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "physical"}})
    physical_camera = AppConfig.from_ini_dict(
        {
            "Instruments": {"ct400_backend": "simulation"},
            "Camera:Real": {"identifier": "DEV_1", "name": "Real", "enabled": "true", "backend": "vimba"},
        }
    )
    disabled_physical_camera = AppConfig.from_ini_dict(
        {
            "Instruments": {"ct400_backend": "simulation"},
            "Camera:Disabled": {"identifier": "DEV_1", "name": "Disabled", "enabled": "false", "backend": "vimba"},
        }
    )

    assert app.is_simulation_smoke_config(simulation)
    assert not app.is_simulation_smoke_config(physical_ct400)
    assert not app.is_simulation_smoke_config(physical_camera)
    assert app.is_simulation_smoke_config(disabled_physical_camera)


def test_distribution_template_is_explicitly_driver_free():
    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(Path("packaging/config.simulation.ini")))

    assert app.is_simulation_smoke_config(config)
    assert config.instruments.ct400_dll_path == ""
    assert len(config.cameras) == 1
    camera = next(iter(config.cameras.values()))
    assert camera.enabled and camera.backend == "simulation"
    assert not camera.identifier.startswith("DEV_")


def test_smoke_image_save_failure_shuts_down_real_window(tmp_path):
    # This must be a fresh application process: replacing QApplication inside
    # pytest-qt's shared session cannot exercise real event-loop termination.
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
import app

windows = []
original_window = app.MainWindow
def construct(*args, **kwargs):
    window = original_window(*args, **kwargs)
    windows.append(window)
    return window
app.MainWindow = construct
sys.argv = ['app.py', '--smoke-test', '--config', 'packaging/config.simulation.ini',
            '--log-file', str(Path(sys.argv[1]) / 'smoke.log')]
with patch.object(QPixmap, 'save', return_value=False):
    result = app.main()
sys.excepthook = sys.__excepthook__
window = windows[0]
closed_at_return = window._shutdown_complete
running_at_return = any(thread.isRunning() for thread in window.findChildren(QThread)
                        if shiboken6.isValid(thread))
try:
    assert result == 5, result
    assert closed_at_return, 'smoke error returned with an unclosed MainWindow'
    assert not running_at_return, 'smoke error returned with live native window threads'
finally:
    # Also clean up when testing the old implementation's failing invariant.
    window.close()
    window.deleteLater()
    QApplication.instance().processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
print('SMOKE_ERROR_SHUTDOWN_OK')
"""
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    with subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as child:
        try:
            stdout, stderr = child.communicate(timeout=40)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(child)
            stdout, stderr = child.communicate(timeout=10)
            pytest.fail("Smoke error child timed out:\n" + (stdout + stderr).decode(errors="replace"))
        assert child.returncode == 0, (stdout + stderr).decode(errors="replace")
        assert b"SMOKE_ERROR_SHUTDOWN_OK" in stdout
