"""Deterministic, hardware-independent IPG EDFA simulator with supervised M4 writes."""

from __future__ import annotations

import math
import threading
from datetime import UTC, datetime
from typing import override

from hardware.edfa_controls import (
    EDFAEmissionAction,
    EDFAWriteOutcome,
    EDFAWritePolicy,
    EDFAWriteRequest,
    EDFAWriteResult,
    execute_supervised_write,
    format_emission_command,
)
from hardware.edfa_types import (
    EDFACommunicationError,
    EDFAConfiguration,
    EDFAOpticalPower,
    EDFAProtocolError,
    EDFAState,
    EDFAStateError,
    EDFAStatus,
    EDFATelemetry,
    PowerUnit,
)
from hardware.interfaces import AbstractEDFA

_DEFAULT_STATUS = (1 << 0) | (1 << 21) | (1 << 22)  # APC selected, monitors available; emission not inferred.


class DummyEDFA(AbstractEDFA):
    """Offline-only setpoint/emission model; never opens VISA.

    set_status_word() and failure injection support deterministic safety
    regression tests. Simulator emission actions have NO physical counterpart.
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
        self._power_sp_w = 1.0
        self._power_lim_w = 2.0
        self._current_sp_a = 0.25
        self._current_lim_a = 1.0
        self._gain_sp_db = 15.0
        self._gain_lim_db = 30.0
        self._emission_on = False
        self.write_commands: list[str] = []
        self._simulation_policy = EDFAWritePolicy(
            enabled=True, site_reference="SIMULATION", maximum_power_w=2.0,
            maximum_gain_db=30.0, maximum_current_a=1.0,
        )

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
                power_setpoint=EDFAOpticalPower(
                    10 * math.log10(self._power_sp_w * 1000) if unit is PowerUnit.DBM else self._power_sp_w,
                    unit,
                ),
                back_reflection_percent_of_max=0.0 if status.back_reflection_monitor_available else None,
            )

    @override
    def read_configuration(self) -> EDFAConfiguration:
        with self._lock:
            self._require_idle()
            self._fail("read_configuration")
            status = self._status
            unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W
            return EDFAConfiguration(
                status=status,
                power_limit=EDFAOpticalPower(
                    10 * math.log10(self._power_lim_w * 1000) if unit is PowerUnit.DBM else self._power_lim_w,
                    unit,
                ),
                diode_current_setpoint_a=self._current_sp_a,
                diode_current_limit_a=self._current_lim_a,
                gain_setpoint_db=self._gain_sp_db if status.agc_available else None,
                gain_limit_db=self._gain_lim_db if status.agc_available else None,
                acquired_at_utc=datetime.now(UTC),
            )

    def apply_control(self, request: EDFAWriteRequest) -> EDFAWriteResult:
        """Complete offline transaction, including injected ambiguous failures."""
        with self._lock:
            self._require_idle()
            if not isinstance(request, EDFAWriteRequest):
                raise EDFAProtocolError("Typed EDFAWriteRequest required")
            if self._emission_on:
                return EDFAWriteResult(request, EDFAWriteOutcome.REJECTED, None, None,
                                       "Simulator emission active: setpoint write refused")
            session = _DummyWriteSession(self)
            try:
                result = execute_supervised_write(session, request, self._simulation_policy, physical=False)
            except Exception as exc:
                self._state = EDFAState.ERROR
                raise EDFACommunicationError("Simulated EDFA write preflight failed") from exc
            if result.outcome in (EDFAWriteOutcome.MISMATCH, EDFAWriteOutcome.UNKNOWN):
                self._state = EDFAState.ERROR
            return result

    def simulate_emission_action(self, action: EDFAEmissionAction) -> str:
        """SIMULATOR ONLY: physical emission controls remain unimplemented."""
        with self._lock:
            self._require_idle()
            command = format_emission_command(action)
            self._emission_on = action is EDFAEmissionAction.ON
            self.write_commands.append(command)
            return command

    def set_failure_injection(self, failures: set[str]) -> None:
        with self._lock:
            self._fail_operations = set(failures)

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


class _DummyWriteSession:
    """GPIB-like query/write facade used only inside the simulator lock."""

    def __init__(self, device: DummyEDFA) -> None:
        self.device = device
        self.did_write = False

    def query(self, message: str) -> str:
        dev = self.device
        if "preflight_read" in dev._fail_operations and not self.did_write:
            raise OSError("Simulated preflight read timeout")
        if "post_write_read" in dev._fail_operations and self.did_write:
            raise OSError("Simulated post-write read timeout")
        status = dev._status
        unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W

        def power(value_w: float) -> str:
            return str(10 * math.log10(value_w * 1000) if unit is PowerUnit.DBM else value_w)

        responses = {
            "SOUR:POW:STAT?": str(status.raw_word),
            "SOUR:POW?": power(0.0) if unit is PowerUnit.W else "-60",
            "SOUR:POW:SPO?": power(dev._power_sp_w),
            "SOUR:POW:LIM:HIGH?": power(dev._power_lim_w),
            "SOUR:CURR?": "0",
            "SOUR:CURR:SPO?": str(dev._current_sp_a),
            "SOUR:CURR:LIM:HIGH?": str(dev._current_lim_a),
            "SOUR:GAIN:SPO?": str(dev._gain_sp_db),
            "SOUR:GAIN:LIM:HIGH?": str(dev._gain_lim_db),
            "SOUR:TEMP:LEV?": "26",
            "INP?": "-60" if status.logarithmic_power_units else "0",
            "INP:LIM:LOW?": "-3" if status.logarithmic_power_units else "0.5",
            "SOUR:POW:BACK?": "0",
        }
        return responses[message]

    def write(self, message: str) -> None:
        dev = self.device
        dev.write_commands.append(message)
        if "write_before" in dev._fail_operations:
            raise OSError("Simulated uncertain VISA write before device update")
        prefix, _, argument = message.partition(" ")
        if prefix == "SOUR:POW":
            dev._power_sp_w = (
                10 ** ((float(argument) - 30) / 10) if dev._status.logarithmic_power_units
                else float(argument)
            )
        elif prefix == "SOUR:GAIN":
            dev._gain_sp_db = float(argument)
        elif prefix == "SOUR:CURR":
            dev._current_sp_a = float(argument)
        elif prefix == "UNIT:POW":
            bit = 1 << 18
            value = dev._status.raw_word
            dev._status = EDFAStatus(value | bit if argument == "DBm" else value & ~bit)
        else:
            raise EDFAProtocolError("Simulator refuses command not in M4 setpoint allowlist")
        self.did_write = True
        if "write_after" in dev._fail_operations:
            raise OSError("Simulated uncertain VISA write after device update")
