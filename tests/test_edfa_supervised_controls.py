"""M4 regression tests: synthetic VISA and simulator only, never lab hardware."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from config_model import AppConfig
from hardware.dummy_edfa import DummyEDFA
from hardware.edfa_controls import (
    EDFAEmissionAction,
    EDFAWriteKind,
    EDFAWriteOutcome,
    EDFAWritePolicy,
    EDFAWriteRequest,
    format_emission_command,
    format_write_command,
)
from hardware.edfa_types import EDFAState, EDFAStateError, PowerUnit
from hardware.ipg_ead2kc import IPGEAD2KCGPIB

APC = (1 << 0) | (1 << 20) | (1 << 21)
AGC = (1 << 9) | (1 << 20) | (1 << 21)
ACC = (1 << 16) | (1 << 20) | (1 << 21)
SITE = EDFAWritePolicy(True, "LAB-EDFA-M4-001", 0.5, 20.0, 0.5)


def approved(kind: EDFAWriteKind, value: float | PowerUnit, unit: PowerUnit | None = None) -> EDFAWriteRequest:
    return EDFAWriteRequest(kind, value, unit, operator_confirmed=True, emission_off_attested=True)


class WriteSession:
    def __init__(self, status: int = APC):
        self.status = status
        self.output = "Off"
        self.pow = 0.2
        self.gain = 15.0
        self.current = 0.25
        self.closed = False
        self.timeout = 0
        self.write_termination = None
        self.read_termination = None
        self.writes: list[str] = []
        self.queries: list[str] = []
        self.fail_write_before = False
        self.fail_write_after = False
        self.fail_post_read = False
        self.mismatch = False
        self.flip_status_on_read = False

    def query(self, message: str) -> str:
        self.queries.append(message)
        if self.fail_post_read and self.writes:
            raise OSError("injected post-write timeout")
        if self.flip_status_on_read and len(self.queries) >= 3:
            self.status ^= (1 << 18)
        dBm = bool(self.status & (1 << 18))
        power = (lambda w: str(10 * __import__("math").log10(w * 1000) if dBm else w))
        responses = {
            "*IDN?": "IPG EAD-2K-C, Rev 1.1.175",
            "SOUR:POW:STAT?": str(self.status),
            "SOUR:POW?": self.output,
            "SOUR:POW:SPO?": power(self.pow),
            "SOUR:POW:LIM:HIGH?": power(2.2),
            "SOUR:CURR?": "0.021",
            "SOUR:CURR:SPO?": str(self.current),
            "SOUR:CURR:LIM:HIGH?": "2.4",
            "SOUR:GAIN:SPO?": str(self.gain),
            "SOUR:GAIN:LIM:HIGH?": "38",
            "SOUR:TEMP:LEV?": "23.4",
            "INP?": "0.08",
            "INP:LIM:LOW?": "0.23",
        }
        return responses[message]

    def write(self, message: str) -> None:
        self.writes.append(message)
        if self.fail_write_before:
            raise OSError("ambiguous VISA send timeout")
        prefix, _, argument = message.partition(" ")
        if not self.mismatch:
            if prefix == "SOUR:POW":
                self.pow = float(argument) if not (self.status & (1 << 18)) else 10 ** ((float(argument) - 30) / 10)
            elif prefix == "SOUR:GAIN":
                self.gain = float(argument)
            elif prefix == "SOUR:CURR":
                self.current = float(argument)
            elif prefix == "UNIT:POW":
                self.status = self.status | (1 << 18) if argument == "DBm" else self.status & ~(1 << 18)
            else:
                raise AssertionError("Unknown write sent to fake VISA")
        if self.fail_write_after:
            raise OSError("write perhaps reached the device")

    def close(self) -> None:
        self.closed = True


class Manager:
    def __init__(self, session: WriteSession):
        self.session = session
        self.closed = False

    def open_resource(self, _name: str) -> WriteSession:
        return self.session

    def close(self) -> None:
        self.closed = True


def connect(session: WriteSession, policy: EDFAWritePolicy = SITE) -> IPGEAD2KCGPIB:
    device = IPGEAD2KCGPIB("GPIB0::12::INSTR", manager_factory=lambda: Manager(session))
    device.configure_write_policy(policy)
    assert session.writes == []
    device.connect()
    assert session.writes == []
    return device


@pytest.mark.parametrize(
    ("request", "command"),
    [
        (approved(EDFAWriteKind.POWER, 0.123456789, PowerUnit.W), "SOUR:POW 0.123456789"),
        (approved(EDFAWriteKind.POWER, 27.1, PowerUnit.DBM), "SOUR:POW 27.1"),
        (approved(EDFAWriteKind.GAIN, -2.5), "SOUR:GAIN -2.5"),
        (approved(EDFAWriteKind.CURRENT, 0.125), "SOUR:CURR 0.125"),
        (approved(EDFAWriteKind.POWER_UNITS, PowerUnit.DBM), "UNIT:POW DBm"),
        (approved(EDFAWriteKind.POWER_UNITS, PowerUnit.W), "UNIT:POW W"),
    ],
)
def test_exact_allowlisted_commands(request, command):
    assert format_write_command(request) == command


@pytest.mark.parametrize(
    ("kind", "value", "unit"),
    [
        (EDFAWriteKind.POWER, float("nan"), PowerUnit.W),
        (EDFAWriteKind.POWER, float("inf"), PowerUnit.W),
        (EDFAWriteKind.POWER, -1.0, PowerUnit.W),
        (EDFAWriteKind.POWER, 1.0, PowerUnit.MW),
        (EDFAWriteKind.POWER, 1.0, None),
        (EDFAWriteKind.GAIN, "SOUR:POW:STAT 1", None),
        (EDFAWriteKind.CURRENT, -0.1, None),
        (EDFAWriteKind.CURRENT, True, None),
        (EDFAWriteKind.POWER_UNITS, "DBm", None),
    ],
)
def test_invalid_payload_never_becomes_scpi(kind, value, unit):
    with pytest.raises((ValueError, TypeError)):
        EDFAWriteRequest(kind, value, unit, operator_confirmed=True)


@pytest.mark.parametrize("action", [EDFAEmissionAction.OFF, EDFAEmissionAction.ON])
def test_emission_representation_only(action):
    assert format_emission_command(action) == f"SOUR:POW:STAT {action.value}"
    with pytest.raises(ValueError):
        format_emission_command("ON")


def test_site_opt_in_requires_all_approved_ceilings_and_reference():
    with pytest.raises(ValueError):
        EDFAWritePolicy(True, "", 0.5, 20.0, 0.5)
    with pytest.raises(ValueError):
        EDFAWritePolicy(True, "LAB-EDFA-M4-001", None, 20.0, 0.5)
    with pytest.raises(ValidationError):
        AppConfig(instruments={"edfa_m4_write_enabled": True})
    cfg = AppConfig(instruments={
        "edfa_m4_write_enabled": True, "edfa_m4_site_reference": "LAB-EDFA-M4-001",
        "edfa_m4_approved_power_w": 0.5, "edfa_m4_approved_gain_db": 20,
        "edfa_m4_approved_current_a": 0.5,
    })
    assert cfg.instruments.edfa_m4_write_enabled
    assert not AppConfig().instruments.edfa_m4_write_enabled


def test_physical_writes_disabled_by_default_even_when_connected():
    session = WriteSession()
    device = connect(session, EDFAWritePolicy())
    result = device.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W))
    assert result.outcome is EDFAWriteOutcome.REJECTED
    assert session.writes == []
    device.close()


def test_physical_confirmation_and_off_attestation_both_required():
    session = WriteSession()
    device = connect(session)
    for req in (
        EDFAWriteRequest(EDFAWriteKind.POWER, 0.3, PowerUnit.W),
        EDFAWriteRequest(EDFAWriteKind.POWER, 0.3, PowerUnit.W, operator_confirmed=True),
    ):
        assert device.apply_control(req).outcome is EDFAWriteOutcome.REJECTED
    assert session.writes == []
    device.close()


def test_success_fresh_preflight_single_write_and_post_verification():
    session = WriteSession()
    device = connect(session)
    result = device.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W))
    assert result.outcome is EDFAWriteOutcome.CONFIRMED
    assert result.before == pytest.approx(0.2)
    assert result.after == pytest.approx(0.3)
    assert session.writes == ["SOUR:POW 0.3"]
    assert session.queries.count("SOUR:POW:STAT?") >= 6
    assert result.configuration is not None
    assert device.state is EDFAState.IDLE
    device.close()


@pytest.mark.parametrize(
    ("status", "request", "command"),
    [
        (AGC, approved(EDFAWriteKind.GAIN, 19.0), "SOUR:GAIN 19"),
        (ACC, approved(EDFAWriteKind.CURRENT, 0.35), "SOUR:CURR 0.35"),
    ],
)
def test_mode_specific_gain_and_current_writes(status, request, command):
    session = WriteSession(status)
    device = connect(session)
    result = device.apply_control(request)
    assert result.outcome is EDFAWriteOutcome.CONFIRMED
    assert session.writes == [command]
    device.close()


def test_mode_limit_and_emission_guards_refuse_without_writes():
    for status, request in [
        (APC, approved(EDFAWriteKind.GAIN, 16)),
        (APC, approved(EDFAWriteKind.CURRENT, 0.35)),
        (APC, approved(EDFAWriteKind.POWER, 1.5, PowerUnit.W)),  # above local ceiling, below hardware max
        (APC | (1 << 5), approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W)),
        (APC | (1 << 6), approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W)),
    ]:
        session = WriteSession(status)
        device = connect(session)
        result = device.apply_control(request)
        assert result.outcome is EDFAWriteOutcome.REJECTED
        assert session.writes == []
        device.close()
    session = WriteSession()
    session.output = "0.0"
    device = connect(session)
    assert device.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W)).outcome is EDFAWriteOutcome.REJECTED
    assert not session.writes
    device.close()


def test_unit_switch_is_a_separate_explicit_command_and_requeries_power_fields():
    session = WriteSession()
    device = connect(session)
    result = device.apply_control(approved(EDFAWriteKind.POWER_UNITS, PowerUnit.DBM))
    assert result.outcome is EDFAWriteOutcome.CONFIRMED
    assert result.before is PowerUnit.W
    assert result.after is PowerUnit.DBM
    assert result.configuration is not None and result.configuration.power_limit.unit is PowerUnit.DBM
    assert result.telemetry is not None and result.telemetry.power_setpoint.unit is PowerUnit.DBM
    assert session.writes == ["UNIT:POW DBm"]
    device.close()


@pytest.mark.parametrize("problem", ["fail_write_before", "fail_write_after", "fail_post_read", "mismatch"])
def test_ambiguous_write_and_mismatch_require_explicit_recovery(problem):
    session = WriteSession()
    setattr(session, problem, True)
    device = connect(session)
    result = device.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W))
    assert result.outcome in (EDFAWriteOutcome.UNKNOWN, EDFAWriteOutcome.MISMATCH)
    assert len(session.writes) == 1
    assert device.state is EDFAState.ERROR
    with pytest.raises(EDFAStateError):
        device.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W))
    device.close()


def test_cancelled_request_and_unstable_status_do_not_write():
    session = WriteSession()
    device = connect(session)
    req = approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W)
    req.cancelled.set()
    assert device.apply_control(req).outcome is EDFAWriteOutcome.CANCELLED
    assert not session.writes
    device.close()

    unstable = WriteSession()
    unstable.flip_status_on_read = True
    device = connect(unstable)
    with pytest.raises(Exception, match="preflight|recovery"):
        device.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W))
    assert not unstable.writes
    device.close()


def test_simulator_setters_emission_lock_and_failure_injection():
    sim = DummyEDFA(status_word=APC)
    assert sim.write_commands == []
    sim.connect()
    assert not sim.write_commands
    assert sim.apply_control(approved(EDFAWriteKind.POWER, 0.3, PowerUnit.W)).outcome is EDFAWriteOutcome.CONFIRMED
    assert sim.write_commands == ["SOUR:POW 0.3"]
    assert sim.simulate_emission_action(EDFAEmissionAction.ON) == "SOUR:POW:STAT 1"
    assert sim.apply_control(approved(EDFAWriteKind.POWER, 0.4, PowerUnit.W)).outcome is EDFAWriteOutcome.REJECTED
    assert sim.simulate_emission_action(EDFAEmissionAction.OFF) == "SOUR:POW:STAT 0"
    sim.set_failure_injection({"post_write_read"})
    result = sim.apply_control(approved(EDFAWriteKind.POWER, 0.4, PowerUnit.W))
    assert result.outcome is EDFAWriteOutcome.UNKNOWN
    assert sim.state is EDFAState.ERROR
    sim.close()
