import argparse
from pathlib import Path

import pytest
from pydantic import ValidationError

import app
from config_model import AppConfig


def test_loader_returns_parsed_ini_dictionary(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Instruments]\nct400_backend = simulation\n", encoding="utf-8")

    assert app.load_raw_config_from_ini(config_path) == {"Instruments": {"ct400_backend": "simulation"}}


def test_qsettings_identity_is_stable_and_not_the_configured_window_title():
    class MetadataCapture:
        organization = None
        application = None

        def setOrganizationName(self, value):
            self.organization = value

        def setApplicationName(self, value):
            self.application = value

        def setApplicationVersion(self, _value):
            pass

        def setStyle(self, _value):
            pass

    capture = MetadataCapture()
    app.configure_qt_application(capture, "Editable config title")

    assert capture.organization == "IOPLab"
    assert capture.application == "IOPanel"


def test_loader_raises_for_missing_file(tmp_path):
    config_path = tmp_path / "missing.ini"

    with pytest.raises(app.ConfigLoadError, match="File does not exist") as error:
        app.load_raw_config_from_ini(config_path)

    assert isinstance(error.value.__cause__, FileNotFoundError)
    assert str(config_path) in str(error.value)


def test_loader_raises_for_malformed_ini(tmp_path):
    config_path = tmp_path / "broken.ini"
    config_path.write_text("[Instruments\nct400_backend = simulation\n", encoding="utf-8")

    with pytest.raises(app.ConfigLoadError, match="INI parsing failed") as error:
        app.load_raw_config_from_ini(config_path)

    assert error.value.__cause__ is not None


def test_loader_raises_for_read_oserror(tmp_path, monkeypatch):
    config_path = tmp_path / "unreadable.ini"
    original_read_text = Path.read_text

    def fail_read(path, *args, **kwargs):
        if path == config_path:
            raise OSError("access denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read)

    with pytest.raises(app.ConfigLoadError, match="Could not read file: access denied") as error:
        app.load_raw_config_from_ini(config_path)

    assert isinstance(error.value.__cause__, OSError)


def _prepare_main(monkeypatch, config_path):
    messages = []
    constructed_windows = []

    class ExistingApplication:
        @staticmethod
        def instance():
            return object()

    monkeypatch.setattr(app, "QApplication", ExistingApplication)
    args = argparse.Namespace(config=config_path, log_level=None, log_file=None)
    monkeypatch.setattr(app, "parse_args", lambda: args)
    monkeypatch.setattr(app.QMessageBox, "critical", lambda *args: messages.append(args))
    monkeypatch.setattr(app, "MainWindow", lambda **kwargs: constructed_windows.append(kwargs))
    return messages, constructed_windows


@pytest.mark.parametrize("malformed", [False, True], ids=["missing", "malformed"])
def test_main_stops_on_config_load_failure_before_window_or_validation(tmp_path, monkeypatch, malformed):
    config_path = tmp_path / ("broken.ini" if malformed else "missing.ini")
    if malformed:
        config_path.write_text("[Instruments\nct400_backend = simulation\n", encoding="utf-8")
    messages, constructed_windows = _prepare_main(monkeypatch, config_path)
    monkeypatch.setattr(
        AppConfig,
        "from_ini_dict",
        classmethod(lambda *_args: pytest.fail("schema validation must not run after a load failure")),
    )

    assert app.main() == 1
    assert len(messages) == 1
    assert str(config_path) in messages[0][-1]
    assert "could not be loaded" in messages[0][-1]
    assert constructed_windows == []
    if malformed:
        assert "INI parsing failed" in messages[0][-1]
    else:
        assert "File does not exist" in messages[0][-1]


def test_main_keeps_schema_validation_failure_separate(tmp_path, monkeypatch):
    config_path = tmp_path / "invalid.ini"
    config_path.write_text("[ScanDefaults]\nresolution_pm = 0\n", encoding="utf-8")
    messages, constructed_windows = _prepare_main(monkeypatch, config_path)

    assert app.main() == 1
    assert len(messages) == 1
    assert "is invalid" in messages[0][-1]
    assert "could not be loaded" not in messages[0][-1]
    assert constructed_windows == []


def test_valid_simulation_ini_keeps_optional_model_defaults(tmp_path):
    config_path = tmp_path / "simulation.ini"
    config_path.write_text("[Instruments]\nct400_backend = simulation\n", encoding="utf-8")

    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))

    assert config.instruments.ct400_backend == "simulation"
    assert config.logging.level == "INFO"
    assert config.scan_defaults.input_port == 1
    assert config.histogram_defaults.input_port == 1


def test_valid_physical_style_ini_keeps_omitted_backend_default(tmp_path):
    config_path = tmp_path / "physical-style.ini"
    config_path.write_text("[Instruments]\nct400_dll_path = lab.dll\n", encoding="utf-8")

    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))

    assert config.instruments.ct400_backend == "physical"


def test_main_stops_on_config_semantic_failure_before_window(tmp_path, monkeypatch):
    config_path = tmp_path / "typo.ini"
    config_path.write_text("[Instrument]\nct400_backend = simulation\n", encoding="utf-8")
    messages, constructed_windows = _prepare_main(monkeypatch, config_path)

    assert app.main() == 1
    assert len(messages) == 1
    assert "Configuration Error" in messages[0][1]
    assert str(config_path) in messages[0][-1]
    assert "Instrument" in messages[0][-1]
    assert constructed_windows == []


def test_parseable_schema_invalid_ini_raises_pydantic_validation_error(tmp_path):
    config_path = tmp_path / "invalid.ini"
    config_path.write_text("[ScanDefaults]\nresolution_pm = 0\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))
