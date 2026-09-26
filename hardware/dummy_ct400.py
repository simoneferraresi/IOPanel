import logging
import math
import time

import numpy as np

from hardware.ct400_types import Detector, Enable, PowerData
from hardware.interfaces import AbstractCT400

logger = logging.getLogger("LabApp.DummyCT400")


class DummyCT400(AbstractCT400):
    """A dummy implementation of the CT400 interface for testing and UI development."""

    def __init__(self, scan_duration: float = 5.0, scan_error: str | None = None):
        if scan_duration < 0:
            raise ValueError("scan_duration must be non-negative")
        logger.warning("=" * 50)
        logger.warning("CT400 HARDWARE NOT FOUND/CONFIGURED. USING DUMMY IMPLEMENTATION.")
        logger.warning("All hardware calls will be simulated.")
        logger.warning("=" * 50)
        self._is_connected = False
        self._is_scanning = False
        self._scan_start_time = 0
        self._scan_duration = scan_duration
        self._scan_error = scan_error
        self._scan_min_wavelength = 1550.0
        self._scan_max_wavelength = 1560.0
        self._sampling_resolution_pm = 10
        self._laser_enabled = False

    def is_connected(self) -> bool:
        logger.debug(f"Dummy is_connected called. Returning: {self._is_connected}")
        return self._is_connected

    def set_laser(self, *args, **kwargs) -> None:
        logger.info("Dummy set_laser called with args: %s, kwargs: %s", args, kwargs)
        # In a dummy, we can consider 'set_laser' as connecting.
        self._is_connected = True

    def cmd_laser(self, *args, **kwargs) -> None:
        logger.info("Dummy cmd_laser called with args: %s, kwargs: %s", args, kwargs)
        # Check if the command is to disable the laser
        # This is a simplified check. A more robust dummy would parse kwargs more carefully.
        if "enable" in kwargs and kwargs["enable"] == Enable.DISABLE:
            self._is_connected = False
            self._laser_enabled = False
        elif "enable" in kwargs and kwargs["enable"] == Enable.ENABLE:
            self._laser_enabled = True
        elif len(args) > 1 and args[1] == Enable.DISABLE:
            self._is_connected = False
            self._laser_enabled = False
        elif len(args) > 1 and args[1] == Enable.ENABLE:
            self._laser_enabled = True

    def set_sampling_res(self, resolution_pm: int) -> None:
        logger.info("Dummy set_sampling_res called with resolution: %s pm", resolution_pm)
        if int(resolution_pm) <= 0:
            raise ValueError("resolution_pm must be positive")
        self._sampling_resolution_pm = int(resolution_pm)

    def set_detector_array(self, *args, **kwargs) -> None:
        logger.info("Dummy set_detector_array called with args: %s, kwargs: %s", args, kwargs)

    def set_scan(self, laser_power: float, min_wavelength: float, max_wavelength: float) -> None:
        logger.info("Dummy set_scan called from %s nm to %s nm at %s mW", min_wavelength, max_wavelength, laser_power)
        min_wavelength = float(min_wavelength)
        max_wavelength = float(max_wavelength)
        if min_wavelength >= max_wavelength:
            raise ValueError("min_wavelength must be less than max_wavelength")
        self._scan_min_wavelength = min_wavelength
        self._scan_max_wavelength = max_wavelength

    def start_scan(self) -> None:
        logger.info("Dummy start_scan called. Simulating a scan start.")
        self._is_scanning = True
        self._scan_start_time = time.monotonic()

    def stop_scan(self) -> None:
        logger.info("Dummy stop_scan called.")
        self._is_scanning = False

    def scan_wait_end(self) -> tuple[int, str]:
        """Dummy implementation, returns status code and an empty error string."""
        if not self._is_scanning:
            return 0, ""  # Not scanning or already finished

        if self._scan_error is not None:
            self._is_scanning = False
            return -1, self._scan_error

        elapsed = time.monotonic() - self._scan_start_time
        if elapsed >= self._scan_duration:
            logger.info("Dummy scan_wait_end: Scan finished.")
            self._is_scanning = False
            return 0, ""  # 0 means scan completed successfully
        else:
            return 1, ""  # 1 means scan is still running

    def get_data_points(self, dets_used: list[Detector]) -> tuple[np.ndarray, np.ndarray]:
        logger.info("Dummy get_data_points called. Generating deterministic simulated data.")
        span_pm = (self._scan_max_wavelength - self._scan_min_wavelength) * 1000
        step_nm = self._sampling_resolution_pm / 1000
        interval_count = span_pm / self._sampling_resolution_pm
        nearest_interval_count = round(interval_count)
        # Decimal wavelength endpoints are binary floats. Account only for
        # subtraction precision at their magnitude so an exactly divisible
        # range does not lose its final sample to floor(3.999999999...).
        interval_tolerance = (
            8
            * max(math.ulp(self._scan_min_wavelength), math.ulp(self._scan_max_wavelength))
            * 1000
            / self._sampling_resolution_pm
        )
        if abs(interval_count - nearest_interval_count) <= interval_tolerance:
            interval_count = nearest_interval_count
        num_intervals = int(math.floor(interval_count))
        wavelengths = self._scan_min_wavelength + np.arange(num_intervals + 1) * step_nm
        peak_center = (self._scan_min_wavelength + self._scan_max_wavelength) / 2
        peak_width = (self._scan_max_wavelength - self._scan_min_wavelength) / 6
        powers = -10 * np.exp(-((wavelengths - peak_center) ** 2) / (2 * peak_width**2)) - 30

        # Return in the same format as the real function
        num_detectors = len(dets_used)
        power_array = np.tile(powers, (num_detectors, 1))
        return wavelengths, power_array

    def get_all_powers(self) -> PowerData:
        # Stable plausible readings keep simulated acquisition repeatable.
        pout = -20.0
        detectors = {
            Detector.DE_1: -35.0,
            Detector.DE_2: -45.0,
            Detector.DE_3: -80.0,  # Simulate a dead channel
            Detector.DE_4: -40.0,
        }
        return PowerData(pout=pout, detectors=detectors)

    def close(self) -> None:
        logger.info("Dummy CT400 close called.")
        self._is_scanning = False
        self._laser_enabled = False
        self._is_connected = False
