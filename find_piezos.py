"""Standalone, operator-invoked Thorlabs MDT DLL device inventory.

Uses only the existing vendor List entry point; unlike the application's
PiezoController.find_devices, it does not open COM ports, move stages, or
change voltages. Running this command still loads native vendor software.
"""

from __future__ import annotations

import argparse
import re
import sys
from ctypes import c_char_p, c_int, cdll, create_string_buffer
from pathlib import Path

# Preserve the original lab install location as the default, but allow a
# separate site/operator to override it without editing tracked source code.
DLL_PATH = Path(r"C:\Program Files (x86)\Thorlabs\MDT69XB\Sample\Thorlabs_MDT69XB_PythonSDK\MDT_COMMAND_LIB_x64.dll")


def is_serial_number(s: str) -> bool:
    """Check if a string looks like a typical serial number (e.g., all digits, long)."""
    return s.isdigit() and len(s) > 8


def is_com_port(s: str) -> bool:
    """Check if a string is a valid COM port name (e.g., 'COM3')."""
    return re.match(r"^COM\d+$", s, re.IGNORECASE) is not None


def parse_device_identifiers(raw_string: str) -> list[str]:
    """Preserve the legacy DLL List parser for COM ports and serial numbers."""
    parts = [part for part in raw_string.split(",") if part]
    return [part for part in parts if is_com_port(part) or is_serial_number(part)]


def find_connected_piezos(dll_path: Path | None = None) -> int:
    """Print identifiers reported by the vendor List API; never open a stage."""
    path = dll_path if dll_path is not None else DLL_PATH
    print("--- Piezo Device Discovery Utility (v2) ---")

    if not path.is_file():
        print(f"ERROR: The DLL was not found at the specified path: {path}")
        print("Supply the correct installed vendor DLL path with --dll.")
        return 1

    try:
        mdt_lib = cdll.LoadLibrary(str(path))
        cmd_list = mdt_lib.List
        cmd_list.argtypes = [c_char_p, c_int]
        cmd_list.restype = c_int
    except (OSError, AttributeError) as exc:
        print("ERROR: Failed to load the DLL or its List function. Verify the SDK and Python architecture.")
        print(f"Details: {exc}")
        return 1

    buffer_size = 10240
    device_list_buffer = create_string_buffer(buffer_size)

    print("\nScanning for devices...")
    try:
        result_code = cmd_list(device_list_buffer, buffer_size)
    except Exception as exc:
        print(f"ERROR: The DLL List call failed: {exc}")
        return 1

    if result_code < 0:
        print(f"ERROR: The List function failed with error code: {result_code}")
        return 1

    try:
        raw_string = device_list_buffer.value.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        print(f"ERROR: The DLL returned an undecodable device list: {exc}")
        return 1

    if not raw_string:
        print("\n>>> No Piezo devices were found.")
        return 0

    print(f"\nFound device(s). Raw output from DLL: '{raw_string}'")
    identifiers = parse_device_identifiers(raw_string)

    if not identifiers:
        print("\nERROR: Could not parse any valid identifiers (COM port or Serial Number) from the raw string.")
        print("Please check device connections and drivers.")
        return 1

    print("\n--- Detected Identifiers ---")
    print("The following identifiers can be used to connect to your devices.")
    print("Use these in your config.ini file for 'piezo_left_serial' and 'piezo_right_serial'.")

    for index, identifier in enumerate(identifiers, 1):
        identifier_type = "COM Port" if is_com_port(identifier) else "Serial Number"
        print(f"\nDevice #{index}:")
        print(f"  - Identifier: {identifier}")
        print(f"  - Type:       {identifier_type}")

    print("\n------------------------------")
    print("Recommendation: Assign these identifiers in your config.ini and test the connection in the main app.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List Thorlabs MDT piezo identifiers using the vendor DLL.")
    parser.add_argument("--dll", type=Path, default=DLL_PATH, help="Vendor DLL path (defaults to the original lab SDK).")
    args = parser.parse_args(argv)
    return find_connected_piezos(args.dll)


if __name__ == "__main__":
    sys.exit(main())
