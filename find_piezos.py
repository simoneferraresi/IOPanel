# find_piezos.py

import re
import sys
from ctypes import c_char_p, c_int, cdll, create_string_buffer
from pathlib import Path

# --- Configuration ---
# You might need to adjust this path to where your DLL is located
# Or just place the DLL in the same folder as this script.
DLL_PATH = Path(r"C:\Program Files (x86)\Thorlabs\MDT69XB\Sample\Thorlabs_MDT69XB_PythonSDK\MDT_COMMAND_LIB_x64.dll")


def is_serial_number(s: str) -> bool:
    """Check if a string looks like a typical serial number (e.g., all digits, long)."""
    return s.isdigit() and len(s) > 8


def is_com_port(s: str) -> bool:
    """Check if a string is a valid COM port name (e.g., 'COM3')."""
    return re.match(r"^COM\d+$", s, re.IGNORECASE) is not None


def find_connected_piezos():
    """
    Scans for connected Thorlabs MDT devices and prints their information.
    This version uses a more robust parser to handle complex DLL output.
    """
    print("--- Piezo Device Discovery Utility (v2) ---")

    if not DLL_PATH.exists():
        print(f"ERROR: The DLL was not found at the specified path: {DLL_PATH}")
        print(
            "Please place 'MDT_COMMAND_LIB.dll' in the same directory as this script, or update the DLL_PATH variable."
        )
        sys.exit(1)

    try:
        mdt_lib = cdll.LoadLibrary(str(DLL_PATH))
        cmd_list = mdt_lib.List
        cmd_list.argtypes = [c_char_p, c_int]
        cmd_list.restype = c_int
    except OSError as e:
        print("ERROR: Failed to load the DLL. Ensure it is the correct version (32/64-bit) for your Python.")
        print(f"Details: {e}")
        sys.exit(1)

    buffer_size = 10240
    device_list_buffer = create_string_buffer(buffer_size)

    print("\nScanning for devices...")
    result_code = cmd_list(device_list_buffer, buffer_size)

    if result_code < 0:
        print(f"ERROR: The List function failed with error code: {result_code}")
        sys.exit(1)

    raw_string = device_list_buffer.value.decode("utf-8").strip()

    if not raw_string:
        print("\n>>> No Piezo devices were found.")
        sys.exit(0)

    print(f"\nFound device(s). Raw output from DLL: '{raw_string}'")

    # --- Intelligent Parsing Logic ---
    # Split the string by comma and remove any empty parts from trailing commas.
    parts = [part for part in raw_string.split(",") if part]

    identifiers = []
    for part in parts:
        if is_com_port(part) or is_serial_number(part):
            identifiers.append(part)

    if not identifiers:
        print("\nERROR: Could not parse any valid identifiers (COM port or Serial Number) from the raw string.")
        print("Please check device connections and drivers.")
        sys.exit(1)

    print("\n--- Detected Identifiers ---")
    print("The following identifiers can be used to connect to your devices.")
    print("Use these in your config.ini file for 'piezo_left_serial' and 'piezo_right_serial'.")

    for i, identifier in enumerate(identifiers, 1):
        id_type = "COM Port" if is_com_port(identifier) else "Serial Number"
        print(f"\nDevice #{i}:")
        print(f"  - Identifier: {identifier}")
        print(f"  - Type:       {id_type}")

    print("\n------------------------------")
    print("Recommendation: Assign these identifiers in your config.ini and test the connection in the main app.")


if __name__ == "__main__":
    find_connected_piezos()
