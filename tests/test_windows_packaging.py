from pathlib import Path

from PySide6.QtCore import QSettings

import app
from app_settings import AppSettings
from config_model import AppConfig


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
