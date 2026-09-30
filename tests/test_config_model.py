import pytest
from pydantic import ValidationError

from config_model import AppConfig, ConfigSemanticError


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
    assert isinstance(config.scan_defaults.input_port, int)


def test_defaults_are_preserved_and_invalid_input_port_is_rejected():
    config = AppConfig()
    assert config.app_name == "IOPanel"
    assert config.scan_defaults.input_port == 1
    assert config.histogram_defaults.input_port == 1

    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"HistogramDefaults": {"input_port": "5"}})


def test_empty_parsed_config_is_rejected_but_explicit_defaults_remain_available():
    assert AppConfig().instruments.ct400_backend == "physical"

    with pytest.raises(ConfigSemanticError):
        AppConfig.from_ini_dict({})


def test_unknown_top_level_section_is_rejected_with_section_name():
    with pytest.raises(ConfigSemanticError, match="Instrument"):
        AppConfig.from_ini_dict({"Instrument": {"ct400_backend": "simulation"}})


def test_recognized_sections_remain_case_insensitive():
    config = AppConfig.from_ini_dict({"iNsTrUmEnTs": {"ct400_backend": "simulation"}})

    assert config.instruments.ct400_backend == "simulation"


def test_camera_section_without_identifier_is_rejected_with_section_name():
    for camera_data in (
        {"enabled": "true", "name": "Top camera"},
        {"identifier": "", "enabled": "true", "name": "Top camera"},
        {"identifier": "   ", "enabled": "true", "name": "Top camera"},
    ):
        with pytest.raises(ConfigSemanticError, match="Camera:Top"):
            AppConfig.from_ini_dict(
                {
                    "Instruments": {"ct400_backend": "simulation"},
                    "Camera:Top": camera_data,
                }
            )


def test_duplicate_camera_identifiers_are_rejected_with_identifier():
    with pytest.raises(ConfigSemanticError, match="same-id") as error:
        AppConfig.from_ini_dict(
            {
                "Camera:Top": {"identifier": "same-id", "name": "Top"},
                "Camera:Side": {"identifier": "same-id", "name": "Side"},
            }
        )
    assert "Camera:Top" in str(error.value)
    assert "Camera:Side" in str(error.value)


def test_distinct_camera_identifiers_load_correctly():
    config = AppConfig.from_ini_dict(
        {
            "Camera:Top": {"identifier": "top-id", "name": "Top"},
            "Camera:Side": {"identifier": "side-id", "name": "Side"},
        }
    )

    assert set(config.cameras) == {"top-id", "side-id"}


def test_unknown_keys_inside_recognized_sections_remain_tolerated():
    config = AppConfig.from_ini_dict({"Logging": {"level": "INFO", "mode": "a"}})

    assert config.logging.level == "INFO"


def test_camera_typed_validation_remains_pydantic_validation_error():
    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"Camera:Top": {"identifier": "top-id"}})
