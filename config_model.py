from typing import Literal

from pydantic import BaseModel, Field

# --- Pydantic Models for each configuration section ---


class LoggingConfig(BaseModel):
    """Configuration for the application's logging behavior."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    file: str = "lab_app.log"
    max_bytes: int = Field(5 * 1024 * 1024, gt=0, description="Max log file size in bytes must be positive")
    backup_count: int = Field(3, ge=0, description="Log backup count must be non-negative")


class InstrumentsConfig(BaseModel):
    """Configuration for physical hardware addresses and paths."""

    ct400_dll_path: str = ""
    tunics_gpib_address: int = Field(10, ge=0, le=30, description="GPIB address must be between 0 and 30")
    tunics_laser_type: str = "LS_TunicsT100s_HP"


class CameraConfig(BaseModel):
    """Configuration for a single camera instance."""

    enabled: bool = False
    name: str
    flip_horizontal: bool = False


class ScanDefaults(BaseModel):
    """Default parameters for the CT400 Wavelength Scan panel."""

    start_wavelength_nm: float = 1550.0
    end_wavelength_nm: float = 1560.0
    resolution_pm: int = Field(1, gt=0)
    speed_nm_s: int = Field(10, gt=0)
    laser_power: float = 1.0
    power_unit: Literal["mW", "dBm"] = "mW"
    input_port: Literal[1, 2, 3, 4] = 1
    min_wavelength_nm: float = 1440.0
    max_wavelength_nm: float = 1640.0
    safe_parking_wavelength: float = 1550.0


class HistogramDefaults(BaseModel):
    """Default parameters for the Power Monitor (Histogram) panel."""

    wavelength_nm: float = 1550.0
    laser_power: float = 1.0
    power_unit: Literal["mW", "dBm"] = "mW"
    input_port: Literal[1, 2, 3, 4] = 1
    detector_1_enabled: bool = True
    detector_2_enabled: bool = True
    detector_3_enabled: bool = True
    detector_4_enabled: bool = True


class UIConfig(BaseModel):
    """Configuration for general user interface behavior."""

    initial_width_ratio: float = Field(0.8, gt=0.1, le=1.0)
    initial_height_ratio: float = Field(0.8, gt=0.1, le=1.0)


class AppConfig(BaseModel):
    """The main typed configuration class for the entire application."""

    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    instruments: InstrumentsConfig = Field(default_factory=InstrumentsConfig)
    scan_defaults: ScanDefaults = Field(default_factory=ScanDefaults)
    histogram_defaults: HistogramDefaults = Field(default_factory=HistogramDefaults)
    ui: UIConfig = Field(default_factory=UIConfig)
    app_name: str = "IOPanel"
    cameras: dict[str, CameraConfig] = Field(default_factory=dict)

    @classmethod
    def from_ini_dict(cls, config_dict: dict) -> "AppConfig":
        """
        Creates an AppConfig instance from a raw dictionary loaded from config.ini.
        This method intelligently handles dynamic [Camera:...] sections and correctly
        maps other sections to their corresponding Pydantic models.
        It uses the 'identifier' field within each camera section as the key
        for the resulting 'cameras' dictionary.
        """
        init_data = {}
        cameras_data = {}
        for section_name, section_data in config_dict.items():
            section_lower = section_name.lower()
            if section_lower.startswith("camera:"):
                # Extract the identifier from the section data. This is crucial.
                identifier = section_data.get("identifier")
                if not identifier:
                    # logger.warning(f"Skipping camera section '{section_name}': missing 'identifier' field.")
                    continue

                # We can now remove 'identifier' from the data passed to the model,
                # as it's now the key. This prevents "unexpected keyword argument" errors.
                config_for_model = section_data.copy()
                config_for_model.pop("identifier", None)

                # Create the CameraConfig object and store it under its proper identifier key.
                cameras_data[identifier] = CameraConfig(**config_for_model)

            elif section_lower == "app":
                init_data["app_name"] = section_data.get("name", "IOPanel")
            else:
                if section_lower in cls.model_fields:
                    init_data[section_lower] = section_data

        init_data["cameras"] = cameras_data
        return cls(**init_data)
