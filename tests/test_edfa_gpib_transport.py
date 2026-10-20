"""Injected fake VISA sessions; this test file never imports or opens PyVISA."""

from __future__ import annotations

import pytest

from hardware.edfa_types import EDFACommunicationError, EDFAProtocolError, EDFAState, EDFAStateError
from hardware.ipg_ead2kc import IPGEAD2KCGPIB

RESOURCE = "GPIB0::10::INSTR"
GOOD_STATUS = (1 << 0) | (1 << 21) | (1 << 22)


class FakeSession:
    def __init__(self, *, identity: str = "IPG PHOTONICS,EAD-2K-C,FA160101", status: int = GOOD_STATUS) -> None:
        self.timeout = 0
        self.write_termination = "sentinel"
        self.read_termination = "sentinel"
        self.calls: list[str] = []
        self.identity = identity
        self.status = status
        self.fail_query = False
        self.fail_close = False
        self.closed = False
        self.response_overrides: dict[str, str] = {}

    def query(self, message: str) -> str:
        self.calls.append(message)
        if self.fail_query:
            raise OSError("simulated GPIB timeout")
        replies = {
            "*IDN?": self.identity,
            "SOUR:POW:STAT?": str(self.status),
            "SOUR:POW?": "0.0",
            "SOUR:POW:SPO?": "1.0",
            "SOUR:CURR?": "0.0",
            "SOUR:TEMP:LEV?": "28.9",
            "INP?": "1.0",
            "INP:LIM:LOW?": "0.5",
            "SOUR:POW:BACK?": "0",
        }
        return self.response_overrides.get(message, replies[message])

    def close(self) -> None:
        if self.fail_close:
            raise OSError("simulated VISA close failure")
        self.closed = True


class FakeManager:
    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()
        self.opened: list[str] = []
        self.closed = False
        self.fail_open = False
        self.fail_close = False

    def open_resource(self, resource_name: str) -> FakeSession:
        self.opened.append(resource_name)
        if self.fail_open:
            raise OSError("simulated VISA open failure")
        return self.session

    def close(self) -> None:
        if self.fail_close:
            raise OSError("simulated VISA manager close failure")
        self.closed = True


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "ASRL1::INSTR",
        "USB0::0x1313::0x80BB::INSTR",
        "GPIB0::INTFC",
        "GPIB0::0::INSTR",
        "GPIB0::31::INSTR",
        "GPIB0::10::INSTR;SOUR:POW:STAT 1",
        "GPIB0::10::INSTR\\n",
    ],
)
def test_only_explicit_gpib_primary_address_allowed(invalid: str) -> None:
    with pytest.raises(ValueError):
        IPGEAD2KCGPIB(invalid)


@pytest.mark.parametrize("timeout", [0, -1, True, 100.5, 60001])
def test_invalid_timeout_rejected(timeout: int) -> None:
    with pytest.raises(ValueError):
        IPGEAD2KCGPIB(RESOURCE, timeout_ms=timeout)


def test_construction_has_no_side_effects_and_connect_queries_only_identity() -> None:
    manager = FakeManager()
    factories: list[str] = []

    def factory() -> FakeManager:
        factories.append("called")
        return manager

    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=factory, timeout_ms=2500)
    assert not factories
    assert not manager.opened
    assert edfa.state is EDFAState.DISCONNECTED
    with pytest.raises(EDFAStateError):
        edfa.identify()
    edfa.connect()
    assert factories == ["called"]
    assert manager.opened == [RESOURCE]
    assert manager.session.calls == ["*IDN?"]
    assert manager.session.timeout == 2500
    assert manager.session.write_termination is None
    assert manager.session.read_termination is None
    assert edfa.identify() == "IPG PHOTONICS,EAD-2K-C,FA160101"
    assert edfa.is_connected()
    with pytest.raises(EDFAStateError):
        edfa.connect()
    edfa.close()
    assert manager.session.closed and manager.closed
    assert edfa.state is EDFAState.DISCONNECTED
    edfa.close()


def test_telemetry_query_sequence_and_status_recheck() -> None:
    session = FakeSession()
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: FakeManager(session))
    edfa.connect()
    report = edfa.read_telemetry()
    assert report.output_power.value == 0.0
    assert report.input_power is not None and report.input_power.value == 1.0
    assert report.status.input_monitor_available
    assert session.calls.count("SOUR:POW:STAT?") == 2
    assert session.calls[0] == "*IDN?"
    assert all(command.endswith("?") for command in session.calls)
    edfa.close()


def test_refuse_wrong_instrument_before_other_queries() -> None:
    session = FakeSession(identity="SOME OTHER INSTRUMENT")
    manager = FakeManager(session)
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: manager)
    with pytest.raises(EDFAProtocolError, match="identity"):
        edfa.connect()
    assert session.calls == ["*IDN?"]
    assert session.closed and manager.closed
    assert edfa.state is EDFAState.DISCONNECTED


def test_open_failure_attempts_manager_close_and_prevents_reuse() -> None:
    manager = FakeManager()
    manager.fail_open = True
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: manager)
    with pytest.raises(EDFACommunicationError):
        edfa.connect()
    assert manager.closed
    assert edfa.state is EDFAState.DISCONNECTED


def test_failed_query_requires_explicit_close_and_reconnect() -> None:
    session = FakeSession()
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: FakeManager(session))
    edfa.connect()
    session.fail_query = True
    with pytest.raises(EDFACommunicationError):
        edfa.read_telemetry()
    assert edfa.state is EDFAState.ERROR
    with pytest.raises(EDFAStateError):
        edfa.identify()
    with pytest.raises(EDFAStateError):
        edfa.connect()
    edfa.close()
    assert edfa.state is EDFAState.DISCONNECTED


def test_bad_telemetry_marks_error_instead_of_partial_result() -> None:
    session = FakeSession()
    session.response_overrides["SOUR:POW?"] = "oops"
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: FakeManager(session))
    edfa.connect()
    with pytest.raises(EDFAProtocolError):
        edfa.read_telemetry()
    assert edfa.state is EDFAState.ERROR
    edfa.close()


def test_failed_resource_close_is_reported_and_can_be_retried() -> None:
    session = FakeSession()
    session.fail_close = True
    manager = FakeManager(session)
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: manager)
    edfa.connect()
    with pytest.raises(EDFACommunicationError, match="release"):
        edfa.close()
    assert edfa.state is EDFAState.ERROR
    assert not session.closed
    assert not manager.closed
    session.fail_close = False
    edfa.close()
    assert edfa.state is EDFAState.DISCONNECTED


def test_factory_error_never_queries_any_device() -> None:
    def broken_factory() -> FakeManager:
        raise OSError("VISA runtime unavailable")

    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=broken_factory)
    with pytest.raises(EDFACommunicationError):
        edfa.connect()
    assert edfa.state is EDFAState.DISCONNECTED


def test_import_and_default_construction_do_not_load_visa(monkeypatch: pytest.MonkeyPatch) -> None:
    import hardware.ipg_ead2kc as backend

    loaded: list[str] = []

    def forbidden_import(name: str):
        loaded.append(name)
        raise AssertionError("PyVISA import must be lazy")

    monkeypatch.setattr(backend.importlib, "import_module", forbidden_import)
    instance = backend.IPGEAD2KCGPIB(RESOURCE)
    assert instance.state is EDFAState.DISCONNECTED
    assert not loaded


def test_default_factory_reports_missing_pyvisa(monkeypatch: pytest.MonkeyPatch) -> None:
    import hardware.ipg_ead2kc as backend

    def missing_import(name: str):
        raise ModuleNotFoundError("No module named pyvisa")

    monkeypatch.setattr(backend.importlib, "import_module", missing_import)
    edfa = backend.IPGEAD2KCGPIB(RESOURCE)
    with pytest.raises(EDFACommunicationError, match="PyVISA"):
        edfa.connect()
    assert edfa.state is EDFAState.DISCONNECTED


def test_telemetry_status_unit_change_is_rejected() -> None:
    class ChangingStatusSession(FakeSession):
        def query(self, message: str) -> str:
            if message == "SOUR:POW:STAT?" and message in self.calls:
                self.status |= 1 << 18
            return super().query(message)

    session = ChangingStatusSession()
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: FakeManager(session))
    edfa.connect()
    with pytest.raises(EDFAProtocolError, match="status changed"):
        edfa.read_telemetry()
    assert edfa.state is EDFAState.ERROR
    assert session.calls.count("SOUR:POW:STAT?") == 2
    edfa.close()
    assert edfa.state is EDFAState.DISCONNECTED


def test_manager_close_failure_is_not_mistaken_for_disconnection() -> None:
    manager = FakeManager()
    manager.fail_close = True
    edfa = IPGEAD2KCGPIB(RESOURCE, manager_factory=lambda: manager)
    edfa.connect()
    with pytest.raises(EDFACommunicationError, match="release"):
        edfa.close()
    assert manager.session.closed
    assert edfa.state is EDFAState.ERROR
    manager.fail_close = False
    edfa.close()
    assert edfa.state is EDFAState.DISCONNECTED


@pytest.mark.parametrize(
    ("write_mode", "read_mode", "expected_write", "expected_read"),
    [("none", "none", None, None), ("lf", "cr", "\\n", "\\r"), ("crlf", "lf", "\\r\\n", "\\n")],
)
def test_explicit_gpib_terminations_applied_before_identity_query(
    write_mode: str, read_mode: str, expected_write: str | None, expected_read: str | None
) -> None:
    session = FakeSession()
    edfa = IPGEAD2KCGPIB(
        RESOURCE,
        write_termination=write_mode,
        read_termination=read_mode,
        manager_factory=lambda: FakeManager(session),
    )
    edfa.connect()
    assert session.write_termination == expected_write
    assert session.read_termination == expected_read
    edfa.close()


@pytest.mark.parametrize("bad", ["LF", "\\n", "eoi", " ", "", "none\\n"])
def test_termination_configuration_is_allowlisted(bad: str) -> None:
    with pytest.raises(ValueError, match="termination"):
        IPGEAD2KCGPIB(RESOURCE, write_termination=bad)
