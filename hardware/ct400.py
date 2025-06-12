"""
Python wrapper for the Yenista CT400 Component Tester using CT400_lib.dll.
This module provides a high-level, Pythonic interface to control the CT400
device. It handles the low-level ctypes interactions, provides clear error
handling, and exposes the device's functionality through a well-documented class.
The `CT400` class is designed to be used as a context manager to ensure
that the connection to the device is properly closed, even if errors occur.
"""

import logging
from ctypes import (
    POINTER,
    Array,
    WinDLL,
    byref,
    c_char,
    c_double,
    c_int32,
    c_uint32,
    c_uint64,
    create_string_buffer,
)
from pathlib import Path

import numpy as np

# Import the shared types from the new file
from hardware.ct400_types import (
    Detector,
    Enable,
    LaserInput,
    LaserSource,
    PowerData,
    Unit,
)

# Import the ABC for implementation
from hardware.interfaces import AbstractCT400

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


class CT400(AbstractCT400):
    """A Python wrapper for the CT400_lib.dll.

    Provides an object-oriented interface to the CT400 hardware. This class
    manages DLL loading, function signature configuration, communication, and
    resource cleanup. It is recommended to use this class as a context
    manager (using a `with` statement) to ensure the connection is always
    closed properly.

    Args:
        dll_path (str): The absolute or relative path to the `CT400_lib.dll` file.

    Raises:
        FileNotFoundError: If the DLL file cannot be found at the specified path.
        CT400InitializationError: If the DLL fails to load or the device fails
                                  to initialize.
    """

    _ERROR_BUFFER_SIZE = 4096  # Increased for safety, as discussed.

    def __init__(self, dll_path: Path):
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
        if not dll_path.exists():
            logger.error(f"CT400 DLL not found at path: {dll_path}")
            raise FileNotFoundError(f"CT400 DLL not found at: {dll_path}")

        try:
            # WinDLL needs a string path, so we convert back at the last moment.
            self.dll = WinDLL(str(dll_path))
        except OSError as e:
            raise CT400InitializationError(
                f"Failed to load DLL from {dll_path}. Ensure it is a valid 64-bit or 32-bit DLL matching your Python interpreter. Error: {e}"
            ) from e

        self._configure_function_signatures()

        # The CT400_Init function uses a pointer to an integer to return an error code.
        init_error = c_int32()
        self.handle: int | None = self.dll.CT400_Init(byref(init_error))

        if not self.handle:
            raise CT400InitializationError(f"Failed to initialize CT400 hardware. DLL Error code: {init_error.value}")

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
            ("CT400_ScanWaitEnd", c_int32, [c_uint64, Array[c_char]]),
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
            logger.error(f"CT400 API Error: {error_message} (Return Code: {return_code})")
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
        """Configures a laser connected to a specific input port of the CT400.

        Args:
            laser_input: The input port to configure (e.g., `LaserInput.LI_1`).
            enable: `Enable.ENABLE` or `Enable.DISABLE` this laser configuration.
            gpib_address: The GPIB address of the laser source.
            laser_type: The type of the laser source (e.g., `LaserSource.LS_TunicsT100s_HP`).
            min_wavelength: Minimum operating wavelength of the laser in nm.
            max_wavelength: Maximum operating wavelength of the laser in nm.
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
        self._check_rc(result, f"Failed to set laser configuration for input {laser_input.name}")

    def cmd_laser(
        self,
        laser_input: LaserInput,
        enable: Enable,
        wavelength: float,
        power: float,
    ) -> None:
        """
        Sends a command to a configured laser, such as setting its wavelength and power.
        """
        logger.debug(f"Executing CmdLaser: Input={laser_input.name}, En={enable.name}, WL={wavelength}, P={power}")
        result = self.dll.CT400_CmdLaser(
            self.handle,
            laser_input.value,
            enable.value,
            wavelength,
            power,
        )
        self._check_rc(result, f"Failed to send command to laser on input {laser_input.name}")

    def set_sampling_res(self, resolution_pm: int) -> None:
        """
        Configures the sampling resolution for wavelength scans.
        """
        result = self.dll.CT400_SetSamplingResolution(self.handle, resolution_pm)
        self._check_rc(result, f"Failed to set sample resolution to {resolution_pm} pm")

    def set_detector_array(self, det2: Enable, det3: Enable, det4: Enable, ext: Enable) -> None:
        """
        Configures which detectors are active during a scan.
        """
        result = self.dll.CT400_SetDetectorArray(self.handle, det2.value, det3.value, det4.value, ext.value)
        self._check_rc(result, "Failed to set detector array configuration")

    def set_bnc(self, enable: Enable, alpha: float, beta: float, unit: Unit) -> None:
        """
        Configures the external BNC detector input, including scaling and units.
        """
        result = self.dll.CT400_SetBNC(self.handle, enable.value, alpha, beta, unit.value)
        self._check_rc(result, "Failed to set external BNC detector configuration")

    def set_scan(self, laser_power: float, min_wavelength: float, max_wavelength: float) -> None:
        """
        Configures the primary parameters for a wavelength scan.
        """
        logger.debug(f"Setting scan: P={laser_power}, MinWL={min_wavelength}, MaxWL={max_wavelength}")
        result = self.dll.CT400_SetScan(self.handle, laser_power, min_wavelength, max_wavelength)
        self._check_rc(result, "Failed to set scan configuration")

    def start_scan(self) -> None:
        """
        Starts the pre-configured wavelength scan. This is a non-blocking call.
        """
        logger.debug("Starting scan...")
        result = self.dll.CT400_ScanStart(self.handle)
        self._check_rc(result, "Failed to start scan")
        logger.info("Scan started successfully.")

    def stop_scan(self) -> None:
        """
        Stops an ongoing wavelength scan. This is a safe-to-call function.
        """
        result = self.dll.CT400_ScanStop(self.handle)
        if result == -1:
            logger.warning("CT400_ScanStop returned an error. The scan might have already finished or failed.")

    def scan_wait_end(self) -> tuple[int, str]:
        """
        Waits for the scan to end or polls its current status.

        This implementation creates and manages the ctypes error buffer internally,
        preventing it from leaking into other application layers and mitigating
        the risk of buffer overflows by decoding safely.

        Returns:
            A tuple containing the raw status code and the decoded error message.
        """
        # Buffer is now an implementation detail, not part of the interface.
        error_buf = create_string_buffer(self._ERROR_BUFFER_SIZE)  # A reasonable size
        result = self.dll.CT400_ScanWaitEnd(self.handle, error_buf)

        # Safely decode the buffer.
        error_msg = ""
        try:
            # The value attribute is a bytes object, decode it.
            error_msg = error_buf.value.decode("utf-8", errors="ignore").strip("\x00")
        except Exception as e:
            logger.error(f"Failed to decode error buffer from CT400_ScanWaitEnd: {e}")
            error_msg = "Could not decode error message from device."

        if result < 0 and result != -1:  # Specific documented error codes
            logger.error(f"CT400 Scan Error (Code: {result}): {error_msg}")
        elif result == -1:  # A general failure of the function call itself
            # This is a special case. -1 means the function call itself failed,
            # whereas other negative numbers are specific scan error codes.
            raise CT400CommunicationError(
                f"The call to CT400_ScanWaitEnd failed. Check device connection. Message: {error_msg}"
            )

        return result, error_msg

    def get_data_points(self, dets_used: list[Detector]) -> tuple[np.ndarray, np.ndarray]:
        """
        Retrieves the resampled wavelength and power data after a scan has completed.
        """
        # Get the number of resampled data points available.
        num_points = self.dll.CT400_GetNbDataPointsResampled(self.handle)
        self._check_rc(num_points, "Failed to get the number of resampled data points")

        if num_points <= 0:
            logger.warning(f"Scan reported {num_points} resampled data points. Returning empty arrays.")
            return np.array([]), np.empty((len(dets_used), 0))

        logger.info(f"Retrieving {num_points} resampled data points.")

        # --- Retrieve Wavelength Data ---
        wl_buffer = (c_double * num_points)()
        result = self.dll.CT400_ScanGetWavelengthResampledArray(self.handle, wl_buffer, num_points)
        self._check_rc(result, "Failed to retrieve resampled wavelength data")
        wavelengths = np.ctypeslib.as_array(wl_buffer)

        # --- Retrieve Power Data for Each Requested Detector ---
        det_pows = np.empty((len(dets_used), num_points), dtype=float)
        pow_buffer = (c_double * num_points)()  # Re-use this buffer for each detector call
        for i, det in enumerate(dets_used):
            result_det = self.dll.CT400_ScanGetDetectorResampledArray(
                self.handle,
                det.value,
                pow_buffer,
                num_points,
            )
            self._check_rc(result_det, f"Failed to get resampled data for detector {det.name}")
            # Important: Copy the data from the buffer immediately.
            det_pows[i, :] = np.ctypeslib.as_array(pow_buffer).copy()
        return wavelengths, det_pows

    def get_all_powers(self) -> PowerData:
        """
        Reads the instantaneous power values from all configured detectors.
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
                logger.error(f"An unexpected exception occurred during CT400_Close: {e}")
            finally:
                # Mark as closed regardless of outcome to prevent reuse
                self.handle = None
                del self.dll
        else:
            logger.debug("Attempted to close an already closed or uninitialized CT400 instance.")

    def __enter__(self):
        """Allows the CT400 class to be used as a context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ensures the connection is closed when exiting the context."""
        self.close()
