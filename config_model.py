from dataclasses import dataclass, field
from typing import Any, Dict


# A helper function to safely get typed values from the config dict
def get_typed_value(data: Dict, key: str, default: Any, target_type: type) -> Any:
    """Safely retrieves and casts a value from a dictionary."""
    value = data.get(key, default)
    try:
        return target_type(value)
    except (ValueError, TypeError):
        return default


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "lab_app.log"
    mode: str = "a"
    max_bytes: int = 5 * 1024 * 1024  # 5 MB
    backup_count: int = 3


@dataclass
class InstrumentsConfig:
    ct400_dll_path: str = ""
    tunics_gpib_address: int = 10


@dataclass
class CameraConfig:
    enabled: bool = False
    identifier: str = ""
    name: str = "Camera"
    flip_horizontal: bool = False
    # You can add more camera-specific config defaults here
    # e.g., default_exposure: float = 10000.0


@dataclass
class ScanDefaults:
    start_wavelength_nm: float = 1550.0
    end_wavelength_nm: float = 1560.0
    resolution_pm: int = 1
    speed_nm_s: int = 10
    laser_power: float = 1.0
    power_unit: str = "mW"
    input_port: int = 1
    min_wavelength_nm: float = 1440.0
    max_wavelength_nm: float = 1640.0
    safe_parking_wavelength: float = 1550.0


@dataclass
class HistogramDefaults:
    wavelength_nm: float = 1550.0
    laser_power: float = 1.0
    power_unit: str = "mW"
    input_port: int = 1
    # For the detector checkboxes, we can define them like this
    detector_1_enabled: bool = True
    detector_2_enabled: bool = True
    detector_3_enabled: bool = True
    detector_4_enabled: bool = True


@dataclass
class UIConfig:
    initial_width_ratio: float = 0.8
    initial_height_ratio: float = 0.8


@dataclass
class AppConfig:
    """The main typed configuration class for the entire application."""

    logging: LoggingConfig = field(default_factory=LoggingConfig)
    instruments: InstrumentsConfig = field(default_factory=InstrumentsConfig)
    scan_defaults: ScanDefaults = field(default_factory=ScanDefaults)
    histogram_defaults: HistogramDefaults = field(default_factory=HistogramDefaults)
    ui: UIConfig = field(default_factory=UIConfig)
    app_name: str = "IOPanel"
    cameras: Dict[str, CameraConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "AppConfig":
        """Creates an AppConfig instance from a raw dictionary loaded from config.ini."""

        # --- PARSE SECTIONS WITH EXPLICIT TYPE CONVERSION ---

        log_data = config_dict.get("Logging", {})
        logging_conf = LoggingConfig(
            level=get_typed_value(log_data, "level", "INFO", str),
            file=get_typed_value(log_data, "file", "lab_app.log", str),
            mode=get_typed_value(log_data, "mode", "a", str),
            max_bytes=get_typed_value(log_data, "max_bytes", 5242880, int),
            backup_count=get_typed_value(log_data, "backup_count", 3, int),
        )

        instr_data = config_dict.get("Instruments", {})
        instruments_conf = InstrumentsConfig(
            ct400_dll_path=get_typed_value(instr_data, "ct400_dll_path", "", str),
            tunics_gpib_address=get_typed_value(
                instr_data, "tunics_gpib_address", 10, int
            ),
        )

        scan_data = config_dict.get("ScanDefaults", {})
        scan_conf = ScanDefaults(
            start_wavelength_nm=get_typed_value(
                scan_data, "start_wavelength_nm", 1550.0, float
            ),
            end_wavelength_nm=get_typed_value(
                scan_data, "end_wavelength_nm", 1560.0, float
            ),
            resolution_pm=get_typed_value(scan_data, "resolution_pm", 1, int),
            speed_nm_s=get_typed_value(scan_data, "speed_nm_s", 10, int),
            laser_power=get_typed_value(scan_data, "laser_power", 1.0, float),
            power_unit=get_typed_value(scan_data, "power_unit", "mW", str),
            input_port=get_typed_value(scan_data, "input_port", 1, int),
            min_wavelength_nm=get_typed_value(
                scan_data, "min_wavelength_nm", 1440.0, float
            ),
            max_wavelength_nm=get_typed_value(
                scan_data, "max_wavelength_nm", 1640.0, float
            ),
        )

        hist_data = config_dict.get("HistogramDefaults", {})
        hist_conf = HistogramDefaults(
            wavelength_nm=get_typed_value(hist_data, "wavelength_nm", 1550.0, float),
            laser_power=get_typed_value(hist_data, "laser_power", 1.0, float),
            power_unit=get_typed_value(hist_data, "power_unit", "mW", str),
            input_port=get_typed_value(hist_data, "input_port", 1, int),
            detector_1_enabled=get_typed_value(
                hist_data, "detector_1_enabled", True, bool
            ),
            detector_2_enabled=get_typed_value(
                hist_data, "detector_2_enabled", True, bool
            ),
            detector_3_enabled=get_typed_value(
                hist_data, "detector_3_enabled", True, bool
            ),
            detector_4_enabled=get_typed_value(
                hist_data, "detector_4_enabled", True, bool
            ),
        )

        ui_data = config_dict.get("UI", {})
        ui_conf = UIConfig(
            initial_width_ratio=get_typed_value(
                ui_data, "initial_width_ratio", 0.8, float
            ),
            initial_height_ratio=get_typed_value(
                ui_data, "initial_height_ratio", 0.8, float
            ),
        )

        app_name = str(config_dict.get("App", {}).get("name", "IOPanel"))

        # Parse dynamic camera sections (this part was already using the helper and is correct)
        camera_configs = {}
        for section_name, section_data in config_dict.items():
            if section_name.lower().startswith("camera:"):
                cam_conf = CameraConfig(
                    enabled=get_typed_value(section_data, "enabled", False, bool),
                    identifier=get_typed_value(section_data, "identifier", "", str),
                    name=get_typed_value(section_data, "name", section_name, str),
                    flip_horizontal=get_typed_value(
                        section_data, "flip_horizontal", False, bool
                    ),
                )
                camera_configs[section_name] = cam_conf

        return cls(
            logging=logging_conf,
            instruments=instruments_conf,
            scan_defaults=scan_conf,
            histogram_defaults=hist_conf,
            ui=ui_conf,
            app_name=app_name,
            cameras=camera_configs,
        )
