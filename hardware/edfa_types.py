"""Pure, unit-explicit models for the IPG EAD-2K-C read-only integration.

Status bits follow the photographed EAD-2K-C manual, P21-006000 Rev A,
page 29. Status flags must never be treated as an independent safety interlock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto


class EDFAState(Enum):
    DISCONNECTED = auto()
    IDLE = auto()
    ERROR = auto()


class EDFAControlMode(Enum):
    ACC = auto()
    APC = auto()
    AGC = auto()


class PowerUnit(Enum):
    W = "W"
    MW = "mW"
    DBM = "dBm"


class EDFAStateError(RuntimeError):
    """An operation is not permitted in the current backend state."""


class EDFACommunicationError(RuntimeError):
    """A transport call failed; device state must be re-established."""


class EDFAProtocolError(ValueError):
    """A read-only command returned an unrecognized or invalid response."""


@dataclass(frozen=True, slots=True)
class EDFAOpticalPower:
    value: float
    unit: PowerUnit

    def __post_init__(self) -> None:
        if not isinstance(self.unit, PowerUnit):
            raise TypeError("unit must be a PowerUnit")
        if not math.isfinite(self.value):
            raise ValueError("optical power must be finite")
        if self.unit is not PowerUnit.DBM and self.value < 0:
            raise ValueError("linear optical power cannot be negative")


@dataclass(frozen=True, slots=True)
class EDFAStatus:
    """Unmodified 32-bit status word with manual-defined interpretations.

    In particular, bit 5 is described as 'emission is starting' and is NOT
    reliable confirmation of steady optical emission.
    """

    raw_word: int

    def __post_init__(self) -> None:
        if type(self.raw_word) is not int or not 0 <= self.raw_word <= 0xFFFFFFFF:
            raise ValueError("raw_word must be an unsigned 32-bit integer")

    def _bit(self, index: int) -> bool:
        return bool(self.raw_word & (1 << index))

    @property
    def interlock_open(self) -> bool:
        return self._bit(2)

    @property
    def overtemperature(self) -> bool:
        return self._bit(3)

    @property
    def input_power_low(self) -> bool:
        return self._bit(4)

    @property
    def emission_starting(self) -> bool:
        return self._bit(5)

    @property
    def back_reflection_alarm(self) -> bool:
        return self._bit(6)

    @property
    def three_second_startup(self) -> bool:
        return self._bit(14)

    @property
    def logarithmic_power_units(self) -> bool:
        return self._bit(18)

    @property
    def agc_available(self) -> bool:
        return self._bit(20)

    @property
    def input_monitor_available(self) -> bool:
        return self._bit(21)

    @property
    def back_reflection_monitor_available(self) -> bool:
        return self._bit(22)

    @property
    def control_mode(self) -> EDFAControlMode | None:
        active = [
            mode
            for bit, mode in ((0, EDFAControlMode.APC), (9, EDFAControlMode.AGC), (16, EDFAControlMode.ACC))
            if self._bit(bit)
        ]
        return active[0] if len(active) == 1 else None

    @property
    def known_fault_reported(self) -> bool:
        return self.interlock_open or self.overtemperature or self.input_power_low or self.back_reflection_alarm

    @property
    def unassigned_bits(self) -> int:
        defined = (0, 2, 3, 4, 5, 6, 9, 14, 16, 17, 18, 20, 21, 22)
        mask = sum(1 << bit for bit in defined)
        return self.raw_word & (0xFFFFFFFF ^ mask)


@dataclass(frozen=True, slots=True)
class EDFATelemetry:
    """Read-only snapshot. Sequential queries are not atomic."""

    status: EDFAStatus
    output_power: EDFAOpticalPower | None
    diode_current_a: float
    case_temperature_c: float
    acquired_at_utc: datetime
    input_power: EDFAOpticalPower | None = None
    minimum_input_power: EDFAOpticalPower | None = None
    power_setpoint: EDFAOpticalPower | None = None
    back_reflection_percent_of_max: float | None = None
    output_power_non_numeric_reply: str | None = None

    def __post_init__(self) -> None:
        # The real EAD-2K-C returned 'Off' to SOUR:POW? during its
        # first read-only, emission-disabled GPIB qualification. Do not
        # fabricate a measured zero or infer optical safety from this token.
        if self.output_power is None:
            if self.output_power_non_numeric_reply != "Off":
                raise ValueError("Missing output power requires the explicit instrument 'Off' reply")
        elif self.output_power_non_numeric_reply is not None:
            raise ValueError("A numeric output-power reading cannot also report 'Off'")
        if self.acquired_at_utc.tzinfo is None or self.acquired_at_utc.utcoffset() is None:
            raise ValueError("acquired_at_utc must be timezone-aware")
        for name in ("diode_current_a", "case_temperature_c", "back_reflection_percent_of_max"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.diode_current_a < 0:
            raise ValueError("diode_current_a cannot be negative")
        if self.back_reflection_percent_of_max is not None and self.back_reflection_percent_of_max < 0:
            raise ValueError("back_reflection_percent_of_max cannot be negative")
