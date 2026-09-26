import pytest
from pydantic import ValidationError

from config_model import AppConfig


def test_scan_defaults_ini_alias_is_applied():
    config = AppConfig.from_ini_dict({"ScanDefaults": {"start_wavelength_nm": "1530.0"}})

    assert config.scan_defaults.start_wavelength_nm == 1530.0


def test_histogram_defaults_ini_alias_is_applied():
    config = AppConfig.from_ini_dict({"HistogramDefaults": {"wavelength_nm": "1545.0"}})

    assert config.histogram_defaults.wavelength_nm == 1545.0


def test_app_name_override_uses_field_alias():
    config = AppConfig.from_ini_dict({"App": {"name": "Lab Control"}})

    assert config.app_name == "Lab Control"


def test_valid_string_input_port_is_converted_to_integer():
    config = AppConfig.from_ini_dict({"ScanDefaults": {"input_port": "3"}})

    assert config.scan_defaults.input_port == 3
    assert type(config.scan_defaults.input_port) is int


def test_defaults_are_preserved_and_invalid_input_port_is_rejected():
    config = AppConfig.from_ini_dict({})
    assert config.app_name == "IOPanel"
    assert config.scan_defaults.input_port == 1
    assert config.histogram_defaults.input_port == 1

    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"HistogramDefaults": {"input_port": "5"}})
