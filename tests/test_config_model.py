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
    assert config.instruments.pm5020_backend == "simulation"
    assert config.instruments.pm5020_resource_name == ""
    assert config.instruments.pm5020_wrapper_path == ""
    assert config.instruments.pm5020_dll_path == ""

    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"HistogramDefaults": {"input_port": "5"}})


@pytest.mark.parametrize("backend", ["physical", "simulation"])
def test_pm5020_backend_mode_is_validated_and_older_ini_defaults_to_simulation(backend):
    legacy = AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "simulation"}})
    configured = AppConfig.from_ini_dict({"Instruments": {"pm5020_backend": backend}})
    assert legacy.instruments.pm5020_backend == "simulation"
    assert configured.instruments.pm5020_backend == backend

    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"Instruments": {"pm5020_backend": "auto"}})


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


def test_edfa_config_defaults_are_inert_and_legacy_ini_still_parses():
    old = AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "simulation"}})
    assert old.instruments.edfa_backend == "simulation"
    assert old.instruments.edfa_gpib_resource_name == ""
    assert old.instruments.edfa_gpib_timeout_ms == 3000
    assert old.instruments.edfa_gpib_write_termination == "none"


def test_edfa_readonly_gpib_config_is_explicit_and_validated():
    valid = AppConfig.from_ini_dict(
        {
            "Instruments": {
                "edfa_backend": "physical",
                "edfa_gpib_resource_name": "GPIB0::12::INSTR",
                "edfa_gpib_timeout_ms": "5000",
                "edfa_gpib_write_termination": "lf",
                "edfa_gpib_read_termination": "crlf",
            }
        }
    )
    assert valid.instruments.edfa_gpib_resource_name == "GPIB0::12::INSTR"
    assert valid.instruments.edfa_gpib_timeout_ms == 5000
    assert valid.instruments.edfa_gpib_write_termination == "lf"
    for bad in ["GPIB0::31::INSTR", "ASRL1::INSTR", "GPIB0::12::INSTR;SOUR:POW:STAT 1"]:
        with pytest.raises(ValidationError):
            AppConfig.from_ini_dict({"Instruments": {"edfa_gpib_resource_name": bad}})
    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"Instruments": {"edfa_gpib_timeout_ms": "0"}})
    with pytest.raises(ValidationError):
        AppConfig.from_ini_dict({"Instruments": {"edfa_gpib_read_termination": "anything"}})
