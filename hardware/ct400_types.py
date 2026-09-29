from dataclasses import dataclass
from enum import Enum, IntEnum, auto


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


class CT400ScanCode(IntEnum):
    """ScanWaitEnd codes documented by CT400 Programming Guide 1.4.

    Unknown values remain plain integers in ScanWaitResult.raw_code.  These
    names describe vendor codes only; application errors have no vendor code.
    """

    SUCCESS = 0
    USER_CANCELLED = 1
    DATA_EXCHANGE_DSP_FAILURE = 2
    WAVELENGTH_REFERENCING_ERROR = 3
    SWITCH_FAILURE = 4
    DSP_COMMUNICATION_FAILURE = 5
    WARNING_100 = 100
    WARNING_101 = 101
    WARNING_102 = 102
    WARNING_103 = 103
    WARNING_104 = 104
    WARNING_106 = 106
    WARNING_108 = 108
    WARNING_109 = 109
    WARNING_110 = 110
    WARNING_111 = 111
    WARNING_112 = 112
    WARNING_113 = 113
    WARNING_114 = 114
    WARNING_115 = 115
    WARNING_117 = 117
    WARNING_118 = 118
    WARNING_119 = 119
    WARNING_120 = 120
    WARNING_121 = 121
    WARNING_122 = 122
    WARNING_123 = 123
    WARNING_124 = 124
    # Scan is performed after sampling resolution is adjusted.
    WARNING_999 = 999


class CT400ScanResultKind(Enum):
    SUCCESS = auto()
    USER_CANCELLED = auto()
    FATAL_ERROR = auto()
    WARNING = auto()
    UNEXPECTED = auto()


_CT400_WARNING_CODES = frozenset(
    (*range(100, 105), 106, *range(108, 116), *range(117, 125), 999)
)


@dataclass(frozen=True)
class ScanWaitResult:
    """Raw ScanWaitEnd result plus a classification from the vendor guide."""

    raw_code: int
    error_message: str

    @property
    def kind(self) -> CT400ScanResultKind:
        if self.raw_code == CT400ScanCode.SUCCESS:
            return CT400ScanResultKind.SUCCESS
        if self.raw_code == CT400ScanCode.USER_CANCELLED:
            return CT400ScanResultKind.USER_CANCELLED
        if 2 <= self.raw_code <= 5:
            return CT400ScanResultKind.FATAL_ERROR
        if self.raw_code in _CT400_WARNING_CODES:
            return CT400ScanResultKind.WARNING
        return CT400ScanResultKind.UNEXPECTED


@dataclass(frozen=True)
class InstrumentError:
    """
    A structured object to represent an error from an instrument.
    This is emitted by signals instead of a raw string.
    """

    code: int | None
    message: str
    source: str  # e.g., "ScanWorker", "PowerFetchWorker"
    kind: CT400ScanResultKind | None = None

    @property
    def code_name(self) -> str:
        if self.code is None:
            return "APPLICATION_ERROR"
        try:
            return CT400ScanCode(self.code).name
        except ValueError:
            return str(self.code)
