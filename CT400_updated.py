import os
from collections import namedtuple
from ctypes import *
from enum import IntEnum
from typing import List, Tuple

import numpy as np


# Custom Exceptions
class CT400Error(Exception):
    """Base exception class for CT400 errors."""

    pass


class CT400InitializationError(CT400Error):
    """Error during CT400 initialization."""

    pass


class CT400CommunicationError(CT400Error):
    """Error during communication with the CT400 device."""

    pass


# Enums from the header file
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
    POUT = 0
    DE_1 = 1
    DE_2 = 2
    DE_3 = 3
    DE_4 = 4
    DE_5 = 5  # Although DE_5 seems unused in ReadPowerDetectors based on signature


class Enable(IntEnum):
    DISABLE = 0
    ENABLE = 1


class Unit(IntEnum):
    Unit_mW = 0
    Unit_dBm = 1


# Named tuple for power readings
PowerData = namedtuple("PowerData", ["pout", "detectors"])


class CT400:
    """
    Python wrapper for the CT400_lib.dll using ctypes.

    Provides an interface to control the CT400 device for wavelength scanning
    and power measurements.
    """

    def __init__(self, dll_path: str):
        """
        Initializes the CT400 wrapper and connects to the device.

        Args:
            dll_path: The absolute or relative path to the CT400_lib.dll file.

        Raises:
            FileNotFoundError: If the DLL file cannot be found at the specified path.
            CT400InitializationError: If the device fails to initialize via the DLL.
        """
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"CT400 DLL not found at: {dll_path}")
        if not os.path.isabs(dll_path):
            # If relative, it's relative to the current working directory
            # Consider resolving relative to the script/package if needed
            dll_path = os.path.abspath(dll_path)

        try:
            # Load the DLL
            self.dll = WinDLL(dll_path)
        except OSError as e:
            raise CT400InitializationError(
                f"Failed to load DLL from {dll_path}: {e}"
            ) from e

        # Initialize error variable (used by CT400_Init)
        self.error = c_int32()

        # Configure function signatures
        self._configure_functions()

        # Initialize the device
        self.handle = self.dll.CT400_Init(byref(self.error))
        if self.handle == 0:
            raise CT400InitializationError(
                f"Failed to initialize CT400 hardware. DLL Error code: {self.error.value}"
            )
        else:
            print(
                f"CT400 Initialized successfully. Handle: {self.handle}"
            )  # Basic success log

    def _configure_functions(self):
        """Configure ctypes argtypes and restype for all DLL functions."""
        # CT400_Init
        self.dll.CT400_Init.argtypes = [POINTER(c_int32)]
        self.dll.CT400_Init.restype = c_uint64

        # CT400_CheckConnected
        self.dll.CT400_CheckConnected.argtypes = [c_uint64]
        self.dll.CT400_CheckConnected.restype = c_int32

        # CT400_GetNbInputs
        self.dll.CT400_GetNbInputs.argtypes = [c_uint64]
        self.dll.CT400_GetNbInputs.restype = c_int32

        # CT400_GetNbDetectors
        self.dll.CT400_GetNbDetectors.argtypes = [c_uint64]
        self.dll.CT400_GetNbDetectors.restype = c_int32

        # CT400_GetCT400Type
        self.dll.CT400_GetCT400Type.argtypes = [c_uint64]
        self.dll.CT400_GetCT400Type.restype = c_int32

        # CT400_ReadPowerDetectors
        self.dll.CT400_ReadPowerDetectors.argtypes = [
            c_uint64,
            POINTER(c_double),  # Pout
            POINTER(c_double),  # P1
            POINTER(c_double),  # P2
            POINTER(c_double),  # P3
            POINTER(c_double),  # P4
            POINTER(c_double),  # Vext
        ]
        self.dll.CT400_ReadPowerDetectors.restype = c_int32

        # CT400_SetLaser
        self.dll.CT400_SetLaser.argtypes = [
            c_uint64,  # handle
            c_int32,  # laser_input (enum value)
            c_int32,  # enable (enum value)
            c_int32,  # gpib_address
            c_int32,  # laser_type (enum value)
            c_double,  # min_wavelength
            c_double,  # max_wavelength
            c_int32,  # speed
        ]
        self.dll.CT400_SetLaser.restype = c_int32

        # CT400_CmdLaser
        self.dll.CT400_CmdLaser.argtypes = [
            c_uint64,  # handle
            c_int32,  # laser_input (enum value)
            c_int32,  # enable (enum value)
            c_double,  # wavelength
            c_double,  # power
        ]
        # Corrected: Added missing restype definition
        self.dll.CT400_CmdLaser.restype = c_int32

        # CT400_SetSamplingResolution
        self.dll.CT400_SetSamplingResolution.argtypes = [c_uint64, c_uint32]
        self.dll.CT400_SetSamplingResolution.restype = c_int32

        # CT400_SetDetectorArray
        self.dll.CT400_SetDetectorArray.argtypes = [
            c_uint64,  # handle
            c_int32,  # det2 (enum value)
            c_int32,  # det3 (enum value)
            c_int32,  # det4 (enum value)
            c_int32,  # ext (enum value)
        ]
        self.dll.CT400_SetDetectorArray.restype = c_int32

        # CT400_SetBNC
        self.dll.CT400_SetBNC.argtypes = [
            c_uint64,  # handle
            c_int32,  # enable (enum value)
            c_double,  # alpha
            c_double,  # beta
            c_int32,  # unit (enum value)
        ]
        self.dll.CT400_SetBNC.restype = c_int32

        # CT400_SetScan
        self.dll.CT400_SetScan.argtypes = [
            c_uint64,  # handle
            c_double,  # laser_power
            c_double,  # min_wavelength
            c_double,  # max_wavelength
        ]
        self.dll.CT400_SetScan.restype = c_int32

        # CT400_ScanStart
        self.dll.CT400_ScanStart.argtypes = [c_uint64]
        self.dll.CT400_ScanStart.restype = c_int32

        # CT400_ScanStop
        self.dll.CT400_ScanStop.argtypes = [c_uint64]
        self.dll.CT400_ScanStop.restype = c_int32

        # CT400_ScanWaitEnd
        self.dll.CT400_ScanWaitEnd.argtypes = [c_uint64, c_char_p]
        self.dll.CT400_ScanWaitEnd.restype = c_int32

        # CT400_GetNbDataPoints
        self.dll.CT400_GetNbDataPoints.argtypes = [
            c_uint64,
            POINTER(c_int32),  # Pointer to store data_points
            POINTER(c_int32),  # Pointer to store discard_points
        ]
        self.dll.CT400_GetNbDataPoints.restype = c_int32

        # CT400_GetNbDataPointsResampled
        self.dll.CT400_GetNbDataPointsResampled.argtypes = [c_uint64]
        self.dll.CT400_GetNbDataPointsResampled.restype = c_int32

        # CT400_ScanGetWavelengthResampledArray
        self.dll.CT400_ScanGetWavelengthResampledArray.argtypes = [
            c_uint64,
            POINTER(c_double),  # Buffer to store wavelengths
            c_int32,  # Size of the buffer
        ]
        self.dll.CT400_ScanGetWavelengthResampledArray.restype = c_int32

        # CT400_ScanGetDetectorResampledArray
        self.dll.CT400_ScanGetDetectorResampledArray.argtypes = [
            c_uint64,
            c_int32,  # detector (enum value)
            POINTER(c_double),  # Buffer to store detector powers
            c_int32,  # Size of the buffer
        ]
        self.dll.CT400_ScanGetDetectorResampledArray.restype = c_int32

        # CT400_Close
        self.dll.CT400_Close.argtypes = [c_uint64]
        self.dll.CT400_Close.restype = c_int32

    def is_connected(self) -> bool:
        """
        Checks if the CT400 device is currently connected and responding.

        Returns:
            True if connected, False otherwise.
        """
        # Assuming a return value > 0 means connected. Verify this with DLL docs.
        return bool(self.dll.CT400_CheckConnected(self.handle))

    def get_number_inputs(self) -> int:
        """
        Gets the number of laser input ports available on the device.

        Returns:
            The number of available inputs.

        Raises:
            CT400CommunicationError: If the query fails.
        """
        result = self.dll.CT400_GetNbInputs(self.handle)
        if result == -1:
            raise CT400CommunicationError(
                "Failed to get the number of available inputs"
            )
        return result

    def get_number_detectors(self) -> int:
        """
        Gets the number of detector channels available on the device.

        Returns:
            The number of available detectors.

        Raises:
            CT400CommunicationError: If the query fails.
        """
        result = self.dll.CT400_GetNbDetectors(self.handle)
        if result == -1:
            raise CT400CommunicationError(
                "Failed to get the number of available detectors"
            )
        return result

    def get_CT400_type(self) -> int:
        """
        Gets the specific type identifier of the connected CT400 model.

        Returns:
            An integer representing the device type.

        Raises:
            CT400CommunicationError: If the query fails.
        """
        result = self.dll.CT400_GetCT400Type(self.handle)
        if result == -1:
            raise CT400CommunicationError("Failed to get the CT400 type")
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
            laser_input: The input port enum (LaserInput.LI_1, etc.).
            enable: Enable or disable this laser configuration (Enable.ENABLE/DISABLE).
            gpib_address: The GPIB address of the laser source.
            laser_type: The type of the laser source enum (LaserSource.LS_...).
            min_wavelength: Minimum operating wavelength of the laser (nm).
            max_wavelength: Maximum operating wavelength of the laser (nm).
            speed: Operating speed parameter for the laser (units depend on laser).

        Raises:
            CT400CommunicationError: If setting the configuration fails.
        """
        result = self.dll.CT400_SetLaser(
            self.handle,
            laser_input.value,
            enable.value,
            gpib_address,
            laser_type.value,
            c_double(min_wavelength),
            c_double(max_wavelength),
            speed,
        )
        if result == -1:
            # TODO: Query DLL for specific error message if available
            raise CT400CommunicationError(
                f"Failed to set laser configuration for input {laser_input.name}"
            )

    def cmd_laser(
        self,
        laser_input: LaserInput,
        enable: Enable,
        wavelength: float,
        power: float,
    ) -> None:
        """
        Sends a command to a configured laser (e.g., set wavelength, power, enable/disable).

        Args:
            laser_input: The input port enum of the laser to command.
            enable: Enable or disable the laser output (Enable.ENABLE/DISABLE).
            wavelength: Target wavelength for the laser (nm).
            power: Target power for the laser (mW or dBm, depends on laser/unit setting).

        Raises:
            CT400CommunicationError: If sending the command fails.
        """
        result = self.dll.CT400_CmdLaser(
            self.handle,
            laser_input.value,
            enable.value,
            c_double(wavelength),
            c_double(power),
        )
        if result == -1:
            # TODO: Query DLL for specific error message if available
            raise CT400CommunicationError(
                f"Failed to send command to laser input {laser_input.name}"
            )

    def set_sampling_res(self, res: int) -> None:
        """
        Configures the sampling resolution for wavelength scans.

        Args:
            res: The desired resolution in picometers (pm).

        Raises:
            CT400CommunicationError: If setting the resolution fails.
        """
        result = self.dll.CT400_SetSamplingResolution(self.handle, c_uint32(res))
        if result == -1:
            raise CT400CommunicationError(
                f"Failed to set sample resolution to {res} pm"
            )

    def set_detector_array(
        self, det2: Enable, det3: Enable, det4: Enable, ext: Enable
    ) -> None:
        """
        Configures which detectors (DE_2, DE_3, DE_4, and external BNC) are active.
        Note: DE_1 is typically always active or configured separately.

        Args:
            det2: Enable status for Detector 2.
            det3: Enable status for Detector 3.
            det4: Enable status for Detector 4.
            ext: Enable status for the external BNC input.

        Raises:
            CT400CommunicationError: If configuring the detector array fails.
        """
        result = self.dll.CT400_SetDetectorArray(
            self.handle,
            det2.value,
            det3.value,
            det4.value,
            ext.value,
        )
        if result == -1:
            raise CT400CommunicationError("Failed to set detector array configuration")

    def set_bnc(self, enable: Enable, alpha: float, beta: float, unit: Unit) -> None:
        """
        Configures the external BNC detector input.

        Args:
            enable: Enable status for the BNC input.
            alpha: Scaling factor alpha for the BNC input.
            beta: Offset factor beta for the BNC input.
            unit: The units for the BNC reading (Unit.Unit_mW or Unit.Unit_dBm).

        Raises:
            CT400CommunicationError: If configuring the BNC input fails.
        """
        result = self.dll.CT400_SetBNC(
            self.handle, enable.value, c_double(alpha), c_double(beta), unit.value
        )
        if result == -1:
            raise CT400CommunicationError(
                "Failed to set external BNC detector configuration"
            )

    def set_scan(
        self, laser_power: float, min_wavelength: float, max_wavelength: float
    ) -> None:
        """
        Configures the parameters for a wavelength scan.

        Args:
            laser_power: The laser power to use during the scan (mW or dBm).
            min_wavelength: The starting wavelength for the scan (nm).
            max_wavelength: The ending wavelength for the scan (nm).

        Raises:
            CT400CommunicationError: If setting the scan configuration fails.
        """
        result = self.dll.CT400_SetScan(
            self.handle,
            c_double(laser_power),
            c_double(min_wavelength),
            c_double(max_wavelength),
        )
        if result == -1:
            raise CT400CommunicationError("Failed to set scan configuration")

    def start_scan(self) -> None:
        """
        Starts the configured wavelength scan.

        Raises:
            CT400CommunicationError: If starting the scan fails.
        """
        result = self.dll.CT400_ScanStart(self.handle)
        if result == -1:
            raise CT400CommunicationError("Failed to start scan")

    def stop_scan(self) -> None:
        """
        Stops an ongoing wavelength scan.

        Raises:
            CT400CommunicationError: If stopping the scan fails.
        """
        result = self.dll.CT400_ScanStop(self.handle)
        if result == -1:
            # Log warning instead of raising error, as stopping might be called defensively
            print(
                "Warning: CT400_ScanStop returned an error code (scan might have already finished or failed)"
            )
            # raise CT400CommunicationError("Failed to stop scan")

    def get_current_wavelength(self) -> float:
        """
        (Assumed Function - Not explicitly in _configure_functions, needs verification)
        Gets the current wavelength of the laser during a scan or operation.

        Returns:
            The current wavelength in nm.

        Raises:
            CT400CommunicationError: If getting the wavelength fails.
            AttributeError: If the DLL function name is incorrect or missing.
        """
        # Placeholder: Replace 'CT400_GetCurrentWavelength' with the actual DLL function name
        # and configure its argtypes/restype in _configure_functions if it exists.
        # Example:
        # if not hasattr(self.dll, 'CT400_GetCurrentWavelength'):
        #    raise NotImplementedError("Function CT400_GetCurrentWavelength not found in DLL wrapper.")
        # self.dll.CT400_GetCurrentWavelength.restype = c_double
        # self.dll.CT400_GetCurrentWavelength.argtypes = [c_uint64]
        # current_wl = self.dll.CT400_GetCurrentWavelength(self.handle)
        # if current_wl < 0: # Or check some other error condition based on docs
        #    raise CT400CommunicationError("Failed to get current wavelength")
        # return current_wl
        # --- If the function doesn't exist, remove or comment out this method ---
        # For now, let's raise NotImplementedError to highlight it needs checking
        raise NotImplementedError(
            "get_current_wavelength needs verification of DLL function name and signature."
        )

    def get_data_points(
        self, dets_used: List[Detector] = [Detector.DE_1]
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Retrieves the resampled wavelength and power data after a scan completes.

        Args:
            dets_used: A list of Detector enums for which to retrieve power data.
                       Defaults to [Detector.DE_1].

        Returns:
            A tuple containing:
                - wavelengths (np.ndarray): Array of wavelength points (nm).
                - det_pows (np.ndarray): 2D array where each row corresponds to the
                  power data (dB or mW) for a detector specified in dets_used.

        Raises:
            CT400CommunicationError: If retrieving data points or scan info fails.
        """
        # --- Get number of points ---
        # Create buffers for the output parameters
        data_points_buf = c_int32()
        discard_points_buf = c_int32()

        result = self.dll.CT400_GetNbDataPoints(
            self.handle, byref(data_points_buf), byref(discard_points_buf)
        )
        if result == -1:
            raise CT400CommunicationError(
                "Failed to get number of data points from scan"
            )

        # --- Get number of resampled points ---
        number_points_resampled = self.dll.CT400_GetNbDataPointsResampled(self.handle)
        if number_points_resampled == -1:
            raise CT400CommunicationError(
                "Failed to get number of resampled points from scan"
            )
        if number_points_resampled <= 0:
            # Handle case where scan might have failed or produced no data
            print("Warning: Scan reported 0 resampled data points.")
            return np.array([]), np.array(
                [[] for _ in dets_used]
            )  # Return empty arrays

        # --- Get Wavelengths Once ---
        wavelength_array_buf = (c_double * number_points_resampled)()
        result = self.dll.CT400_ScanGetWavelengthResampledArray(
            self.handle, wavelength_array_buf, number_points_resampled
        )
        if result == -1:
            raise CT400CommunicationError(
                "Failed to get resampled wavelength data from scan"
            )
        # Convert ctypes array to NumPy array immediately
        wavelengths = np.ctypeslib.as_array(wavelength_array_buf)

        # --- Get Detector Powers ---
        det_pows = np.empty([len(dets_used), number_points_resampled], dtype=float)
        # Create a reusable buffer for detector data
        detector_array_buf = (c_double * number_points_resampled)()

        for i, det in enumerate(dets_used):
            result = self.dll.CT400_ScanGetDetectorResampledArray(
                self.handle,
                det.value,  # Pass detector enum value
                detector_array_buf,  # Pass the buffer
                number_points_resampled,
            )
            if result == -1:
                raise CT400CommunicationError(
                    f"Failed to get resampled data for detector {det.name}"
                )

            # Copy data from the buffer into the correct row of the numpy array
            det_pows[i, :] = np.ctypeslib.as_array(
                detector_array_buf
            )  # Direct assignment should be fine

        return (
            wavelengths.copy(),
            det_pows.copy(),
        )  # Return copies to avoid issues if buffer is reused? (Probably overkill)

    def scan_wait_end(self, error_buf: c_char_p) -> int:
        """
        Waits for the scan to end or checks its status.

        Args:
            error_buf: A ctypes character buffer (e.g., create_string_buffer(1024))
                       to receive an error message if the scan failed.

        Returns:
            0: If the scan completed successfully.
            1: If the scan is still running.
           <0: If an error occurred during the scan (error message in error_buf).

        Raises:
            CT400CommunicationError: If the call to wait/check scan status fails itself.
        """
        result = self.dll.CT400_ScanWaitEnd(self.handle, error_buf)
        # Note: result == -1 indicates an error *in the DLL call itself*,
        # while result < 0 (but not -1) indicates a *scan operation error*.
        # The calling code (ScanWorker) handles the < 0 case.
        if result == -1:
            # This likely means a more fundamental communication issue
            raise CT400CommunicationError(
                "Failed call to CT400_ScanWaitEnd (DLL communication error)"
            )
        return result

    def get_all_powers(self) -> PowerData:
        """
        Reads the instantaneous power values from all configured detectors.

        Returns:
            A PowerData namedtuple containing:
                - pout (float): Power value from the Pout detector.
                - detectors (Dict[Detector, float]): Dictionary mapping Detector enums
                  (DE_1 to DE_4) to their corresponding power values.

        Raises:
            CT400CommunicationError: If reading the power values fails.
        """
        pout = c_double()
        p1 = c_double()
        p2 = c_double()
        p3 = c_double()
        p4 = c_double()
        vext = c_double()  # Vext seems unused based on the PowerData structure

        result = self.dll.CT400_ReadPowerDetectors(
            self.handle,
            byref(pout),
            byref(p1),
            byref(p2),
            byref(p3),
            byref(p4),
            byref(vext),  # Pass Vext buffer even if not used in return value
        )

        if result == -1:
            raise CT400CommunicationError("Failed to read power values from detectors")

        # Map detector enum keys to their c_double values
        detectors_dict = {
            Detector.DE_1: p1.value,
            Detector.DE_2: p2.value,
            Detector.DE_3: p3.value,
            Detector.DE_4: p4.value,
            # Omitting Detector.POUT here as it's returned separately
            # Omitting Detector.DE_5 as it's not read by this function
        }

        return PowerData(pout=pout.value, detectors=detectors_dict)

    def close(self) -> None:
        """
        Closes the connection to the CT400 device and releases resources.
        Safe to call even if already closed.
        """
        if hasattr(self, "handle") and self.handle:
            try:
                result = self.dll.CT400_Close(self.handle)
                if result == -1:
                    # Log error but don't necessarily raise, as we're closing anyway
                    print(f"Warning: Error code {result} received during CT400_Close.")
                else:
                    print(f"CT400 Closed successfully. Handle: {self.handle}")
            except Exception as e:
                print(f"Exception during CT400_Close: {e}")
            finally:
                # Ensure handle is cleared even if close fails
                self.handle = None  # Mark as closed
                del self.dll  # Unload DLL? Maybe not necessary if process exits soon.

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the runtime context related to this object, ensuring cleanup."""
        self.close()
