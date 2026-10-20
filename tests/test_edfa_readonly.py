"""Read-only EDFA unit tests: no VISA, PySide6 or physical instruments."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hardware.dummy_edfa import DummyEDFA
from hardware.edfa_gpib import GPIBReadOnlyReader, parse_identity, parse_number, parse_status
from hardware.edfa_types import (
    EDFACommunicationError,
    EDFAControlMode,
    EDFAOpticalPower,
    EDFAProtocolError,
    EDFAState,
    EDFAStateError,
    EDFAStatus,
    EDFATelemetry,
    PowerUnit,
)


class FakeGPIB:
    def __init__(self, replies: dict[str, str]):
        self.replies = replies
        self.commands: list[str] = []

    def query(self, message: str) -> str:
        self.commands.append(message)
        return self.replies[message]


def _replies(*, status: int = (1 << 0) | (1 << 21) | (1 << 22)) -> dict[str, str]:
    return {
        "*IDN?": "IPG, EAD-2K-C, FA160101",
        "SOUR:POW:STAT?": f"{status}\r\n",
        "SOUR:POW?": "0.0",
        "SOUR:POW:SPO?": "1.2",
        "SOUR:CURR?": "0.0",
        "SOUR:TEMP:LEV?": "28.9",
        "INP?": "1.23",
        "INP:LIM:LOW?": "0.50",
        "SOUR:POW:BACK?": "27",
    }


@pytest.mark.parametrize("raw", ["", "\r\n", "nan", "inf", "-inf", "1,25", "1.0 W", "not a number"])
def test_number_parser_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(EDFAProtocolError):
        parse_number(raw)


def test_parse_number_accepts_manual_numeric_examples() -> None:
    assert parse_number("2.31\r\n") == pytest.approx(2.31)
    assert parse_number("-3.0") == pytest.approx(-3.0)


@pytest.mark.parametrize("raw", ["-1", "4294967296", "0x8000", "1.25", "STA: something", "1\n2", ""])
def test_status_parser_rejects_invalid_words(raw: str) -> None:
    with pytest.raises(EDFAProtocolError):
        parse_status(raw)


@pytest.mark.parametrize("raw", ["123", "STA: 123", "sta:123\r\n"])
def test_status_accepts_documented_or_prefixed_decimal(raw: str) -> None:
    assert parse_status(raw).raw_word == 123


def test_status_bits_and_conflicting_modes_are_preserved() -> None:
    word = sum(1 << i for i in (0, 2, 3, 4, 5, 6, 14, 17, 18, 20, 21, 22, 31))
    status = EDFAStatus(word)
    assert status.control_mode is EDFAControlMode.APC
    assert status.interlock_open and status.overtemperature and status.input_power_low
    assert status.emission_starting and status.three_second_startup and status.back_reflection_alarm
    assert status.logarithmic_power_units and status.input_monitor_available and status.agc_available
    assert status.known_fault_reported
    assert status.unassigned_bits == 1 << 31
    assert EDFAStatus((1 << 0) | (1 << 16)).control_mode is None
    assert EDFAStatus(0).control_mode is None


@pytest.mark.parametrize("word", [-1, 2**32, 1.0, True])
def test_status_rejects_invalid_words(word: int) -> None:
    with pytest.raises(ValueError):
        EDFAStatus(word)


def test_read_only_reader_uses_exact_allowlist_and_preserves_units() -> None:
    session = FakeGPIB(_replies())
    reader = GPIBReadOnlyReader(session)
    assert reader.identify() == "IPG, EAD-2K-C, FA160101"
    report = reader.read_telemetry()
    assert report.status.control_mode is EDFAControlMode.APC
    assert report.output_power == EDFAOpticalPower(0.0, PowerUnit.W)
    assert report.input_power == EDFAOpticalPower(1.23, PowerUnit.MW)
    assert report.minimum_input_power == EDFAOpticalPower(0.5, PowerUnit.MW)
    assert report.power_setpoint == EDFAOpticalPower(1.2, PowerUnit.W)
    assert report.back_reflection_percent_of_max == 27.0
    assert report.case_temperature_c == 28.9
    assert report.acquired_at_utc.utcoffset() is not None
    assert session.commands == [
        "*IDN?",
        "SOUR:POW:STAT?",
        "SOUR:POW?",
        "SOUR:POW:SPO?",
        "SOUR:CURR?",
        "SOUR:TEMP:LEV?",
        "INP?",
        "INP:LIM:LOW?",
        "SOUR:POW:BACK?",
    ]
    assert all(command.endswith("?") for command in session.commands)


def test_read_only_reader_skips_unavailable_monitors() -> None:
    session = FakeGPIB(_replies(status=1 << 16))
    report = GPIBReadOnlyReader(session).read_telemetry()
    assert report.status.control_mode is EDFAControlMode.ACC
    assert report.input_power is None
    assert report.minimum_input_power is None
    assert report.back_reflection_percent_of_max is None
    assert "INP?" not in session.commands and "SOUR:POW:BACK?" not in session.commands


def test_logarithmic_units_are_not_confused_with_watts() -> None:
    session = FakeGPIB(_replies(status=(1 << 18) | (1 << 21)))
    session.replies["SOUR:POW?"] = "33.0"
    session.replies["SOUR:POW:SPO?"] = "32.0"
    session.replies["INP?"] = "0.0"
    session.replies["INP:LIM:LOW?"] = "-3.0"
    report = GPIBReadOnlyReader(session).read_telemetry()
    assert report.output_power.unit is PowerUnit.DBM
    assert report.input_power == EDFAOpticalPower(0, PowerUnit.DBM)
    assert report.minimum_input_power == EDFAOpticalPower(-3, PowerUnit.DBM)


def test_invalid_response_fails_closed_and_never_writes() -> None:
    session = FakeGPIB(_replies())
    session.replies["SOUR:POW?"] = "not a power"
    with pytest.raises(EDFAProtocolError):
        GPIBReadOnlyReader(session).read_telemetry()
    assert session.commands == ["SOUR:POW:STAT?", "SOUR:POW?"]
    with pytest.raises(EDFAProtocolError):
        GPIBReadOnlyReader(session)._query("SOUR:POW:STAT 1")
    assert session.commands == ["SOUR:POW:STAT?", "SOUR:POW?"]


def test_dummy_lifecycle_and_fault_injection() -> None:
    edfa = DummyEDFA()
    assert edfa.state is EDFAState.DISCONNECTED
    with pytest.raises(EDFAStateError):
        edfa.read_telemetry()
    edfa.connect()
    assert edfa.is_connected()
    assert "SIMULATED" in edfa.identify()
    assert edfa.read_telemetry().output_power.value == 0.0
    with pytest.raises(EDFAStateError):
        edfa.connect()
    edfa.set_status_word((1 << 2) | (1 << 4))
    report = edfa.read_telemetry()
    assert report.status.interlock_open and report.status.input_power_low
    assert report.input_power is None
    edfa.close()
    edfa.close()
    assert not edfa.is_connected()


def test_dummy_error_recovery_requires_deliberate_close() -> None:
    edfa = DummyEDFA(fail_operations={"read_telemetry"})
    edfa.connect()
    with pytest.raises(EDFACommunicationError):
        edfa.read_telemetry()
    assert edfa.state is EDFAState.ERROR
    with pytest.raises(EDFAStateError):
        edfa.identify()
    edfa.close()
    assert edfa.state is EDFAState.DISCONNECTED


def test_dummy_connection_failure_remains_disconnected() -> None:
    edfa = DummyEDFA(fail_operations={"connect"})
    with pytest.raises(EDFACommunicationError):
        edfa.connect()
    assert edfa.state is EDFAState.DISCONNECTED


def test_model_validation_rejects_invalid_telemetry() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError):
        EDFAOpticalPower(float("nan"), PowerUnit.W)
    with pytest.raises(ValueError):
        EDFAOpticalPower(-1.0, PowerUnit.MW)
    with pytest.raises(ValueError):
        EDFATelemetry(EDFAStatus(0), EDFAOpticalPower(0, PowerUnit.W), -1.0, 25.0, now)
    assert parse_identity("IPG,EAD-2K-C,FA160101\r\n") == "IPG,EAD-2K-C,FA160101"
    with pytest.raises(EDFAProtocolError):
        parse_identity("\x00")


@pytest.mark.parametrize("raw", ["Off", "off", "OFF", " Off\\r\\n "])
def test_output_power_off_is_missing_observation_not_zero(raw: str) -> None:
    from hardware.edfa_gpib import parse_output_power

    power, reply = parse_output_power(raw, PowerUnit.W)
    assert power is None
    assert reply == "Off"


@pytest.mark.parametrize("raw", ["Off!", "Output Off", "Error", "Disabled", "", "nan", "inf"])
def test_output_power_unknown_non_numeric_still_rejected(raw: str) -> None:
    from hardware.edfa_gpib import parse_output_power

    with pytest.raises(EDFAProtocolError):
        parse_output_power(raw, PowerUnit.W)


def test_output_power_numeric_zero_remains_distinct_from_off() -> None:
    from hardware.edfa_gpib import parse_output_power

    power, reply = parse_output_power("0.0", PowerUnit.W)
    assert power == EDFAOpticalPower(0.0, PowerUnit.W)
    assert reply is None


def test_telemetry_preserves_non_numeric_output_and_other_measurements() -> None:
    session = FakeGPIB(_replies())
    session.replies["SOUR:POW?"] = "Off"
    report = GPIBReadOnlyReader(session).read_telemetry()
    assert report.output_power is None
    assert report.output_power_non_numeric_reply == "Off"
    assert report.diode_current_a == 0.0
    assert report.case_temperature_c == 28.9
    assert report.power_setpoint == EDFAOpticalPower(1.2, PowerUnit.W)
    assert all(command.endswith("?") for command in session.commands)


def test_telemetry_rejects_ambiguous_output_value_and_off_marker() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="requires"):
        EDFATelemetry(EDFAStatus(0), None, 0.0, 25.0, now)
    with pytest.raises(ValueError, match="cannot also"):
        EDFATelemetry(
            EDFAStatus(0), EDFAOpticalPower(0.0, PowerUnit.W), 0.0, 25.0, now,
            output_power_non_numeric_reply="Off",
        )
