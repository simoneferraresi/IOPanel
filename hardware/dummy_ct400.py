import logging
import time

import numpy as np

from hardware.ct400_types import Detector, Enable, PowerData
from hardware.interfaces import AbstractCT400

logger = logging.getLogger("LabApp.DummyCT400")


class DummyCT400(AbstractCT400):
    """A dummy implementation of the CT400 interface for testing and UI development."""

    def __init__(self):
        logger.warning("=" * 50)
        logger.warning("CT400 HARDWARE NOT FOUND/CONFIGURED. USING DUMMY IMPLEMENTATION.")
        logger.warning("All hardware calls will be simulated.")
        logger.warning("=" * 50)
        self._is_connected = False
        self._is_scanning = False
        self._scan_start_time = 0
        self._scan_duration = 5.0  # Simulate a 5-second scan

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
        elif len(args) > 1 and args[1] == Enable.DISABLE:
            self._is_connected = False

    def set_sampling_res(self, *args, **kwargs) -> None:
        logger.info("Dummy set_sampling_res called with args: %s, kwargs: %s", args, kwargs)

    def set_detector_array(self, *args, **kwargs) -> None:
        logger.info("Dummy set_detector_array called with args: %s, kwargs: %s", args, kwargs)

    def set_scan(self, *args, **kwargs) -> None:
        logger.info("Dummy set_scan called with args: %s, kwargs: %s", args, kwargs)

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

        elapsed = time.monotonic() - self._scan_start_time
        if elapsed >= self._scan_duration:
            logger.info("Dummy scan_wait_end: Scan finished.")
            self._is_scanning = False
            return 0, ""  # 0 means scan completed successfully
        else:
            return 1, ""  # 1 means scan is still running

    def get_data_points(self, dets_used: list[Detector]) -> tuple[np.ndarray, np.ndarray]:
        logger.info("Dummy get_data_points called. Generating fake data.")
        # Generate some plausible fake data
        wavelengths = np.linspace(1550, 1560, 1001)
        # A simple Gaussian peak
        peak_center = 1555
        peak_width = 1.5
        noise = np.random.randn(len(wavelengths)) * 0.1
        powers = -10 * np.exp(-((wavelengths - peak_center) ** 2) / (2 * peak_width**2)) - 30 + noise

        # Return in the same format as the real function
        num_detectors = len(dets_used)
        power_array = np.tile(powers, (num_detectors, 1))
        return wavelengths, power_array

    def get_all_powers(self) -> PowerData:
        # Return some random-ish but plausible live data
        pout = -20 + np.random.randn()
        detectors = {
            Detector.DE_1: -35 + np.random.randn() * 2,
            Detector.DE_2: -45 + np.random.randn() * 2,
            Detector.DE_3: -80.0,  # Simulate a dead channel
            Detector.DE_4: -40 + np.random.randn() * 2,
        }
        return PowerData(pout=pout, detectors=detectors)

    def close(self) -> None:
        logger.info("Dummy CT400 close called.")
        self._is_connected = False
