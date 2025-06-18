# hardware/piezo.py (Version 5 - With Prerequisite Call)

import logging
from ctypes import POINTER, WinDLL, byref, c_char_p, c_double, c_int, create_string_buffer
from pathlib import Path

# Note: We don't actually need pyserial for this attempt, but it's good to have.
import serial.tools.list_ports

logger = logging.getLogger("LabApp.Piezo")


# ... Exceptions are the same ...
class PiezoError(Exception): ...


class PiezoConnectionError(PiezoError): ...


class PiezoController:
    _dll = None
    VOLTS_PER_NM = 0.0037

    def __init__(self, dll_path: Path):
        if PiezoController._dll is None:
            if not dll_path.is_file():
                raise FileNotFoundError(f"Piezo DLL not found at: {dll_path}")
            try:
                PiezoController._dll = WinDLL(str(dll_path))
                self._configure_func_signatures()
            except OSError as e:
                PiezoController._dll = None
                raise PiezoError(f"Failed to load Piezo DLL from {dll_path}: {e}") from e

        self.hdl: int | None = None
        self.port: str | None = None

    def _configure_func_signatures(self):
        if not hasattr(PiezoController._dll, "signatures_configured"):
            dll = PiezoController._dll
            # --- CRITICAL ADDITION: List function ---
            dll.List.argtypes = [c_char_p, c_int]
            dll.List.restype = c_int

            dll.Open.argtypes = [c_char_p, c_int, c_int]
            dll.Open.restype = c_int

            dll.Close.argtypes = [c_int]
            dll.Close.restype = c_int
            dll.GetId.argtypes = [c_int, c_char_p]
            dll.GetId.restype = c_int
            dll.GetXAxisMaxVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetXAxisMaxVoltage.restype = c_int
            dll.GetYAxisMaxVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetYAxisMaxVoltage.restype = c_int
            dll.GetZAxisMaxVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetZAxisMaxVoltage.restype = c_int
            # Min voltage is usually 0, but good to have for completeness
            dll.GetXAxisMinVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetXAxisMinVoltage.restype = c_int
            dll.GetYAxisMinVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetYAxisMinVoltage.restype = c_int
            dll.GetZAxisMinVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetZAxisMinVoltage.restype = c_int
            dll.GetXAxisVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetXAxisVoltage.restype = c_int
            dll.SetXAxisVoltage.argtypes = [c_int, c_double]
            dll.SetXAxisVoltage.restype = c_int
            dll.GetYAxisVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetYAxisVoltage.restype = c_int
            dll.SetYAxisVoltage.argtypes = [c_int, c_double]
            dll.SetYAxisVoltage.restype = c_int
            dll.GetZAxisVoltage.argtypes = [c_int, POINTER(c_double)]
            dll.GetZAxisVoltage.restype = c_int
            dll.SetZAxisVoltage.argtypes = [c_int, c_double]
            dll.SetZAxisVoltage.restype = c_int
            dll.signatures_configured = True

    def get_max_voltage(self, axis: str) -> float:
        """Gets the maximum voltage for a specific axis ('x', 'y', or 'z')."""
        if not self.is_connected():
            raise PiezoConnectionError("Not connected")
        voltage = c_double(0)
        func = getattr(self._dll, f"Get{axis.capitalize()}AxisMaxVoltage")
        rc = func(self.hdl, byref(voltage))
        if rc < 0:
            raise PiezoError(f"GetMaxVoltage failed for axis {axis} with code {rc}")
        return voltage.value

    def get_min_voltage(self, axis: str) -> float:
        """Gets the minimum voltage for a specific axis ('x', 'y', or 'z')."""
        if not self.is_connected():
            raise PiezoConnectionError("Not connected")
        voltage = c_double(0)
        func = getattr(self._dll, f"Get{axis.capitalize()}AxisMinVoltage")
        rc = func(self.hdl, byref(voltage))
        if rc < 0:
            raise PiezoError(f"GetMinVoltage failed for axis {axis} with code {rc}")
        return voltage.value

    @staticmethod
    def find_devices(dll_path: Path) -> list[str]:
        """
        Finds valid Thorlabs Piezo controllers by first calling the List
        function to prime the DLL, then attempting to open discovered COM ports.
        """
        if not dll_path.is_file():
            logger.error(f"Cannot find devices; DLL not found at {dll_path}")
            return []

        if PiezoController._dll is None:
            PiezoController(dll_path)

        # --- NEW STRATEGY ---
        # 1. Call the DLL's List function. This may be a necessary prerequisite
        #    to initialize the driver's internal state.
        logger.info("Calling DLL's List function to prime the driver...")
        list_buffer = create_string_buffer(10240)
        num_devices_from_list = PiezoController._dll.List(list_buffer, 10240)
        logger.info(
            f"DLL List function returned {num_devices_from_list} devices with raw string: {list_buffer.value.decode(errors='ignore')}"
        )

        # 2. Now, proceed with our robust scanning of all available system COM ports.
        available_ports = [port.device for port in serial.tools.list_ports.comports()]
        logger.info(f"Scanning available system COM ports: {available_ports}")

        found_devices = []
        for port in available_ports:
            # Short timeout for the scan
            hdl = PiezoController._dll.Open(port.encode("utf-8"), 115200, 1)
            if hdl >= 0:
                logger.info(f"Successfully opened device on {port} with handle {hdl}. This is a Piezo Controller.")
                found_devices.append(port)
                PiezoController._dll.Close(hdl)
            else:
                logger.debug(f"Port {port} is not a Piezo Controller. Open failed with code {hdl}.")

        if not found_devices:
            logger.warning(
                "Could not open any COM ports via the DLL. This could be a driver issue or a permissions problem."
            )
        else:
            logger.info(f"Confirmed Piezo controllers on ports: {found_devices}")

        return found_devices

    # The rest of the PiezoController class (connect, disconnect, get_voltage, etc.)
    # remains exactly the same as in Version 4.
    def connect(self, port: str, baud_rate: int = 115200, timeout: int = 3) -> None:
        if self.is_connected():
            self.disconnect()
        self.port = port
        self.hdl = self._dll.Open(port.encode("utf-8"), baud_rate, timeout)
        if self.hdl < 0:
            error_code = self.hdl
            self.hdl = None
            self.port = None
            msg = f"Failed to connect to Piezo on {port}. DLL error code: {error_code}."
            raise PiezoConnectionError(msg)
        logger.info(f"Successfully connected to Piezo on {port} with handle {self.hdl}")

    def disconnect(self) -> None:
        if self.hdl is not None:
            logger.info(f"Disconnecting from Piezo on port {self.port}...")
            self._dll.Close(self.hdl)
        self.hdl = None
        self.port = None

    def is_connected(self) -> bool:
        return self.hdl is not None

    def get_id(self) -> str:
        if not self.is_connected():
            raise PiezoConnectionError("Device not connected.")
        buffer = create_string_buffer(256)
        rc = self._dll.GetId(self.hdl, buffer)
        if rc < 0:
            raise PiezoError(f"GetId failed with code {rc}")
        return buffer.value.decode("utf-8")

    def get_voltage(self, axis: str) -> float:
        if not self.is_connected():
            raise PiezoConnectionError("Not connected")
        voltage = c_double(0)
        func = getattr(self._dll, f"Get{axis.capitalize()}AxisVoltage")
        rc = func(self.hdl, byref(voltage))
        if rc < 0:
            raise PiezoError(f"GetVoltage failed for axis {axis} with code {rc}")
        return voltage.value

    def set_voltage(self, axis: str, voltage: float) -> None:
        if not self.is_connected():
            raise PiezoConnectionError("Not connected")

        # --- NEW: Add robust voltage clamping ---
        min_v = self.get_min_voltage(axis)
        max_v = self.get_max_voltage(axis)
        clamped_voltage = max(min_v, min(max_v, voltage))

        if abs(clamped_voltage - voltage) > 1e-9:  # Use a small tolerance for float comparison
            logger.warning(
                f"Voltage for axis {axis} out of range. Commanded: {voltage:.3f}V, Clamped to: {clamped_voltage:.3f}V"
            )
        # ----------------------------------------

        func = getattr(self._dll, f"Set{axis.capitalize()}AxisVoltage")
        rc = func(self.hdl, c_double(clamped_voltage))  # Use the clamped value
        if rc < 0:
            raise PiezoError(f"SetVoltage failed for axis {axis} with code {rc}")

    def move_nm(self, axis: str, distance_nm: float) -> None:
        current_voltage = self.get_voltage(axis)
        voltage_delta = distance_nm * self.VOLTS_PER_NM
        new_voltage = current_voltage + voltage_delta
        self.set_voltage(axis, new_voltage)
