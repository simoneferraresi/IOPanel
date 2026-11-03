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

from hardware.edfa_types import EDFAConfiguration, EDFAOpticalPower, EDFAProtocolError, EDFAStatus, EDFATelemetry, PowerUnit

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
        "SOUR:CURR:SPO?",
        "SOUR:CURR:LIM:HIGH?",
        "SOUR:GAIN:SPO?",
        "SOUR:GAIN:LIM:HIGH?",
        "SOUR:POW:LIM:HIGH?",
    }
)
# These five commands were transcribed in a previous review of IPG
# P21-006000 Rev A pp. 31-32. Firmware Rev 1.1.175 has NOT yet
# physically qualified them; no standard/event queries are included.
EXTENDED_READ_QUERIES = frozenset(
    {
        "SOUR:CURR:SPO?",
        "SOUR:CURR:LIM:HIGH?",
        "SOUR:GAIN:SPO?",
        "SOUR:GAIN:LIM:HIGH?",
        "SOUR:POW:LIM:HIGH?",
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


def parse_output_power(response: str, unit: PowerUnit) -> tuple[EDFAOpticalPower | None, str | None]:
    """Decode the observed 'Off' output reply without converting it to 0 W.

    Only SOUR:POW? is known to return this token. Other telemetry fields
    retain strict numeric parsing until separately qualified on hardware.
    """
    cleaned = _text(response)
    if cleaned.casefold() == "off":
        return None, "Off"
    return EDFAOpticalPower(parse_number(cleaned), unit), None


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

    def read_qualification_value(self, command: str) -> tuple[str, float]:
        """One explicitly selected qualified-for-software candidate; never arbitrary SCPI.

        This is intended for the operator-gated CLI. Unknown commands or
        malformed numeric responses fail without silently inferring support.
        """
        if command not in EXTENDED_READ_QUERIES:
            raise EDFAProtocolError(f"Not an approved extended readback query: {command!r}")
        raw = _text(self._query(command))
        return raw, parse_number(raw)

    def read_configuration(self) -> EDFAConfiguration:
        """Read rarely changing settings only when explicitly requested.

        Never used by the periodic telemetry poll. Gain readbacks are skipped
        if status bit 20 does not advertise AGC availability.
        """
        status = parse_status(self._query("SOUR:POW:STAT?"))
        raw_replies: list[tuple[str, str]] = []

        def numeric(command: str) -> float:
            raw = _text(self._query(command))
            value = parse_number(raw)
            raw_replies.append((command, raw))
            return value

        power_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W
        power_limit = EDFAOpticalPower(numeric("SOUR:POW:LIM:HIGH?"), power_unit)
        current_setpoint = numeric("SOUR:CURR:SPO?")
        current_limit = numeric("SOUR:CURR:LIM:HIGH?")
        gain_setpoint = numeric("SOUR:GAIN:SPO?") if status.agc_available else None
        gain_limit = numeric("SOUR:GAIN:LIM:HIGH?") if status.agc_available else None
        return EDFAConfiguration(
            status=status,
            power_limit=power_limit,
            diode_current_setpoint_a=current_setpoint,
            diode_current_limit_a=current_limit,
            gain_setpoint_db=gain_setpoint,
            gain_limit_db=gain_limit,
            acquired_at_utc=datetime.now(UTC),
            raw_replies=tuple(raw_replies),
        )

    def read_telemetry(self) -> EDFATelemetry:
        status = parse_status(self._query("SOUR:POW:STAT?"))
        power_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W
        input_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.MW

        # Sequential reads are NOT a coherent time-synchronized sample.
        output, output_non_numeric_reply = parse_output_power(self._query("SOUR:POW?"), power_unit)
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
            output_power_non_numeric_reply=output_non_numeric_reply,
            diode_current_a=current,
            case_temperature_c=temperature,
            acquired_at_utc=datetime.now(UTC),
            input_power=input_power,
            minimum_input_power=threshold,
            power_setpoint=setpoint,
            back_reflection_percent_of_max=reflection,
        )
