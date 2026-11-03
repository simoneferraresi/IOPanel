"""M3.1 readback regressions. Fake GPIB and Qt simulation only: never real hardware."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config_model import AppConfig
from hardware.dummy_edfa import DummyEDFA
from hardware.edfa_gpib import EXTENDED_READ_QUERIES, GPIBReadOnlyReader
from hardware.edfa_types import (
    EDFACommunicationError,
    EDFAConfiguration,
    EDFAOpticalPower,
    EDFAProtocolError,
    EDFAState,
    EDFAStateError,
    EDFAStatus,
    PowerUnit,
)
from hardware.ipg_ead2kc import IPGEAD2KCGPIB
from tools.edfa_readonly_probe import run, summarize_configuration

RESOURCE = "GPIB0::12::INSTR"
AGC_AVAILABLE = (1 << 0) | (1 << 20) | (1 << 21)
NEW_REPLIES = {
    "SOUR:POW:LIM:HIGH?": "2.0",
    "SOUR:CURR:SPO?": "0.25",
    "SOUR:CURR:LIM:HIGH?": "1.20",
    "SOUR:GAIN:SPO?": "15.5",
    "SOUR:GAIN:LIM:HIGH?": "30.0",
}


class FakeSession:
    def __init__(self, status: int = AGC_AVAILABLE) -> None:
        self.status = status
        self.calls: list[str] = []
        self.responses: dict[str, str] = {
            "*IDN?": "IPG EAD-2K-C, Rev 1.1.175",
            "SOUR:POW?": "Off",
            "SOUR:POW:SPO?": "0.2",
            "SOUR:CURR?": "0.021",
            "SOUR:TEMP:LEV?": "22.7",
            "INP?": "0.08",
            "INP:LIM:LOW?": "0.23",
            **NEW_REPLIES,
        }
        self.timeout = 0
        self.write_termination = None
        self.read_termination = None
        self.closed = False
        self.fail_command: str | None = None

    def query(self, message: str) -> str:
        self.calls.append(message)
        if message == self.fail_command:
            raise OSError("injected VISA timeout")
        if message == "SOUR:POW:STAT?":
            return str(self.status)
        return self.responses[message]

    def close(self) -> None:
        self.closed = True


class FakeManager:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.opened: list[str] = []
        self.closed = False

    def open_resource(self, name: str) -> FakeSession:
        self.opened.append(name)
        return self.session

    def close(self) -> None:
        self.closed = True


def connect(session: FakeSession) -> tuple[IPGEAD2KCGPIB, FakeManager]:
    manager = FakeManager(session)
    device = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: manager)
    assert session.calls == []
    device.connect()
    assert manager.opened == [RESOURCE]
    assert session.calls == ["*IDN?"]
    return device, manager


def test_extended_reader_all_five_commands_and_raw_replies() -> None:
    session = FakeSession()
    configuration = GPIBReadOnlyReader(session).read_configuration()
    assert configuration.power_limit == EDFAOpticalPower(2.0, PowerUnit.W)
    assert configuration.diode_current_setpoint_a == 0.25
    assert configuration.diode_current_limit_a == 1.2
    assert configuration.gain_setpoint_db == 15.5
    assert configuration.gain_limit_db == 30.0
    assert dict(configuration.raw_replies) == NEW_REPLIES
    assert {command for command, _ in configuration.raw_replies} == EXTENDED_READ_QUERIES
    assert session.calls == [
        "SOUR:POW:STAT?",
        "SOUR:POW:LIM:HIGH?",
        "SOUR:CURR:SPO?",
        "SOUR:CURR:LIM:HIGH?",
        "SOUR:GAIN:SPO?",
        "SOUR:GAIN:LIM:HIGH?",
    ]
    assert configuration.acquired_at_utc.utcoffset() is not None


def test_missing_agc_capability_skips_gain_queries_without_fabricating_zero() -> None:
    session = FakeSession(status=1 << 0)
    configuration = GPIBReadOnlyReader(session).read_configuration()
    assert configuration.gain_setpoint_db is None
    assert configuration.gain_limit_db is None
    assert all("GAIN" not in command for command in session.calls)
    assert configuration.power_limit.value == 2.0


def test_power_unit_status_controls_limit_without_confusing_dbm_with_watts() -> None:
    session = FakeSession(status=AGC_AVAILABLE | (1 << 18))
    session.responses["SOUR:POW:LIM:HIGH?"] = "33.01"
    configuration = GPIBReadOnlyReader(session).read_configuration()
    assert configuration.power_limit == EDFAOpticalPower(33.01, PowerUnit.DBM)


@pytest.mark.parametrize("bad", ["Off", "nan", "inf", "-inf", "error", "1.0 A", "", "1,2"])
def test_invalid_extended_response_is_an_error_not_a_missing_capability(bad: str) -> None:
    session = FakeSession()
    session.responses["SOUR:CURR:SPO?"] = bad
    with pytest.raises(EDFAProtocolError):
        GPIBReadOnlyReader(session).read_configuration()
    assert session.calls[-1] == "SOUR:CURR:SPO?"
    assert "SOUR:GAIN:SPO?" not in session.calls


@pytest.mark.parametrize("command", ["*ESR?", "*OPC?", "*STB?", "*CLS", "SOUR:CURR 1", "SOUR:POW:STAT 1"])
def test_qualification_rejects_all_nonapproved_queries(command: str) -> None:
    session = FakeSession()
    with pytest.raises(EDFAProtocolError):
        GPIBReadOnlyReader(session).read_qualification_value(command)
    assert not session.calls


def test_physical_configuration_requires_only_one_visa_session_and_status_recheck() -> None:
    session = FakeSession()
    device, manager = connect(session)
    configuration = device.read_configuration()
    assert configuration.gain_limit_db == 30.0
    assert session.calls.count("SOUR:POW:STAT?") == 2
    assert device.state is EDFAState.IDLE
    device.close()
    assert session.closed and manager.closed


def test_ordinary_physical_telemetry_never_adds_new_queries() -> None:
    session = FakeSession()
    device, _ = connect(session)
    telemetry = device.read_telemetry()
    assert telemetry.output_power is None
    assert telemetry.output_power_non_numeric_reply == "Off"
    assert not EXTENDED_READ_QUERIES.intersection(session.calls)
    device.close()


def test_physical_configuration_failure_requires_explicit_close() -> None:
    session = FakeSession()
    session.fail_command = "SOUR:CURR:SPO?"
    device, manager = connect(session)
    with pytest.raises(EDFACommunicationError, match="explicit recovery"):
        device.read_configuration()
    assert device.state is EDFAState.ERROR
    with pytest.raises(EDFAStateError):
        device.read_configuration()
    assert "SOUR:GAIN:SPO?" not in session.calls
    device.close()
    assert manager.closed and session.closed


def test_individual_qualification_returns_raw_numeric_and_keeps_allowlist() -> None:
    session = FakeSession()
    device, _ = connect(session)
    raw, value, status = device.read_qualification_query("SOUR:CURR:SPO?")
    assert (raw, value) == ("0.25", 0.25)
    assert status.raw_word == AGC_AVAILABLE
    assert session.calls == ["*IDN?", "SOUR:POW:STAT?", "SOUR:CURR:SPO?", "SOUR:POW:STAT?"]
    with pytest.raises(EDFAProtocolError):
        device.read_qualification_query("*ESR?")
    assert device.state is EDFAState.IDLE
    device.close()


def test_inconsistent_status_on_configuration_is_rejected_not_cached() -> None:
    class Changing(FakeSession):
        def query(self, message: str) -> str:
            if message == "SOUR:POW:STAT?" and "SOUR:POW:STAT?" in self.calls:
                self.status |= 1 << 18
            return super().query(message)

    session = Changing()
    device, _ = connect(session)
    with pytest.raises(EDFAProtocolError, match="changed"):
        device.read_configuration()
    assert device.state is EDFAState.ERROR
    device.close()


def test_simulator_gain_capability_and_fault_recovery() -> None:
    simulator = DummyEDFA()
    simulator.connect()
    conf = simulator.read_configuration()
    assert conf.gain_setpoint_db is None
    assert conf.gain_limit_db is None
    simulator.set_status_word(AGC_AVAILABLE)
    conf = simulator.read_configuration()
    assert conf.gain_setpoint_db == 15.0
    assert conf.gain_limit_db == 30.0
    assert conf.power_limit.unit is PowerUnit.W
    simulator.close()
    failing = DummyEDFA(fail_operations={"read_configuration"})
    failing.connect()
    with pytest.raises(EDFACommunicationError):
        failing.read_configuration()
    assert failing.state is EDFAState.ERROR
    failing.close()
    assert failing.state is EDFAState.DISCONNECTED


def test_configuration_model_rejects_inconsistent_units_and_values() -> None:
    fields = {
        "status": EDFAStatus(AGC_AVAILABLE),
        "power_limit": EDFAOpticalPower(2.0, PowerUnit.W),
        "diode_current_setpoint_a": 0.5,
        "diode_current_limit_a": 1.0,
        "gain_setpoint_db": 15.0,
        "gain_limit_db": 30.0,
        "acquired_at_utc": datetime.now(UTC),
    }
    with pytest.raises(ValueError, match="unit"):
        EDFAConfiguration(**{**fields, "power_limit": EDFAOpticalPower(33, PowerUnit.DBM)})
    with pytest.raises(ValueError, match="finite"):
        EDFAConfiguration(**{**fields, "diode_current_limit_a": float("nan")})
    with pytest.raises(ValueError, match="both"):
        EDFAConfiguration(**{**fields, "gain_limit_db": None})
    with pytest.raises(ValueError, match="capability"):
        EDFAConfiguration(**{**fields, "status": EDFAStatus(1 << 0)})


def test_probe_options_stay_operator_gated_and_capture_raw_reply() -> None:
    config = AppConfig.from_ini_dict(
        {"Instruments": {"edfa_backend": "physical", "edfa_gpib_resource_name": RESOURCE}}
    )
    called: list[str] = []

    class FakeBackend:
        def __init__(self, *args, **kwargs):
            called.append("construct")

        def connect(self):
            called.append("connect")

        def identify(self):
            return "IPG EAD-2K-C"

        def read_telemetry(self):
            return DummyEDFAForProbe.report()

        def read_qualification_query(self, command):
            called.append(command)
            return "0.25", 0.25, EDFAStatus(AGC_AVAILABLE)

        def read_configuration(self):
            called.append("configuration")
            sim = DummyEDFA(status_word=AGC_AVAILABLE)
            sim.connect()
            return sim.read_configuration()

        def close(self):
            called.append("close")

    class DummyEDFAForProbe:
        @staticmethod
        def report():
            sim = DummyEDFA()
            sim.connect()
            return sim.read_telemetry()

    with pytest.raises(ValueError, match="operator-confirmed"):
        run(
            config,
            connect_readonly=True,
            operator_confirmed=False,
            backend_factory=FakeBackend,
            qualification_query="SOUR:CURR:SPO?",
        )
    assert not called
    with pytest.raises(ValueError, match="five audited"):
        run(
            config,
            connect_readonly=True,
            operator_confirmed=True,
            backend_factory=FakeBackend,
            qualification_query="*ESR?",
        )
    assert not called
    result = run(
        config,
        connect_readonly=True,
        operator_confirmed=True,
        backend_factory=FakeBackend,
        qualification_query="SOUR:CURR:SPO?",
    )
    assert result["qualification"]["raw_reply"] == "0.25"
    assert result["qualification"]["reported_unit"] == "A"
    assert called == ["construct", "connect", "SOUR:CURR:SPO?", "close"]
    called.clear()
    result = run(
        config,
        connect_readonly=True,
        operator_confirmed=True,
        backend_factory=FakeBackend,
        extended_configuration=True,
    )
    assert result["configuration"]["power_limit"]["unit"] == "W"
    assert result["configuration"]["gain_limit_db"] == 30.0
    assert called == ["construct", "connect", "configuration", "close"]


def test_configuration_summary_explicitly_reports_missing_gain() -> None:
    simulator = DummyEDFA()
    simulator.connect()
    data = summarize_configuration(simulator.read_configuration())
    assert data["gain_setpoint_db"] is None
    assert "AGC" in data["gain_unavailable_reason"]
    assert "SAFETY" in data["note"]
    simulator.close()


def test_gui_configuration_is_on_demand_and_does_not_start_emission(qtbot) -> None:
    from ui.edfa_panel import EDFAReadOnlyPanel

    panel = EDFAReadOnlyPanel(
        worker=__import__("logic.edfa_monitor", fromlist=["EDFAMonitorWorker"]).EDFAMonitorWorker(
            simulator_factory=lambda: DummyEDFA(status_word=AGC_AVAILABLE)
        )
    )
    qtbot.addWidget(panel)
    assert not panel.configuration_button.isEnabled()
    assert panel.reading_labels["edfaPowerLimitLabel"].text() == "—"
    panel.request_connect()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.IDLE, timeout=3000)
    assert panel.reading_labels["edfaPowerLimitLabel"].text() == "—"
    panel.configuration_button.click()
    qtbot.waitUntil(lambda: panel.reading_labels["edfaGainLimitLabel"].text() == "30 dB", timeout=3000)
    assert panel.reading_labels["edfaCurrentLimitLabel"].text() == "1 A"
    assert panel.reading_labels["edfaConfigUpdatedLabel"].text() != "—"
    panel.start_button.click()
    qtbot.waitUntil(lambda: panel._monitoring, timeout=3000)
    assert not panel.configuration_button.isEnabled()
    panel.stop_button.click()
    qtbot.waitUntil(lambda: not panel._monitoring, timeout=3000)
    panel.request_disconnect()
    qtbot.waitUntil(lambda: panel._backend_state is EDFAState.DISCONNECTED, timeout=3000)
    assert panel.reading_labels["edfaGainLimitLabel"].text() == "—"
    panel.request_shutdown()
    qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=3000)


def test_physical_gui_requires_separate_confirmation_for_extended_readback(qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from ui.edfa_panel import EDFAReadOnlyPanel

    panel = EDFAReadOnlyPanel(backend_mode="physical", resource_name=RESOURCE)
    qtbot.addWidget(panel)
    panel._backend_state = EDFAState.IDLE
    panel._update_controls()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    requested: list[bool] = []
    panel.configuration_requested.connect(lambda: requested.append(True))
    panel.request_configuration()
    assert requested == []
    assert not panel.task.worker_thread.isRunning()
    panel.request_shutdown()
