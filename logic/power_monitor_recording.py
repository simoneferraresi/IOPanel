"""Immutable settings and completed snapshots for Power Monitor recordings."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto

import numpy as np

from hardware.ct400_types import Detector, LaserInput


@dataclass(frozen=True)
class PowerMonitorAcquisitionSettings:
    wavelength_nm: float
    entered_laser_power: str
    entered_laser_power_unit: str
    laser_power_mw: float
    laser_input: LaserInput
    detectors: tuple[Detector, ...]
    requested_poll_interval_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "detectors", _normalize_detectors(self.detectors))
        if not isinstance(self.laser_input, LaserInput):
            raise TypeError("laser_input must be a LaserInput enum member")
        if self.requested_poll_interval_ms <= 0:
            raise ValueError("requested_poll_interval_ms must be greater than zero")


class PowerMonitorRecordingStopReason(Enum):
    USER_STOPPED = auto()
    MONITORING_STOPPED = auto()
    MONITORING_ERROR = auto()


@dataclass(frozen=True)
class PowerMonitorRecordingSample:
    """One successfully captured monitor sample, including its unplotted Pout value."""

    elapsed_s: float
    pout: float
    detectors: tuple[Detector, ...]
    detector_values: tuple[float, ...]

    def __post_init__(self) -> None:
        elapsed = float(self.elapsed_s)
        if not np.isfinite(elapsed) or elapsed < 0:
            raise ValueError("elapsed_s must be finite and non-negative")
        detectors = _normalize_detectors(self.detectors)
        detector_values = tuple(float(value) for value in self.detector_values)
        if len(detector_values) != len(detectors):
            raise ValueError("detector_values length must match detectors")
        object.__setattr__(self, "elapsed_s", elapsed)
        object.__setattr__(self, "pout", float(self.pout))
        object.__setattr__(self, "detectors", detectors)
        object.__setattr__(self, "detector_values", detector_values)


@dataclass(frozen=True)
class PowerMonitorRecording:
    settings: PowerMonitorAcquisitionSettings
    elapsed_s: np.ndarray
    pout_data: np.ndarray
    detector_data: np.ndarray
    detectors: tuple[Detector, ...]
    started_at_utc: datetime
    completed_at_utc: datetime
    duration_s: float
    backend: str
    simulated: bool
    stop_reason: PowerMonitorRecordingStopReason
    detector_unit: str = "dBm"
    pout_unit: str = "dBm"

    def __post_init__(self) -> None:
        detectors = _normalize_detectors(self.detectors)
        object.__setattr__(self, "detectors", detectors)
        if detectors != self.settings.detectors:
            raise ValueError("recording detectors must match settings.detectors in identity and order")
        if self.detector_unit != "dBm" or self.pout_unit != "dBm":
            raise ValueError("CT400 instantaneous Power Monitor readings must use dBm")
        for name in ("started_at_utc", "completed_at_utc"):
            value = getattr(self, name)
            if value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("completed_at_utc must not precede started_at_utc")
        if not np.isfinite(self.duration_s) or self.duration_s < 0:
            raise ValueError("duration_s must be finite and non-negative")

        elapsed = np.array(self.elapsed_s, dtype=float, copy=True)
        pout = np.array(self.pout_data, dtype=float, copy=True)
        detector_data = np.array(self.detector_data, dtype=float, copy=True)
        if elapsed.ndim != 1:
            raise ValueError("elapsed_s must be 1D")
        if pout.ndim != 1:
            raise ValueError("pout_data must be 1D")
        if detector_data.ndim != 2:
            raise ValueError("detector_data must be 2D")
        if len(pout) != len(elapsed):
            raise ValueError("pout_data and elapsed_s must have matching sample counts")
        expected_shape = (len(detectors), len(elapsed))
        if detector_data.shape != expected_shape:
            raise ValueError(f"detector_data must have shape {expected_shape}, got {detector_data.shape}")
        if not np.all(np.isfinite(elapsed)) or np.any(elapsed < 0):
            raise ValueError("elapsed_s must contain finite non-negative values")
        if np.any(np.diff(elapsed) < 0):
            raise ValueError("elapsed_s must be monotonic non-decreasing")
        if len(elapsed) and self.duration_s < elapsed[-1] and not np.isclose(self.duration_s, elapsed[-1]):
            raise ValueError("duration_s must be at least the last elapsed sample time")
        for array in (elapsed, pout, detector_data):
            array.setflags(write=False)
        object.__setattr__(self, "elapsed_s", elapsed)
        object.__setattr__(self, "pout_data", pout)
        object.__setattr__(self, "detector_data", detector_data)


def _normalize_detectors(detectors: Iterable[Detector]) -> tuple[Detector, ...]:
    try:
        ordered = tuple(detectors)
    except TypeError as error:
        raise TypeError("detectors must be an iterable of Detector members") from error
    if any(not isinstance(detector, Detector) for detector in ordered):
        raise TypeError("every detector must be a Detector enum member")
    if any(detector in (Detector.POUT, Detector.DE_5) for detector in ordered):
        raise ValueError("only DE1-DE4 optical monitor detectors are valid recording rows")
    if len(set(ordered)) != len(ordered):
        raise ValueError("detectors must not contain duplicates")
    return ordered
