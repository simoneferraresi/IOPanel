# CT400_updated.py

"""
Python wrapper for the Yenista CT400 Component Tester using CT400_lib.dll.

This module provides a high-level, Pythonic interface to control the CT400
device. It handles the low-level ctypes interactions, provides clear error
handling, and exposes the device's functionality through a well-documented class.

The `CT400` class is designed to be used as a context manager to ensure
that the connection to the device is properly closed, even if errors occur.

Basic Usage:
    from ctypes import create_string_buffer

    DLL_PATH = "path/to/your/CT400_lib.dll"

    try:
        with CT400(DLL_PATH) as ct400:
            # Check device information
            print(f"Device connected: {ct400.is_connected()}")
            print(f"Number of inputs: {ct400.get_number_inputs()}")
            print(f"Number of detectors: {ct400.get_number_detectors()}")

            # Example: Perform a scan
            ct400.set_laser(...)
            ct400.set_scan(laser_power=10.0, min_wavelength=1520.0, max_wavelength=1570.0)
            ct400.set_sampling_res(10) # 10 pm resolution
            ct400.start_scan()

            # Wait for scan to finish
            error_buf = create_string_buffer(1024)
            while (status := ct400.scan_wait_end(error_buf)) == 1:
                # status == 1 means scan is still running
                time.sleep(0.5)

            if status < 0:
                print(f"Scan failed with error: {error_buf.value.decode()}")
            else:
                # Retrieve and process data
                wavelengths, powers = ct400.get_data_points(
                    dets_used=[Detector.DE_1, Detector.DE_2]
                )
                print(f"Scan complete. Retrieved {len(wavelengths)} points.")

    except CT400Error as e:
        print(f"An error occurred with the CT400: {e}")
    except FileNotFoundError as e:
        print(f"Could not find the DLL: {e}")

"""

import logging
import os
from collections import namedtuple
from ctypes import (
    POINTER,
    WinDLL,
    byref,
    c_char_p,
    c_double,
    c_int32,
    c_uint32,
    c_uint64,
)
from enum import IntEnum
from typing import List, Optional, Tuple

import numpy as np

# Module-level logger
logger = logging.getLogger("LabApp.CT400")


# --- Custom Exceptions for Clear Error Reporting ---


class CT400Error(Exception):
    """Base exception class for all CT400-related errors."""

    pass


class CT400InitializationError(CT400Error):
    """Raised for errors during CT400 initialization or DLL loading."""

    pass


class CT400CommunicationError(CT400Error):
    """Raised for errors during communication with the CT400 device after successful initialization."""

    pass


# --- Enums Mirroring the C Header for Type Safety and Readability ---


class LaserSource(IntEnum):
    LS_TunicsPlus = 0
    LS_TunicsPurity = 1
    LS_TunicsReference = 2
    LS_TunicsT100s_HP = 3
    LS_TunicsT100r = 4
    LS_JdsuSws = 5
    LS_Agilent = 6


class LaserInput(IntEnum):
    LI_1 = 1
    LI_2 = 2
    LI_3 = 3
    LI_4 = 4


class Detector(IntEnum):
    """
    Enumeration for the detector channels.
    Note: POUT is treated separately. DE_5 is defined in the header but
    is not readable by the `CT400_ReadPowerDetectors` function.
    """

    POUT = 0
    DE_1 = 1
    DE_2 = 2
    DE_3 = 3
    DE_4 = 4
    DE_5 = 5


class Enable(IntEnum):
    DISABLE = 0
    ENABLE = 1


class Unit(IntEnum):
    Unit_mW = 0
    Unit_dBm = 1


# --- Named Tuple for Structured Data Return ---

PowerData = namedtuple("PowerData", ["pout", "detectors"])


class CT400:
    """
    A Python wrapper for the CT400_lib.dll, providing an object-oriented
    interface to the CT400 hardware.

    This class manages the DLL loading, function signatures, communication,
    and resource cleanup. It is recommended to use this class as a context
    manager (using a `with` statement) to ensure the connection is
    always closed properly.
    """

    def __init__(self, dll_path: str):
        """
        Initializes the CT400 wrapper, loads the DLL, and connects to the device.

        Args:
            dll_path: The absolute or relative path to the `CT400_lib.dll` file.
                      If relative, it's resolved against the directory of this script.

        Raises:
            FileNotFoundError: If the DLL file cannot be found at the specified path.
            CT400InitializationError: If the DLL fails to load or the device fails
                                      to initialize.
        """
        # Resolve DLL path if it's relative
        if not os.path.isabs(dll_path):
            script_dir = os.path.dirname(os.path.abspath(__file__))
            dll_path = os.path.join(script_dir, dll_path)
            logger.info(f"Relative DLL path provided. Resolved to: {dll_path}")

        if not os.path.exists(dll_path):
            logger.error(f"CT400 DLL not found at resolved path: {dll_path}")
            raise FileNotFoundError(f"CT400 DLL not found at: {dll_path}")

        try:
            self.dll = WinDLL(dll_path)
        except OSError as e:
            raise CT400InitializationError(
                f"Failed to load DLL from {dll_path}. Ensure it is a valid 64-bit or 32-bit DLL matching your Python interpreter. Error: {e}"
            ) from e

        self._configure_function_signatures()

        # The CT400_Init function uses a pointer to an integer to return an error code.
        init_error = c_int32()
        self.handle: Optional[int] = self.dll.CT400_Init(byref(init_error))

        if not self.handle:
            raise CT400InitializationError(
                f"Failed to initialize CT400 hardware. DLL Error code: {init_error.value}"
            )

        logger.info(f"CT400 Initialized successfully. Handle: {self.handle}")

    def _configure_function_signatures(self):
        """
        Configures the argument and return types for all DLL functions.
        This is crucial for ctypes to correctly handle data types and prevent errors.
        This declarative list makes it easy to verify against the C header file.
        """
        # Function definitions: (function_name, return_type, [arg_types...])
        func_defs = [
            ("CT400_Init", c_uint64, [POINTER(c_int32)]),
            ("CT400_CheckConnected", c_int32, [c_uint64]),
            ("CT400_GetNbInputs", c_int32, [c_uint64]),
            ("CT400_GetNbDetectors", c_int32, [c_uint64]),
            ("CT400_GetCT400Type", c_int32, [c_uint64]),
            (
                "CT400_ReadPowerDetectors",
                c_int32,
                [
                    c_uint64,
                    POINTER(c_double),
                    POINTER(c_double),
                    POINTER(c_double),
                    POINTER(c_double),
                    POINTER(c_double),
                    POINTER(c_double),
                ],
            ),
            (
                "CT400_SetLaser",
                c_int32,
                [
                    c_uint64,
                    c_int32,
                    c_int32,
                    c_int32,
                    c_int32,
                    c_double,
                    c_double,
                    c_int32,
                ],
            ),
            (
                "CT400_CmdLaser",
                c_int32,
                [c_uint64, c_int32, c_int32, c_double, c_double],
            ),
            ("CT400_SetSamplingResolution", c_int32, [c_uint64, c_uint32]),
            (
                "CT400_SetDetectorArray",
                c_int32,
                [c_uint64, c_int32, c_int32, c_int32, c_int32],
            ),
            ("CT400_SetBNC", c_int32, [c_uint64, c_int32, c_double, c_double, c_int32]),
            ("CT400_SetScan", c_int32, [c_uint64, c_double, c_double, c_double]),
            ("CT400_ScanStart", c_int32, [c_uint64]),
            ("CT400_ScanStop", c_int32, [c_uint64]),
            ("CT400_ScanWaitEnd", c_int32, [c_uint64, c_char_p]),
            (
                "CT400_GetNbDataPoints",
                c_int32,
                [c_uint64, POINTER(c_int32), POINTER(c_int32)],
            ),
            ("CT400_GetNbDataPointsResampled", c_int32, [c_uint64]),
            (
                "CT400_ScanGetWavelengthResampledArray",
                c_int32,
                [c_uint64, POINTER(c_double), c_int32],
            ),
            (
                "CT400_ScanGetDetectorResampledArray",
                c_int32,
                [c_uint64, c_int32, POINTER(c_double), c_int32],
            ),
            ("CT400_Close", c_int32, [c_uint64]),
        ]

        for name, restype, argtypes in func_defs:
            func = getattr(self.dll, name)
            func.restype = restype
            func.argtypes = argtypes

    def _check_rc(self, return_code: int, error_message: str):
        """
        Private helper to check the return code of a DLL function.
        Most functions in the DLL return -1 on error.

        Args:
            return_code: The integer return code from the DLL function.
            error_message: The base error message to include in the exception.

        Raises:
            CT400CommunicationError: If the return_code is -1.
        """
        if return_code == -1:
            logger.error(
                f"CT400 API Error: {error_message} (Return Code: {return_code})"
            )
            raise CT400CommunicationError(error_message)

    # --- Public API Methods ---

    def is_connected(self) -> bool:
        """
        Checks if the CT400 device is connected and responsive.

        Returns:
            True if the device is connected, False otherwise.
        """
        return bool(self.dll.CT400_CheckConnected(self.handle))

    def get_number_inputs(self) -> int:
        """
        Gets the number of laser input ports available on the device.

        Returns:
            The number of available inputs.

        Raises:
            CT400CommunicationError: If the query to the device fails.
        """
        result = self.dll.CT400_GetNbInputs(self.handle)
        self._check_rc(result, "Failed to get the number of available inputs")
        return result

    def get_number_detectors(self) -> int:
        """
        Gets the number of detector channels available on the device.

        Returns:
            The number of available detectors.

        Raises:
            CT400CommunicationError: If the query to the device fails.
        """
        result = self.dll.CT400_GetNbDetectors(self.handle)
        self._check_rc(result, "Failed to get the number of available detectors")
        return result

    def get_CT400_type(self) -> int:
        """
        Gets the specific type identifier of the connected CT400 model.
        According to the header: 0=SMF, 1=PM13, 2=PM15.

        Returns:
            An integer representing the device type.

        Raises:
            CT400CommunicationError: If the query to the device fails.
        """
        result = self.dll.CT400_GetCT400Type(self.handle)
        self._check_rc(result, "Failed to get the CT400 device type")
        return result

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
        """
        Configures a laser connected to a specific input port of the CT400.

        Args:
            laser_input: The input port to configure (e.g., `LaserInput.LI_1`).
            enable: `Enable.ENABLE` or `Enable.DISABLE` this laser configuration.
            gpib_address: The GPIB address of the laser source.
            laser_type: The type of the laser source (e.g., `LaserSource.LS_TunicsT100s_HP`).
            min_wavelength: Minimum operating wavelength of the laser in nanometers (nm).
            max_wavelength: Maximum operating wavelength of the laser in nanometers (nm).
            speed: Operating speed parameter for the laser (units are laser-dependent).

        Raises:
            CT400CommunicationError: If setting the configuration fails.
        """
        result = self.dll.CT400_SetLaser(
            self.handle,
            laser_input.value,
            enable.value,
            gpib_address,
            laser_type.value,
            min_wavelength,
            max_wavelength,
            speed,
        )
        self._check_rc(
            result, f"Failed to set laser configuration for input {laser_input.name}"
        )

    def cmd_laser(
        self,
        laser_input: LaserInput,
        enable: Enable,
        wavelength: float,
        power: float,
    ) -> None:
        """
        Sends a command to a configured laser, such as setting its wavelength and power.

        Args:
            laser_input: The input port of the laser to command.
            enable: `Enable.ENABLE` to turn the laser output on, `Enable.DISABLE` for off.
            wavelength: Target wavelength for the laser in nanometers (nm).
            power: Target power for the laser (units depend on laser and `set_bnc` config).

        Raises:
            CT400CommunicationError: If sending the command fails.
        """
        logger.debug(
            f"Executing CmdLaser: Input={laser_input.name}, En={enable.name}, WL={wavelength}, P={power}"
        )
        result = self.dll.CT400_CmdLaser(
            self.handle,
            laser_input.value,
            enable.value,
            wavelength,
            power,
        )
        self._check_rc(
            result, f"Failed to send command to laser on input {laser_input.name}"
        )

    def set_sampling_res(self, resolution_pm: int) -> None:
        """
        Configures the sampling resolution for wavelength scans.

        Args:
            resolution_pm: The desired resolution in picometers (pm).

        Raises:
            CT400CommunicationError: If setting the resolution fails.
        """
        result = self.dll.CT400_SetSamplingResolution(self.handle, resolution_pm)
        self._check_rc(result, f"Failed to set sample resolution to {resolution_pm} pm")

    def set_detector_array(
        self, det2: Enable, det3: Enable, det4: Enable, ext: Enable
    ) -> None:
        """
        Configures which detectors are active during a scan.
        Note: DE_1 is typically always active and not configured here.

        Args:
            det2: `Enable.ENABLE` or `Enable.DISABLE` for Detector 2.
            det3: `Enable.ENABLE` or `Enable.DISABLE` for Detector 3.
            det4: `Enable.ENABLE` or `Enable.DISABLE` for Detector 4.
            ext: `Enable.ENABLE` or `Enable.DISABLE` for the external BNC input.

        Raises:
            CT400CommunicationError: If configuring the detector array fails.
        """
        result = self.dll.CT400_SetDetectorArray(
            self.handle, det2.value, det3.value, det4.value, ext.value
        )
        self._check_rc(result, "Failed to set detector array configuration")

    def set_bnc(self, enable: Enable, alpha: float, beta: float, unit: Unit) -> None:
        """
        Configures the external BNC detector input, including scaling and units.

        Args:
            enable: `Enable.ENABLE` if the BNC input should be treated as optical power.
            alpha: Scaling factor 'A' for the formula (out = Ax + B).
            beta: Offset factor 'B' for the formula (out = Ax + B).
            unit: The units for the BNC reading (`Unit.Unit_mW` or `Unit.Unit_dBm`).

        Raises:
            CT400CommunicationError: If configuring the BNC input fails.
        """
        result = self.dll.CT400_SetBNC(
            self.handle, enable.value, alpha, beta, unit.value
        )
        self._check_rc(result, "Failed to set external BNC detector configuration")

    def set_scan(
        self, laser_power: float, min_wavelength: float, max_wavelength: float
    ) -> None:
        """
        Configures the primary parameters for a wavelength scan.

        Args:
            laser_power: The laser power to use during the scan (e.g., in mW).
            min_wavelength: The starting wavelength for the scan in nanometers (nm).
            max_wavelength: The ending wavelength for the scan in nanometers (nm).

        Raises:
            CT400CommunicationError: If setting the scan configuration fails.
        """
        logger.debug(
            f"Setting scan: P={laser_power}, MinWL={min_wavelength}, MaxWL={max_wavelength}"
        )
        result = self.dll.CT400_SetScan(
            self.handle, laser_power, min_wavelength, max_wavelength
        )
        self._check_rc(result, "Failed to set scan configuration")

    def start_scan(self) -> None:
        """
        Starts the pre-configured wavelength scan. This is a non-blocking call.

        Raises:
            CT400CommunicationError: If starting the scan fails.
        """
        logger.debug("Starting scan...")
        result = self.dll.CT400_ScanStart(self.handle)
        self._check_rc(result, "Failed to start scan")
        logger.info("Scan started successfully.")

    def stop_scan(self) -> None:
        """
        Stops an ongoing wavelength scan. This is a safe-to-call function.
        If the scan is already stopped or an error occurs, it logs a warning
        but does not raise an exception, making it suitable for cleanup tasks.
        """
        result = self.dll.CT400_ScanStop(self.handle)
        if result == -1:
            logger.warning(
                "CT400_ScanStop returned an error. The scan might have already finished or failed."
            )

    def scan_wait_end(self, error_buf: c_char_p) -> int:
        """
        Waits for the scan to end or polls its current status.

        Args:
            error_buf: A ctypes character buffer (e.g., `create_string_buffer(1024)`)
                       which will be populated with an error message if the scan
                       fails.

        Returns:
            - 0: Scan completed successfully.
            - 1: Scan is still running.
            - <0: An error occurred during the scan. The error message is in `error_buf`.

        Raises:
            CT400CommunicationError: If the underlying DLL call to check the status fails,
                                     which indicates a more severe communication problem.
        """
        result = self.dll.CT400_ScanWaitEnd(self.handle, error_buf)
        if result == -1:
            # This is a special case. -1 means the function call itself failed,
            # whereas other negative numbers are specific scan error codes.
            raise CT400CommunicationError(
                "The call to CT400_ScanWaitEnd failed. Check device connection."
            )
        return result

    def get_data_points(
        self, dets_used: List[Detector]
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Retrieves the resampled wavelength and power data after a scan has completed.

        Args:
            dets_used: A list of `Detector` enums for which to retrieve power data.
                       Example: `[Detector.DE_1, Detector.DE_4]`

        Returns:
            A tuple containing:
            - `wavelengths` (np.ndarray): A 1D array of wavelength points in nm.
            - `det_pows` (np.ndarray): A 2D array where each row corresponds to the
              power data (in mW or dBm) for a detector specified in `dets_used`,
              in the same order.

        Raises:
            CT400CommunicationError: If retrieving the number of points or the data arrays fails.
        """
        # Get the number of resampled data points available.
        num_points = self.dll.CT400_GetNbDataPointsResampled(self.handle)
        self._check_rc(num_points, "Failed to get the number of resampled data points")

        if num_points <= 0:
            logger.warning(
                f"Scan reported {num_points} resampled data points. Returning empty arrays."
            )
            return np.array([]), np.empty((len(dets_used), 0))

        logger.info(f"Retrieving {num_points} resampled data points.")

        # --- Retrieve Wavelength Data ---
        wl_buffer = (c_double * num_points)()
        result = self.dll.CT400_ScanGetWavelengthResampledArray(
            self.handle, wl_buffer, num_points
        )
        self._check_rc(result, "Failed to retrieve resampled wavelength data")
        wavelengths = np.ctypeslib.as_array(wl_buffer)

        # --- Retrieve Power Data for Each Requested Detector ---
        det_pows = np.empty((len(dets_used), num_points), dtype=float)
        pow_buffer = (
            c_double * num_points
        )()  # Re-use this buffer for each detector call

        for i, det in enumerate(dets_used):
            result_det = self.dll.CT400_ScanGetDetectorResampledArray(
                self.handle,
                det.value,
                pow_buffer,
                num_points,
            )
            self._check_rc(
                result_det, f"Failed to get resampled data for detector {det.name}"
            )
            # Important: Copy the data from the buffer immediately.
            det_pows[i, :] = np.ctypeslib.as_array(pow_buffer).copy()

        return wavelengths, det_pows

    def get_all_powers(self) -> PowerData:
        """
        Reads the instantaneous power values from all configured detectors.

        This is useful for real-time monitoring outside of a formal scan.

        Returns:
            A `PowerData` namedtuple containing:
            - `pout` (float): Power value from the Pout detector.
            - `detectors` (Dict[Detector, float]): A dictionary mapping detector
              enums (`DE_1` to `DE_4`) to their power values.

        Raises:
            CT400CommunicationError: If reading the power values fails.
        """
        pout, p1, p2, p3, p4, vext = (c_double() for _ in range(6))

        result = self.dll.CT400_ReadPowerDetectors(
            self.handle,
            byref(pout),
            byref(p1),
            byref(p2),
            byref(p3),
            byref(p4),
            byref(vext),
        )
        self._check_rc(result, "Failed to read instantaneous power from detectors")

        detectors_dict = {
            Detector.DE_1: p1.value,
            Detector.DE_2: p2.value,
            Detector.DE_3: p3.value,
            Detector.DE_4: p4.value,
        }

        return PowerData(pout=pout.value, detectors=detectors_dict)

    def close(self) -> None:
        """
        Closes the connection to the CT400 device and releases resources.
        This method is idempotent; it is safe to call multiple times.
        """
        if self.handle is not None:
            logger.info(f"Closing connection to CT400 (Handle: {self.handle})...")
            try:
                result = self.dll.CT400_Close(self.handle)
                if result == -1:
                    logger.warning(
                        f"Error code {result} received during CT400_Close. Resources may not be cleanly released."
                    )
                else:
                    logger.info("CT400 connection closed successfully.")
            except Exception as e:
                logger.error(
                    f"An unexpected exception occurred during CT400_Close: {e}"
                )
            finally:
                # Mark as closed regardless of outcome to prevent reuse
                self.handle = None
                del self.dll
        else:
            logger.debug(
                "Attempted to close an already closed or uninitialized CT400 instance."
            )

    def __enter__(self):
        """Allows the CT400 class to be used as a context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensures the connection is closed when exiting the context."""
        self.close()
