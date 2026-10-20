"""Deterministic, hardware-independent IPG EDFA simulator (read-only M1)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import override

from hardware.edfa_types import (
    EDFACommunicationError,
    EDFAOpticalPower,
    EDFAState,
    EDFAStateError,
    EDFAStatus,
    EDFATelemetry,
    PowerUnit,
)
from hardware.interfaces import AbstractEDFA

_DEFAULT_STATUS = (1 << 0) | (1 << 21) | (1 << 22)  # APC selected, monitors available; emission not inferred.


class DummyEDFA(AbstractEDFA):
    """Simulates *queries* only; deliberately has no emission/setpoint API.

    set_status_word() supports deterministic fault injection in tests. A
    successful close only releases simulated resources, never proves optical
    emission stopped on a physical device.
    """

    def __init__(
        self,
        *,
        status_word: int = _DEFAULT_STATUS,
        fail_operations: set[str] | None = None,
        identity: str = "IPG,EAD-2K-C,SIMULATED",
    ) -> None:
        self._status = EDFAStatus(status_word)
        self._fail_operations = set(fail_operations or ())
        self._identity = identity
        self._state = EDFAState.DISCONNECTED
        self._lock = threading.RLock()

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
        with self._lock:
            if self._state is not EDFAState.DISCONNECTED:
                raise EDFAStateError("EDFA simulator must be disconnected before connecting")
            self._fail("connect")
            self._state = EDFAState.IDLE

    @override
    def identify(self) -> str:
        with self._lock:
            self._require_idle()
            self._fail("identify")
            return self._identity

    @override
    def read_telemetry(self) -> EDFATelemetry:
        with self._lock:
            self._require_idle()
            self._fail("read_telemetry")
            status = self._status
            unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W
            input_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.MW
            return EDFATelemetry(
                status=status,
                output_power=EDFAOpticalPower(-60.0 if unit is PowerUnit.DBM else 0.0, unit),
                diode_current_a=0.0,
                case_temperature_c=26.0,
                acquired_at_utc=datetime.now(UTC),
                input_power=EDFAOpticalPower(0.0, input_unit) if status.input_monitor_available else None,
                minimum_input_power=(
                    EDFAOpticalPower(-3.0 if input_unit is PowerUnit.DBM else 0.5, input_unit)
                    if status.input_monitor_available
                    else None
                ),
                power_setpoint=EDFAOpticalPower(30.0 if unit is PowerUnit.DBM else 1.0, unit),
                back_reflection_percent_of_max=0.0 if status.back_reflection_monitor_available else None,
            )

    def set_status_word(self, word: int) -> None:
        """Simulator-only input injection; not a hardware operation."""
        status = EDFAStatus(word)
        with self._lock:
            self._status = status

    @override
    def close(self) -> None:
        with self._lock:
            self._state = EDFAState.DISCONNECTED

    def _require_idle(self) -> None:
        if self._state is not EDFAState.IDLE:
            raise EDFAStateError(f"EDFA simulator is not ready: {self._state.name}")

    def _fail(self, operation: str) -> None:
        if operation in self._fail_operations:
            if operation != "connect":
                self._state = EDFAState.ERROR
            raise EDFACommunicationError(f"Simulated EDFA communication failure during {operation}")
