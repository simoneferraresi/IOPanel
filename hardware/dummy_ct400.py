import logging
import math
import threading
import time
from typing import override

import numpy as np

from hardware.ct400_types import Detector, Enable, LaserInput, LaserSource, PowerData, ScanWaitResult
from hardware.interfaces import AbstractCT400

logger = logging.getLogger("LabApp.DummyCT400")


class DummyCT400(AbstractCT400):
    """A dummy implementation of the CT400 interface for testing and UI development."""

    def __init__(
        self,
        scan_duration: float = 5.0,
        scan_error: str | None = None,
        scan_result_code: int = 2,
        wait_gate: threading.Event | None = None,
    ):
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
        self._scan_result_code = scan_result_code
        self._wait_gate = wait_gate
        self._stop_requested = threading.Event()
        self.stop_scan_calls = 0
        self._scan_min_wavelength = 1550.0
        self._scan_max_wavelength = 1560.0
        self._sampling_resolution_pm = 10
        self._laser_enabled = False
        self.scan_wait_end_calls = 0
        self.get_data_points_calls = 0
        self.cmd_laser_calls = []

    @override
    def is_connected(self) -> bool:
        logger.debug(f"Dummy is_connected called. Returning: {self._is_connected}")
        return self._is_connected

    @override
    def set_laser(
        self,
        laser_input: LaserInput,
        enable: Enable,
        gpib_address: int,
        laser_type: LaserSource,
        min_wavelength: float,
        max_wavelength: float,
        speed: int,
    ) -> None:
        logger.info(
            "Dummy set_laser called for input %s with enable=%s, address=%s, type=%s, wavelength range=%s-%s, speed=%s",
            laser_input,
            enable,
            gpib_address,
            laser_type,
            min_wavelength,
            max_wavelength,
            speed,
        )
        # In a dummy, we can consider 'set_laser' as connecting.
        self._is_connected = True

    @override
    def cmd_laser(self, laser_input: LaserInput, enable: Enable, wavelength: float, power: float) -> None:
        logger.info(
            "Dummy cmd_laser called for input %s with enable=%s, wavelength=%s, power=%s",
            laser_input,
            enable,
            wavelength,
            power,
        )
        named_args = {
            "laser_input": laser_input,
            "enable": enable,
            "wavelength": wavelength,
            "power": power,
        }
        self.cmd_laser_calls.append(((), named_args))
        if enable == Enable.DISABLE:
            self._is_connected = False
            self._laser_enabled = False
        elif enable == Enable.ENABLE:
            self._laser_enabled = True

    @override
    def set_sampling_res(self, resolution_pm: int) -> None:
        logger.info("Dummy set_sampling_res called with resolution: %s pm", resolution_pm)
        if int(resolution_pm) <= 0:
            raise ValueError("resolution_pm must be positive")
        self._sampling_resolution_pm = int(resolution_pm)

    @override
    def set_detector_array(self, det2: Enable, det3: Enable, det4: Enable, ext: Enable) -> None:
        logger.info("Dummy set_detector_array called with det2=%s, det3=%s, det4=%s, ext=%s", det2, det3, det4, ext)

    @override
    def set_scan(self, laser_power: float, min_wavelength: float, max_wavelength: float) -> None:
        logger.info("Dummy set_scan called from %s nm to %s nm at %s mW", min_wavelength, max_wavelength, laser_power)
        min_wavelength = float(min_wavelength)
        max_wavelength = float(max_wavelength)
        if min_wavelength >= max_wavelength:
            raise ValueError("min_wavelength must be less than max_wavelength")
        self._scan_min_wavelength = min_wavelength
        self._scan_max_wavelength = max_wavelength

    @override
    def start_scan(self) -> None:
        logger.info("Dummy start_scan called. Simulating a scan start.")
        self._stop_requested.clear()
        self._is_scanning = True
        self._scan_start_time = time.monotonic()

    @override
    def stop_scan(self) -> None:
        logger.info("Dummy stop_scan called.")
        self.stop_scan_calls += 1
        if self._is_scanning:
            self._stop_requested.set()

    @override
    def scan_wait_end(self) -> ScanWaitResult:
        """Block until completion or Stop, like the documented wait operation."""
        self.scan_wait_end_calls += 1
        if not self._is_scanning:
            return ScanWaitResult(0, "")

        if self._wait_gate is not None:
            while not self._wait_gate.is_set() and not self._stop_requested.wait(0.01):
                pass
        else:
            remaining = self._scan_duration - (time.monotonic() - self._scan_start_time)
            if remaining > 0:
                self._stop_requested.wait(remaining)

        self._is_scanning = False
        if self._stop_requested.is_set():
            return ScanWaitResult(1, "Measurement cancelled by user.")
        if self._scan_error is not None:
            return ScanWaitResult(self._scan_result_code, self._scan_error)
        logger.info("Dummy scan_wait_end: Scan finished.")
        return ScanWaitResult(0, "")

    @override
    def get_data_points(self, dets_used: list[Detector]) -> tuple[np.ndarray, np.ndarray]:
        logger.info("Dummy get_data_points called. Generating deterministic simulated data.")
        self.get_data_points_calls += 1
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

    @override
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

    @override
    def close(self) -> None:
        logger.info("Dummy CT400 close called.")
        self._is_scanning = False
        self._laser_enabled = False
        self._is_connected = False
