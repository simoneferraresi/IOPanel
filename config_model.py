"""Defines the type-safe configuration models for the application.

This module uses Pydantic to create strongly-typed data models that represent
the structure of the `config.ini` file. This approach provides several key
benefits:
1.  **Validation:** Automatically validates the configuration on load,
    preventing runtime errors from typos or incorrect data types.
2.  **Type-Safety:** Enables static analysis and autocompletion in IDEs.
3.  **Default Values:** Provides a single source of truth for default settings.
4.  **Clear Schema:** The models themselves serve as clear, enforceable
    documentation for the application's configuration structure.

The main class, `AppConfig`, aggregates all other configuration models and
includes a class method `from_ini_dict` to adapt the structure read by
Python's `configparser` into a format Pydantic can validate.
"""

from typing import Literal

from pydantic import BaseModel, Field


class LoggingConfig(BaseModel):
    """Configuration for the application's logging behavior."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="The minimum level of log messages to record."
    )
    file: str = Field(default="lab_app.log", description="The path to the log file.")
    max_bytes: int = Field(default=5 * 1024 * 1024, gt=0, description="Maximum log file size in bytes before rotation.")
    backup_count: int = Field(default=3, ge=0, description="Number of old log files to keep after rotation.")


class InstrumentsConfig(BaseModel):
    """Configuration for physical hardware addresses and driver paths."""

    ct400_dll_path: str = Field(default="", description="The absolute path to the CT400_lib.dll file.")
    tunics_gpib_address: int = Field(
        default=10, ge=0, le=30, description="The GPIB address of the Tunics laser source (0-30)."
    )
    tunics_laser_type: str = Field(
        default="LS_TunicsT100s_HP",
        description="The model type of the Tunics laser, corresponding to a LaserSource enum member.",
    )


class CameraConfig(BaseModel):
    """Configuration for a single camera instance.

    Each camera to be used by the application should have its own corresponding
    `[Camera:Name]` section in the `config.ini` file.
    """

    identifier: str = Field(
        description="Unique Vimba ID for the camera (e.g., 'DEV_...'). This is the primary key used to open the device."
    )
    enabled: bool = Field(
        default=False, description="If True, the application will attempt to initialize this camera on startup."
    )
    name: str = Field(description="A user-friendly name for display in the GUI.")
    flip_horizontal: bool = Field(
        default=False, description="If True, the camera's video feed will be flipped horizontally."
    )


class ScanDefaults(BaseModel):
    """Default parameters for the CT400 Wavelength Scan panel."""

    start_wavelength_nm: float = Field(
        default=1550.0, description="Default starting wavelength for a scan, in nanometers."
    )
    end_wavelength_nm: float = Field(default=1560.0, description="Default ending wavelength for a scan, in nanometers.")
    resolution_pm: int = Field(default=1, gt=0, description="Default scan resolution step, in picometers.")
    speed_nm_s: int = Field(default=10, gt=0, description="Default laser sweep speed, in nanometers per second.")
    laser_power: float = Field(default=1.0, description="Default laser power value.")
    power_unit: Literal["mW", "dBm"] = Field(default="mW", description="Default unit for laser power ('mW' or 'dBm').")
    input_port: Literal[1, 2, 3, 4] = Field(default=1, description="Default laser input port on the CT400 (1-4).")
    min_wavelength_nm: float = Field(
        default=1440.0, description="The minimum wavelength boundary for the connected laser."
    )
    max_wavelength_nm: float = Field(
        default=1640.0, description="The maximum wavelength boundary for the connected laser."
    )
    safe_parking_wavelength: float = Field(
        default=1550.0, description="Wavelength to set the laser to when disabling or stopping operations."
    )


class HistogramDefaults(BaseModel):
    """Default parameters for the Power Monitor (Histogram) panel."""

    wavelength_nm: float = Field(default=1550.0, description="Default wavelength for monitoring, in nanometers.")
    laser_power: float = Field(default=1.0, description="Default laser power value for monitoring.")
    power_unit: Literal["mW", "dBm"] = Field(default="mW", description="Default unit for laser power ('mW' or 'dBm').")
    input_port: Literal[1, 2, 3, 4] = Field(
        default=1, description="Default laser input port on the CT400 for monitoring (1-4)."
    )
    detector_1_enabled: bool = Field(default=True, description="Default state for detector 1 checkbox.")
    detector_2_enabled: bool = Field(default=True, description="Default state for detector 2 checkbox.")
    detector_3_enabled: bool = Field(default=True, description="Default state for detector 3 checkbox.")
    detector_4_enabled: bool = Field(default=True, description="Default state for detector 4 checkbox.")


class UIConfig(BaseModel):
    """Configuration for general user interface behavior."""

    initial_width_ratio: float = Field(
        default=0.8,
        gt=0.1,
        le=1.0,
        description="Initial window width as a ratio of the screen's available width (0.1 to 1.0).",
    )
    initial_height_ratio: float = Field(
        default=0.8,
        gt=0.1,
        le=1.0,
        description="Initial window height as a ratio of the screen's available height (0.1 to 1.0).",
    )


class AppConfig(BaseModel):
    """The main typed configuration class for the entire application.

    This model aggregates all other configuration sections into a single,
    validated object. It is the primary configuration object used throughout
    the application.
    """

    app_name: str = Field(default="IOPanel", alias="name", description="The application name, read from [App] section.")
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    instruments: InstrumentsConfig = Field(default_factory=InstrumentsConfig)
    scan_defaults: ScanDefaults = Field(default_factory=ScanDefaults, alias="scandefaults")
    histogram_defaults: HistogramDefaults = Field(default_factory=HistogramDefaults, alias="histogramdefaults")
    ui: UIConfig = Field(default_factory=UIConfig)
    cameras: dict[str, CameraConfig] = Field(default_factory=dict)

    @classmethod
    def from_ini_dict(cls, config_dict: dict) -> "AppConfig":
        """Creates an AppConfig instance from a raw dictionary from config.ini.

        This method acts as an adapter, transforming the dictionary structure
        produced by Python's `configparser` into the nested structure that
        Pydantic expects for validation. It specifically handles the mapping of
        `[section]` names to model field names and parses the dynamic
        `[Camera:...]` sections into a dictionary of CameraConfig objects.

        Args:
            config_dict: A dictionary where keys are section names from the INI
                file and values are dictionaries of key-value pairs within
                that section.

        Returns:
            A fully validated AppConfig instance.

        Raises:
            pydantic.ValidationError: If the provided configuration data
                does not conform to the model schema.
        """
        init_data = {}
        cameras_data = {}

        # First, populate the data for top-level models
        for section_name, section_data in config_dict.items():
            section_lower = section_name.lower()

            if section_lower.startswith("camera:"):
                continue  # Handle cameras in the next loop

            if section_lower == "app":
                init_data["app_name"] = section_data.get("name", "IOPanel")
                continue

            # Check if the section name directly matches a field name
            if section_lower in cls.model_fields:
                init_data[section_lower] = section_data
                continue

            # If not, check if it matches a field's alias
            found_field = False
            for field_name, field_info in cls.model_fields.items():
                if field_info.alias and field_info.alias == section_lower:
                    init_data[field_name] = section_data
                    found_field = True
                    break
            if not found_field:
                # You can log a warning here for unrecognized sections if you wish
                pass

        # Second, specifically parse the camera sections
        for section_name, section_data in config_dict.items():
            if section_name.lower().startswith("camera:"):
                identifier = section_data.get("identifier")
                if not identifier:
                    # In a real app, you would log this warning
                    print(f"Warning: Skipping camera section '{section_name}': missing 'identifier' field.")
                    continue
                # The dictionary key for `AppConfig.cameras` is the identifier.
                cameras_data[identifier] = CameraConfig(**section_data)

        init_data["cameras"] = cameras_data

        # Finally, validate the prepared dictionary
        return cls(**init_data)
