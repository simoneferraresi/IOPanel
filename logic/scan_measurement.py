"""Immutable acquisition request and completed CT400 scan snapshot."""

from collections.abc import Iterable
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

    def __post_init__(self):
        object.__setattr__(self, "detectors", _normalize_detectors(self.detectors))


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
    scan_id: str | None = None

    def __post_init__(self):
        if self.completed_at_utc.utcoffset() is None:
            raise ValueError("completed_at_utc must be timezone-aware")
        detectors = _normalize_detectors(self.detectors)
        object.__setattr__(self, "detectors", detectors)
        if detectors != self.settings.detectors:
            raise ValueError("measurement detectors must match settings.detectors in identity and order")
        wavelengths = np.array(self.wavelengths_nm, copy=True)
        detector_data = np.array(self.detector_data, copy=True)
        if wavelengths.ndim != 1:
            raise ValueError(f"wavelengths_nm must be 1D, got {wavelengths.ndim}D")
        if detector_data.ndim != 2:
            raise ValueError(f"detector_data must be 2D, got {detector_data.ndim}D")
        expected_shape = (len(detectors), len(wavelengths))
        if detector_data.shape != expected_shape:
            raise ValueError(f"detector_data must have shape {expected_shape}, got {detector_data.shape}")
        wavelengths.setflags(write=False)
        detector_data.setflags(write=False)
        object.__setattr__(self, "wavelengths_nm", wavelengths)
        object.__setattr__(self, "detector_data", detector_data)


def _normalize_detectors(detectors: Iterable[Detector]) -> tuple[Detector, ...]:
    try:
        ordered_detectors = tuple(detectors)
    except TypeError as error:
        raise TypeError("detectors must be an iterable of Detector members") from error
    if any(not isinstance(detector, Detector) for detector in ordered_detectors):
        raise TypeError("every detector must be a Detector enum member")
    if any(detector is Detector.POUT for detector in ordered_detectors):
        raise ValueError("Detector.POUT is represented separately by final_pout")
    for index, detector in enumerate(ordered_detectors):
        if any(previous is detector for previous in ordered_detectors[:index]):
            raise ValueError("detectors must not contain duplicates")
    return ordered_detectors
