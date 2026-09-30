"""Immutable acquisition request and completed CT400 scan snapshot."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput


@dataclass(frozen=True)
class ScanAcquisitionSettings:
    requested_start_wavelength_nm: float
    requested_end_wavelength_nm: float
    requested_resolution_pm: int
    requested_speed_nm_s: str
    entered_laser_power: str
    entered_laser_power_unit: str
    laser_power_mw: float
    laser_input: LaserInput
    detectors: tuple[Detector, ...]


@dataclass(frozen=True)
class ScanMeasurement:
    """Completed scan result; ``completed_at_utc`` marks result assembly after retrieval."""

    settings: ScanAcquisitionSettings
    wavelengths_nm: np.ndarray
    detector_data: np.ndarray
    detectors: tuple[Detector, ...]
    final_pout: float | None
    result_kind: CT400ScanResultKind
    raw_result_code: int
    result_message: str
    backend: str
    simulated: bool
    completed_at_utc: datetime
    detector_unit: str | None = None  # Vendor dB/dBm meaning remains unverified.

    def __post_init__(self):
        if self.completed_at_utc.tzinfo is None:
            raise ValueError("completed_at_utc must be timezone-aware")
        wavelengths = np.array(self.wavelengths_nm, copy=True)
        detector_data = np.array(self.detector_data, copy=True)
        wavelengths.setflags(write=False)
        detector_data.setflags(write=False)
        object.__setattr__(self, "wavelengths_nm", wavelengths)
        object.__setattr__(self, "detector_data", detector_data)
