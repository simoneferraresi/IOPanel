"""M4-A GPIB forensic diagnostics: fake VISA only; never opens laboratory hardware."""

from __future__ import annotations

import logging

import pytest

from config_model import AppConfig
from hardware.edfa_types import EDFACommunicationError, EDFAState, EDFAStateError
from hardware.ipg_ead2kc import IPGEAD2KCGPIB
from tools.edfa_gpib_diagnostics import _QueryOnlySession, _Recorder, run_diagnostics

STATUS = (1 << 0) | (1 << 20) | (1 << 21) | (1 << 22)


class NoListeners(OSError):
    error_code = -1073807265


class FakeVisa:
    def __init__(self, *, fail_command: str = "", fail_phase: str = "", fail_final_status: bool = False) -> None:
        self.timeout = 0
        self.read_termination = None
        self.write_termination = None
        self.commands: list[str] = []
        self.writes: list[str] = []
        self.closed = False
        self.fail_command = fail_command
        self.fail_phase = fail_phase
        self.fail_final_status = fail_final_status

    def query(self, message: str) -> str:
        self.commands.append(message)
        if self.fail_final_status and message == "SOUR:POW:STAT?" and self.commands.count(message) == 4:
            raise NoListeners("injected status-check viWrite error")
        if message == self.fail_command and self.fail_phase in ("viWrite", "viRead"):
            raise NoListeners(f"injected {self.fail_phase} error")
        values = {
            "*IDN?": "IPG EAD-2K-C, Rev 1.1.175",
            "SOUR:POW:STAT?": str(STATUS),
            "SOUR:POW?": "Off",
            "SOUR:POW:SPO?": "0.2",
            "SOUR:CURR?": "0.021",
            "SOUR:TEMP:LEV?": "23.8",
            "INP?": "0.03",
            "INP:LIM:LOW?": "0.23",
            "SOUR:POW:BACK?": "0",
            "SOUR:POW:LIM:HIGH?": "2.2",
            "SOUR:CURR:SPO?": "0.7",
            "SOUR:CURR:LIM:HIGH?": "2.4",
            "SOUR:GAIN:SPO?": "37.0",
            "SOUR:GAIN:LIM:HIGH?": "38.0",
        }
        return values[message]

    def write(self, message: str) -> None:
        self.writes.append(message)
        raise AssertionError("Diagnostic attempted to send a physical setter")

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self, visa: FakeVisa) -> None:
        self.visa = visa
        self.closed = False
        self.opened = []

    def open_resource(self, resource: str) -> FakeVisa:
        self.opened.append(resource)
        return self.visa

    def close(self) -> None:
        self.closed = True


def config(*, writes_enabled: bool = False) -> AppConfig:
    instrument = {"edfa_backend": "physical", "edfa_gpib_resource_name": "GPIB0::12::INSTR"}
    if writes_enabled:
        instrument.update(
            {
                "edfa_m4_write_enabled": "true",
                "edfa_m4_site_reference": "SAFETY-0001",
                "edfa_m4_approved_power_w": "0.5",
                "edfa_m4_approved_gain_db": "20",
                "edfa_m4_approved_current_a": "0.5",
            }
        )
    return AppConfig.from_ini_dict({"Instruments": instrument})


def test_dry_run_never_initializes_a_visa_manager() -> None:
    def forbidden():
        raise AssertionError("dry run must not touch VISA")

    result = run_diagnostics(config(), manager_factory=forbidden)
    assert result["mode"] == "dry-run"


def test_physical_approval_and_write_disabled_are_mandatory() -> None:
    with pytest.raises(ValueError, match="operator-confirmed"):
        run_diagnostics(config(), connect_readonly=True)
    with pytest.raises(ValueError, match="write policy"):
        run_diagnostics(config(writes_enabled=True), connect_readonly=True, operator_confirmed=True)


def test_successful_single_session_has_exact_qualified_query_sequence() -> None:
    visa = FakeVisa()
    manager = FakeManager(visa)
    result = run_diagnostics(
        config(),
        connect_readonly=True,
        operator_confirmed=True,
        manager_factory=lambda: manager,
    )
    assert result["status"] == "PASSED_READ_ONLY_ONLY"
    assert result["completed_queries"] == 16
    assert visa.commands[0] == "*IDN?"
    assert visa.commands[-1] == "SOUR:POW:STAT?"
    assert manager.opened == ["GPIB0::12::INSTR"]
    assert visa.closed and manager.closed and not visa.writes
    assert len([e for e in result["events"] if e["event"] == "query_success"]) == 16


@pytest.mark.parametrize("phase", ["viWrite", "viRead"])
def test_no_listeners_is_reported_and_never_retried(phase: str) -> None:
    visa = FakeVisa(fail_command="SOUR:GAIN:SPO?", fail_phase=phase)
    result = run_diagnostics(
        config(),
        connect_readonly=True,
        operator_confirmed=True,
        sessions=3,
        manager_factory=lambda: FakeManager(visa),
    )
    assert result["status"] == "FAILED"
    assert visa.commands.count("SOUR:GAIN:SPO?") == 1
    assert visa.closed
    errors = [e for e in result["events"] if e["event"] == "query_error"]
    assert len(errors) == 1
    assert errors[0]["visa_code"] == -1073807265
    assert result["completed_queries"] == 14


def test_failure_at_configuration_consistency_check_is_fatal() -> None:
    visa = FakeVisa(fail_final_status=True)
    result = run_diagnostics(
        config(),
        connect_readonly=True,
        operator_confirmed=True,
        manager_factory=lambda: FakeManager(visa),
    )
    assert result["status"] == "FAILED"
    assert result["completed_queries"] == 16
    assert len([e for e in result["events"] if e["event"] == "query_error"]) == 1


def test_query_budget_is_enforced_before_an_extra_command() -> None:
    visa = FakeVisa()
    result = run_diagnostics(
        config(),
        connect_readonly=True,
        operator_confirmed=True,
        max_queries=5,
        manager_factory=lambda: FakeManager(visa),
    )
    assert result["status"] == "FAILED"
    assert result["completed_queries"] == 5
    assert len(visa.commands) == 5
    assert visa.closed


def test_reopening_uses_distinct_sessions_and_never_sends_setters() -> None:
    visas: list[FakeVisa] = []

    def factory() -> FakeManager:
        visa = FakeVisa()
        visas.append(visa)
        return FakeManager(visa)

    result = run_diagnostics(
        config(),
        connect_readonly=True,
        operator_confirmed=True,
        plan="telemetry",
        sessions=2,
        manager_factory=factory,
    )
    assert result["status"] == "PASSED_READ_ONLY_ONLY"
    assert result["completed_queries"] == 18
    assert len(visas) == 2
    assert all(v.closed and not v.writes for v in visas)


def test_query_only_wrapper_blocks_every_setter() -> None:
    visa = FakeVisa()
    wrapper = _QueryOnlySession(visa, _Recorder(10, 10, 0), 1)
    with pytest.raises(RuntimeError, match="forbids"):
        wrapper.write("SOUR:POW 2.2")
    with pytest.raises(ValueError, match="Unapproved"):
        wrapper.query("SOUR:POW:STAT 1")
    assert visa.commands == [] and visa.writes == []


def test_driver_transaction_audit_records_exact_failure_context(caplog: pytest.LogCaptureFixture) -> None:
    visa = FakeVisa(fail_command="SOUR:POW:SPO?", fail_phase="viWrite")
    device = IPGEAD2KCGPIB("GPIB0::12::INSTR", manager_factory=lambda: FakeManager(visa))
    caplog.set_level(logging.DEBUG, logger="LabApp.IPGEAD2KC")
    device.connect()
    with pytest.raises(EDFACommunicationError):
        device.read_telemetry()
    assert device.state is EDFAState.ERROR
    with pytest.raises(EDFAStateError):
        device.read_telemetry()
    assert "visa_hex=0xBFFF005F" in caplog.text
    assert "command='SOUR:POW:SPO?'" in caplog.text
    assert "thread=" in caplog.text
    assert visa.commands.count("SOUR:POW:SPO?") == 1
    device.close()

def test_opt_in_interquery_delay_is_applied_only_between_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import edfa_gpib_diagnostics as diagnostics

    slept: list[float] = []
    monkeypatch.setattr(diagnostics.time, "sleep", slept.append)
    visa = FakeVisa()
    result = run_diagnostics(
        config(),
        connect_readonly=True,
        operator_confirmed=True,
        interquery_delay_ms=50,
        manager_factory=lambda: FakeManager(visa),
    )
    assert result["status"] == "PASSED_READ_ONLY_ONLY"
    assert slept == [0.05] * 15


def test_invalid_pacing_parameter_rejected_without_visa_manager() -> None:
    def forbidden():
        raise AssertionError("unapproved pacing must not create VISA manager")

    with pytest.raises(ValueError, match="predeclared diagnostic delay"):
        run_diagnostics(
            config(),
            connect_readonly=True,
            operator_confirmed=True,
            interquery_delay_ms=1000,
            manager_factory=forbidden,
        )
