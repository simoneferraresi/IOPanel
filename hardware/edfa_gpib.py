"""Read-only IPG EAD-2K-C GPIB commands, with an injected query session.

No VISA resource is opened here. Physical VISA session creation and connection
qualification belong to a later milestone. This module cannot send writes or
emission-control commands.

Sources: IPG P21-006000 Rev A, pages 29, 31-32.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Protocol

from hardware.edfa_types import EDFAOpticalPower, EDFAProtocolError, EDFAStatus, EDFATelemetry, PowerUnit

_READ_QUERIES = frozenset(
    {
        "*IDN?",
        "SOUR:POW:STAT?",
        "INP?",
        "INP:LIM:LOW?",
        "SOUR:POW?",
        "SOUR:POW:SPO?",
        "SOUR:POW:BACK?",
        "SOUR:CURR?",
        "SOUR:TEMP:LEV?",
    }
)
_INTEGER = re.compile(r"(?:STA:\s*)?([0-9]+)", flags=re.IGNORECASE)


class GPIBQuerySession(Protocol):
    """VISA-style query capability, supplied by a caller (fake during M1)."""

    def query(self, message: str) -> str: ...


def _text(response: str) -> str:
    if not isinstance(response, str):
        raise EDFAProtocolError("GPIB response must be text")
    cleaned = response.strip()
    if not cleaned or any(ord(char) < 32 or ord(char) > 126 for char in cleaned):
        raise EDFAProtocolError("GPIB response is empty or contains non-printable characters")
    return cleaned


def parse_identity(response: str) -> str:
    """Preserve the entire identification string; no vendor-specific fields inferred."""
    return _text(response)


def parse_number(response: str) -> float:
    cleaned = _text(response)
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise EDFAProtocolError(f"Expected a bare numeric GPIB response; got {cleaned!r}") from exc
    if not math.isfinite(value):
        raise EDFAProtocolError("Nonfinite GPIB response")
    return value


def parse_status(response: str) -> EDFAStatus:
    """Manual p. 32 refers status representation to the RS-232 page 29.

    Accept a bare decimal word or the RS-232-style STA: prefix; do not
    guess binary/hex encodings without physical response evidence.
    """
    cleaned = _text(response)
    match = _INTEGER.fullmatch(cleaned)
    if match is None:
        raise EDFAProtocolError(f"Expected decimal status word; got {cleaned!r}")
    try:
        return EDFAStatus(int(match.group(1), 10))
    except ValueError as exc:
        raise EDFAProtocolError(f"Invalid unsigned 32-bit EDFA status: {cleaned!r}") from exc


class GPIBReadOnlyReader:
    """Command allowlist and parsing only. Does not own or open the session."""

    def __init__(self, session: GPIBQuerySession):
        self._session = session

    def _query(self, command: str) -> str:
        if command not in _READ_QUERIES:
            raise EDFAProtocolError(f"Query is not in the explicit read-only allowlist: {command!r}")
        return self._session.query(command)

    def identify(self) -> str:
        return parse_identity(self._query("*IDN?"))

    def read_telemetry(self) -> EDFATelemetry:
        status = parse_status(self._query("SOUR:POW:STAT?"))
        power_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W
        input_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.MW

        # Sequential reads are NOT a coherent time-synchronized sample.
        output = EDFAOpticalPower(parse_number(self._query("SOUR:POW?")), power_unit)
        setpoint = EDFAOpticalPower(parse_number(self._query("SOUR:POW:SPO?")), power_unit)
        current = parse_number(self._query("SOUR:CURR?"))
        temperature = parse_number(self._query("SOUR:TEMP:LEV?"))
        input_power = (
            EDFAOpticalPower(parse_number(self._query("INP?")), input_unit) if status.input_monitor_available else None
        )
        threshold = (
            EDFAOpticalPower(parse_number(self._query("INP:LIM:LOW?")), input_unit)
            if status.input_monitor_available
            else None
        )
        reflection = parse_number(self._query("SOUR:POW:BACK?")) if status.back_reflection_monitor_available else None
        return EDFATelemetry(
            status=status,
            output_power=output,
            diode_current_a=current,
            case_temperature_c=temperature,
            acquired_at_utc=datetime.now(UTC),
            input_power=input_power,
            minimum_input_power=threshold,
            power_setpoint=setpoint,
            back_reflection_percent_of_max=reflection,
        )
