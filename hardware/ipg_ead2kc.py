"""Explicitly opened GPIB/VISA backend for the IPG EAD-2K-C.

Never opens a bus on import/initialization or searches addresses. Physical
setpoint writes require separately enabled site policy and supervised attestation;
no physical emission, mode, reset or event-clearing commands are exposed.
A GPIB session may nonetheless enter remote mode: physical use is operator gated.
"""

from __future__ import annotations

import importlib
import logging
import re
import threading
from collections.abc import Callable
from typing import Protocol, Self, override

from hardware.edfa_controls import (
    EDFAWriteOutcome,
    EDFAWritePolicy,
    EDFAWriteRequest,
    EDFAWriteResult,
    execute_supervised_write,
)
from hardware.edfa_gpib import EXTENDED_READ_QUERIES, GPIBQuerySession, GPIBReadOnlyReader, parse_status
from hardware.edfa_types import (
    EDFACommunicationError,
    EDFAConfiguration,
    EDFAProtocolError,
    EDFAState,
    EDFAStateError,
    EDFAStatus,
    EDFATelemetry,
)
from hardware.interfaces import AbstractEDFA

logger = logging.getLogger("LabApp.IPGEAD2KC")

_GPIB_RESOURCE = re.compile(r"GPIB(?:0|[1-9][0-9]*)::(?:[1-9]|[12][0-9]|30)::INSTR", re.IGNORECASE)
_REQUIRED_IDENTITY_TOKEN = "EAD-2K-C"
_CONSISTENCY_BITS = (1 << 18) | (1 << 21) | (1 << 22)
_CONFIGURATION_CONSISTENCY_BITS = _CONSISTENCY_BITS | (1 << 0) | (1 << 9) | (1 << 16) | (1 << 20)
_TERMINATIONS = {"none": None, "lf": "\n", "cr": "\r", "crlf": "\r\n"}


class VisaSession(GPIBQuerySession, Protocol):
    timeout: int
    write_termination: str | None
    read_termination: str | None

    def write(self, message: str) -> object: ...

    def close(self) -> None: ...


class VisaResourceManager(Protocol):
    def open_resource(self, resource_name: str) -> VisaSession: ...

    def close(self) -> None: ...


def _default_manager_factory() -> VisaResourceManager:
    """Delay importing PyVISA and selecting the local VISA backend until Connect."""
    try:
        pyvisa = importlib.import_module("pyvisa")
    except ImportError as exc:
        raise EDFACommunicationError(
            "PyVISA is not installed. M2 does not modify the locked lab dependencies; "
            "qualify and install PyVISA and the compatible VISA runtime before physical use."
        ) from exc
    try:
        return pyvisa.ResourceManager()
    except Exception as exc:
        raise EDFACommunicationError("Unable to initialize the installed VISA resource manager") from exc


class IPGEAD2KCGPIB(AbstractEDFA):
    """An exclusive read-only session with conservative recovery semantics.

    The session owns the manager and instrument handles. All operations are
    serialized. A connection or query failure requires explicit close/reconnect.
    Its state represents communication, not confirmed optical emission state.
    """

    def __init__(
        self,
        resource_name: str,
        *,
        timeout_ms: int = 3000,
        write_termination: str = "none",
        read_termination: str = "none",
        manager_factory: Callable[[], VisaResourceManager] | None = None,
    ) -> None:
        if not isinstance(resource_name, str) or _GPIB_RESOURCE.fullmatch(resource_name) is None:
            raise ValueError("Use one explicit GPIB primary address, for example GPIB0::10::INSTR (not autodiscovery)")
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 60_000:
            raise ValueError("timeout_ms must be an integer between 100 and 60000")
        if write_termination not in _TERMINATIONS or read_termination not in _TERMINATIONS:
            raise ValueError("GPIB termination must be one of: none, lf, cr, crlf")
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self.write_termination = write_termination
        self.read_termination = read_termination
        self._manager_factory = manager_factory or _default_manager_factory
        self._manager: VisaResourceManager | None = None
        self._session: VisaSession | None = None
        self._identity: str | None = None
        self._state = EDFAState.DISCONNECTED
        self._lock = threading.RLock()
        self._write_policy = EDFAWritePolicy()  # Never inherited from a generic connected session.

    @property
    @override
    def state(self) -> EDFAState:
        with self._lock:
            return self._state

    @override
    def is_connected(self) -> bool:
        return self.state is not EDFAState.DISCONNECTED

    @override
    def connect(self) -> None:
        """Only caller-triggered connect; the sole initial device query is *IDN?."""
        with self._lock:
            if self._state is not EDFAState.DISCONNECTED:
                raise EDFAStateError("EDFA session is already open or needs explicit recovery")
            try:
                self._manager = self._manager_factory()
                self._session = self._manager.open_resource(self.resource_name)
                self._session.timeout = self.timeout_ms
                self._session.write_termination = _TERMINATIONS[self.write_termination]
                self._session.read_termination = _TERMINATIONS[self.read_termination]
                identity = GPIBReadOnlyReader(self._session).identify()
                if _REQUIRED_IDENTITY_TOKEN.casefold() not in identity.casefold():
                    raise EDFAProtocolError(
                        "GPIB identification does not establish EAD-2K-C identity; "
                        f"received {identity!r} at {self.resource_name!r}. Refusing telemetry."
                    )
                self._identity = identity
                self._state = EDFAState.IDLE
            except Exception as exc:
                problems = self._close_owned_resources()
                self._state = EDFAState.ERROR if problems else EDFAState.DISCONNECTED
                if problems:
                    exc.add_note("Failed to release one or more VISA resources after unsuccessful connection")
                if isinstance(exc, (EDFAProtocolError, EDFACommunicationError)):
                    raise
                raise EDFACommunicationError(
                    f"Could not establish read-only EDFA GPIB session at {self.resource_name}"
                ) from exc

    @override
    def identify(self) -> str:
        with self._lock:
            self._require_idle()
            assert self._identity is not None
            return self._identity

    @override
    def read_telemetry(self) -> EDFATelemetry:
        with self._lock:
            session = self._require_idle()
            reader = GPIBReadOnlyReader(session)
            try:
                result = reader.read_telemetry()
                # Reject a snapshot if its documented unit/capability flags
                # changed while the sequential responses were being collected.
                # Matching endpoints still cannot establish true atomicity.
                end_status = parse_status(session.query("SOUR:POW:STAT?"))
                if (result.status.raw_word ^ end_status.raw_word) & _CONSISTENCY_BITS:
                    raise EDFAProtocolError(
                        "EDFA power-unit or monitor-capability status changed during a multi-query snapshot"
                    )
                return result
            except Exception as exc:
                self._state = EDFAState.ERROR
                if isinstance(exc, EDFAProtocolError):
                    raise
                raise EDFACommunicationError("EDFA GPIB telemetry read failed; explicit recovery required") from exc

    @override
    def read_configuration(self) -> EDFAConfiguration:
        """Explicit on-demand readback; never part of the telemetry timer."""
        with self._lock:
            session = self._require_idle()
            try:
                report = GPIBReadOnlyReader(session).read_configuration()
                end_status = parse_status(session.query("SOUR:POW:STAT?"))
                if (report.status.raw_word ^ end_status.raw_word) & _CONFIGURATION_CONSISTENCY_BITS:
                    raise EDFAProtocolError("EDFA mode, units or capabilities changed during configuration readback")
                return report
            except Exception as exc:
                self._state = EDFAState.ERROR
                if isinstance(exc, EDFAProtocolError):
                    raise
                raise EDFACommunicationError("EDFA configuration readback failed; explicit recovery required") from exc

    def read_qualification_query(self, command: str) -> tuple[str, float, EDFAStatus]:
        """Single non-mutating manual candidate, for supervised CLI qualification.

        This command cannot be selected by the GUI and cannot be arbitrary SCPI.
        Any unexpected response or communication failure requires close/reconnect.
        """
        if command not in EXTENDED_READ_QUERIES:
            raise EDFAProtocolError(f"Not an approved EDFA qualification query: {command!r}")
        with self._lock:
            session = self._require_idle()
            try:
                start = parse_status(session.query("SOUR:POW:STAT?"))
                if command.startswith("SOUR:GAIN:") and not start.agc_available:
                    raise EDFAProtocolError("AGC capability is not advertised; refusing gain readback")
                result = GPIBReadOnlyReader(session).read_qualification_value(command)
                end = parse_status(session.query("SOUR:POW:STAT?"))
                if (start.raw_word ^ end.raw_word) & _CONFIGURATION_CONSISTENCY_BITS:
                    raise EDFAProtocolError("EDFA mode, units or capabilities changed during qualification readback")
                return (*result, start)
            except Exception as exc:
                self._state = EDFAState.ERROR
                if isinstance(exc, EDFAProtocolError):
                    raise
                raise EDFACommunicationError("EDFA qualification readback failed; explicit recovery required") from exc

    def configure_write_policy(self, policy: EDFAWritePolicy) -> None:
        """Site-local policy is injected by the UI worker, never read from shared defaults."""
        if not isinstance(policy, EDFAWritePolicy):
            raise TypeError("Expected EDFAWritePolicy")
        with self._lock:
            if self._state is not EDFAState.DISCONNECTED:
                raise EDFAStateError("Set write policy before connecting; no mid-session elevation")
            self._write_policy = policy

    def apply_control(self, request: EDFAWriteRequest) -> EDFAWriteResult:
        """Single supervised transaction on the unique owner-thread VISA session.

        Mismatch/ambiguous write => ERROR and manual verification, never
        automatic retry or rollback. Release of VISA does not stop emission.
        """
        with self._lock:
            session = self._require_idle()
            if not isinstance(request, EDFAWriteRequest):
                raise EDFAProtocolError("Typed EDFAWriteRequest required")
            try:

                def guard_idle() -> None:
                    self._require_idle()

                result = execute_supervised_write(
                    session,
                    request,
                    self._write_policy,
                    physical=True,
                    pre_write_guard=guard_idle,
                )
            except (EDFAProtocolError, EDFACommunicationError, OSError) as exc:
                self._state = EDFAState.ERROR
                raise EDFACommunicationError(
                    "EDFA write preflight failed without a command; explicit recovery required"
                ) from exc
            if result.outcome in (EDFAWriteOutcome.UNKNOWN, EDFAWriteOutcome.MISMATCH):
                self._state = EDFAState.ERROR
            return result

    @override
    def close(self) -> None:
        """Release both VISA objects. A failed close leaves an ERROR state."""
        with self._lock:
            if self._state is EDFAState.DISCONNECTED and self._manager is None and self._session is None:
                return
            problems = self._close_owned_resources()
            if problems:
                self._state = EDFAState.ERROR
                error = EDFACommunicationError("Could not confirm complete VISA resource release")
                for label, problem in problems:
                    error.add_note(f"{label} close: {type(problem).__name__}: {problem}")
                raise error
            self._state = EDFAState.DISCONNECTED
            self._identity = None

    def _require_idle(self) -> VisaSession:
        if self._state is not EDFAState.IDLE or self._session is None:
            raise EDFAStateError(f"EDFA is not ready for queries ({self._state.name.lower()})")
        return self._session

    def _close_owned_resources(self) -> list[tuple[str, Exception]]:
        problems: list[tuple[str, Exception]] = []
        if self._session is not None:
            try:
                self._session.close()
            except Exception as exc:
                logger.exception("EDFA GPIB resource close failed")
                problems.append(("session", exc))
            else:
                self._session = None
        # Preserve the manager while a session remains unclosed. Retrying a
        # session close through an already-disposed manager is not reliable.
        if self._manager is not None and self._session is None:
            try:
                self._manager.close()
            except Exception as exc:
                logger.exception("EDFA VISA resource manager close failed")
                problems.append(("manager", exc))
            else:
                self._manager = None
        return problems

    def __enter__(self) -> Self:
        with self._lock:
            self._require_idle()
            return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
