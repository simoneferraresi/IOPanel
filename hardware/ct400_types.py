from dataclasses import dataclass
from enum import IntEnum


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


@dataclass(frozen=True)
class PowerData:
    """Represents an instantaneous power reading from all CT400 detectors."""

    pout: float
    detectors: "dict[Detector, float]"


class CT400StatusCode(IntEnum):
    """
    Defines specific, known status or error codes from the CT400 device.
    This provides a structured alternative to parsing error strings.
    """

    # Success Codes
    SCAN_COMPLETED = 0

    # In-Progress Codes
    SCAN_RUNNING = 1

    # Error Codes (Maps to negative values from the DLL)
    SCAN_ERROR_GENERIC = -1
    SCAN_ERROR_NO_LASER = -2
    SCAN_ERROR_NO_DETECTOR = -3
    SCAN_ERROR_MIN_WAVELENGTH = -4
    SCAN_ERROR_MAX_WAVELENGTH = -5
    SCAN_ERROR_SWEEP_SPEED = -6
    SCAN_ERROR_COMMUNICATION = -10
    SCAN_ERROR_USER_CANCELLED = -99  # Our own internal code
    UNKNOWN_ERROR = -100  # For any other error


@dataclass(frozen=True)
class InstrumentError:
    """
    A structured object to represent an error from an instrument.
    This is emitted by signals instead of a raw string.
    """

    code: CT400StatusCode
    message: str
    source: str  # e.g., "ScanWorker", "PowerFetchWorker"
