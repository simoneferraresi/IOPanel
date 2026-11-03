"""Typed, deliberately narrow EDFA write transactions (IPG P21-006000 Rev A pp.31–32).

No mode-selection, reset, register-clearing or physical emission API lives here.
The caller must own/serialize the injected VISA session. M4 physical writes
require site-local approval, per-request operator attestation and observed
'Off' from SOUR:POW?; that observation is NOT an optical safety interlock.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import Protocol

from hardware.edfa_gpib import GPIBReadOnlyReader, parse_status
from hardware.edfa_types import (
    EDFAConfiguration,
    EDFAControlMode,
    EDFAProtocolError,
    EDFATelemetry,
    PowerUnit,
)


class EDFAWriteKind(Enum):
    POWER = "power"
    GAIN = "gain"
    CURRENT = "current"
    POWER_UNITS = "power_units"


class EDFAWriteOutcome(Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class EDFAEmissionAction(Enum):
    OFF = 0
    ON = 1


# Deliberate small allowlist. An arbitrary string can never reach write().
_ALLOWED_PREFIXES = frozenset({"SOUR:POW", "SOUR:GAIN", "SOUR:CURR", "UNIT:POW"})
_SITE_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{7,79}\Z")


@dataclass(frozen=True, slots=True)
class EDFAWritePolicy:
    enabled: bool = False
    site_reference: str = ""
    maximum_power_w: float | None = None
    maximum_gain_db: float | None = None
    maximum_current_a: float | None = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("write opt-in must be a boolean")
        if not self.enabled:
            return
        if not _SITE_REFERENCE.fullmatch(self.site_reference):
            raise ValueError("Enabled EDFA writes require a site-local approval reference (8–80 safe characters)")
        for key in ("maximum_power_w", "maximum_gain_db", "maximum_current_a"):
            value = getattr(self, key)
            if value is None or type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Enabled EDFA writes require an approved, positive, finite {key}")


@dataclass(frozen=True, slots=True)
class EDFAWriteRequest:
    kind: EDFAWriteKind
    value: float | PowerUnit
    unit: PowerUnit | None = None
    operator_confirmed: bool = False
    emission_off_attested: bool = False
    cancelled: Event = field(default_factory=Event, compare=False, repr=False)

    @property
    def numeric_value(self) -> float:
        """Type-narrow a validated numeric setting; unit-selection is never numeric."""
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("Expected numeric EDFA setpoint value")
        return float(self.value)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EDFAWriteKind):
            raise TypeError("Unrecognized EDFA write operation")
        if self.kind is EDFAWriteKind.POWER_UNITS:
            if self.value not in (PowerUnit.W, PowerUnit.DBM) or self.unit is not None:
                raise ValueError("Device unit selection requires PowerUnit.W or PowerUnit.DBM")
        else:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.numeric_value):
                raise ValueError("EDFA setting must be a finite number, not a boolean or text")
            if self.kind is EDFAWriteKind.POWER:
                if self.unit not in (PowerUnit.W, PowerUnit.DBM):
                    raise ValueError("Power setpoint requires explicit W or dBm units")
                if self.unit is PowerUnit.W and self.numeric_value < 0:
                    raise ValueError("Linear optical power cannot be negative")
            elif self.unit is not None:
                raise ValueError("Gain/current setpoints have fixed dB/A units")
            if self.kind is EDFAWriteKind.CURRENT and self.numeric_value < 0:
                raise ValueError("Diode current cannot be negative")


@dataclass(frozen=True, slots=True)
class EDFAWriteResult:
    request: EDFAWriteRequest
    outcome: EDFAWriteOutcome
    before: float | PowerUnit | None
    after: float | PowerUnit | None
    message: str
    telemetry: EDFATelemetry | None = None
    configuration: EDFAConfiguration | None = None


class EDFAWriteSession(Protocol):
    def query(self, message: str) -> str: ...

    def write(self, message: str) -> object: ...


def format_emission_command(action: EDFAEmissionAction) -> str:
    """Typed simulator/protocol representation only; physical ON remains locked."""
    if not isinstance(action, EDFAEmissionAction):
        raise TypeError("Unknown emission action")
    return f"SOUR:POW:STAT {action.value}"


def format_write_command(request: EDFAWriteRequest) -> str:
    if request.kind is EDFAWriteKind.POWER_UNITS:
        command = f"UNIT:POW {'W' if request.value is PowerUnit.W else 'DBm'}"
    else:
        prefix = {
            EDFAWriteKind.POWER: "SOUR:POW",
            EDFAWriteKind.GAIN: "SOUR:GAIN",
            EDFAWriteKind.CURRENT: "SOUR:CURR",
        }[request.kind]
        command = f"{prefix} {request.numeric_value:.9g}"
    if command.split(" ", 1)[0] not in _ALLOWED_PREFIXES:
        raise EDFAProtocolError("Write command is not allowlisted")
    return command


def _expected_mode(kind: EDFAWriteKind) -> EDFAControlMode | None:
    return {
        EDFAWriteKind.POWER: EDFAControlMode.APC,
        EDFAWriteKind.GAIN: EDFAControlMode.AGC,
        EDFAWriteKind.CURRENT: EDFAControlMode.ACC,
        EDFAWriteKind.POWER_UNITS: None,
    }[kind]


def _read_value(request: EDFAWriteRequest, telemetry: EDFATelemetry, config: EDFAConfiguration) -> float | PowerUnit:
    if request.kind is EDFAWriteKind.POWER:
        if telemetry.power_setpoint is None:
            raise EDFAProtocolError("Power setpoint readback is absent")
        return telemetry.power_setpoint.value
    if request.kind is EDFAWriteKind.GAIN:
        if config.gain_setpoint_db is None:
            raise EDFAProtocolError("Gain setpoint readback is absent")
        return config.gain_setpoint_db
    if request.kind is EDFAWriteKind.CURRENT:
        return config.diode_current_setpoint_a
    return PowerUnit.DBM if config.status.logarithmic_power_units else PowerUnit.W


def _snapshot(session: EDFAWriteSession) -> tuple[EDFATelemetry, EDFAConfiguration]:
    reader = GPIBReadOnlyReader(session)
    telemetry = reader.read_telemetry()
    configuration = reader.read_configuration()
    status = parse_status(session.query("SOUR:POW:STAT?"))
    # Three sequential status samples are not atomic, but reject changes
    # observable between them. Do not trust a cached UI snapshot.
    if telemetry.status.raw_word != configuration.status.raw_word or status.raw_word != configuration.status.raw_word:
        raise EDFAProtocolError("Status changed during EDFA write preflight/readback (mode, units or alarms)")
    return telemetry, configuration


def _power_w(value: float, unit: PowerUnit) -> float:
    return value if unit is PowerUnit.W else 10.0 ** ((value - 30.0) / 10.0)


def _ceiling(value: float | None) -> float:
    if value is None:
        raise ValueError("Independent laboratory operating ceiling is absent")
    return value


def _check_limits(
    request: EDFAWriteRequest, policy: EDFAWritePolicy, telemetry: EDFATelemetry, config: EDFAConfiguration
) -> None:
    status = config.status
    if status.control_mode is None or status.unassigned_bits or status.known_fault_reported:
        raise ValueError("Unverified operating mode, reserved status bits or active fault flags")
    if status.emission_starting or status.three_second_startup:
        raise ValueError("Emission may be starting; setpoint write refused")
    expected_mode = _expected_mode(request.kind)
    if expected_mode is not None and status.control_mode is not expected_mode:
        raise ValueError(f"{request.kind.value} setting requires active {expected_mode.name} mode")
    if request.kind is EDFAWriteKind.GAIN and not status.agc_available:
        raise ValueError("AGC capability is not advertised")
    if request.kind is EDFAWriteKind.POWER:
        actual_unit = PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W
        if (
            request.unit is not actual_unit
            or telemetry.power_setpoint is None
            or telemetry.power_setpoint.unit is not actual_unit
        ):
            raise ValueError("Power setpoint unit is stale or differs from device reporting units")
        if config.power_limit.unit is not actual_unit:
            raise ValueError("Hardware maximum power unit is inconsistent")
        amount = float(request.value)
        if amount > config.power_limit.value or _power_w(amount, actual_unit) > _ceiling(policy.maximum_power_w):
            raise ValueError("Requested power exceeds hardware limit or approved laboratory ceiling")
    elif request.kind is EDFAWriteKind.GAIN:
        if config.gain_limit_db is None or float(request.value) > min(
            config.gain_limit_db, _ceiling(policy.maximum_gain_db)
        ):
            raise ValueError("Requested gain exceeds hardware limit or approved laboratory ceiling")
    elif request.kind is EDFAWriteKind.CURRENT:
        if float(request.value) > min(config.diode_current_limit_a, _ceiling(policy.maximum_current_a)):
            raise ValueError("Requested current exceeds hardware limit or approved laboratory ceiling")
    elif request.value is (PowerUnit.DBM if status.logarithmic_power_units else PowerUnit.W):
        raise ValueError("Instrument already reports the requested power unit")


def _verified(request: EDFAWriteRequest, after: float | PowerUnit) -> bool:
    if request.kind is EDFAWriteKind.POWER_UNITS:
        return request.value is after
    tolerance = {
        EDFAWriteKind.POWER: 0.1 if request.unit is PowerUnit.DBM else 0.0005,
        EDFAWriteKind.GAIN: 0.05,
        EDFAWriteKind.CURRENT: 0.0005,
    }[request.kind]
    return math.isclose(float(request.value), float(after), rel_tol=0.0, abs_tol=tolerance)


def execute_supervised_write(
    session: EDFAWriteSession,
    request: EDFAWriteRequest,
    policy: EDFAWritePolicy,
    *,
    physical: bool,
    pre_write_guard: Callable[[], None] | None = None,
) -> EDFAWriteResult:
    """Exactly one VISA write, no automatic retry, no rollback.

    Caller must hold the backend owner lock. Transport/post-write exceptions
    return UNKNOWN; caller must place physical backend in ERROR requiring
    explicit close, fresh operator verification and reconnect.
    """
    if not policy.enabled or not request.operator_confirmed:
        return EDFAWriteResult(
            request, EDFAWriteOutcome.REJECTED, None, None, "Write gate/operator confirmation absent"
        )
    if physical and not request.emission_off_attested:
        return EDFAWriteResult(
            request, EDFAWriteOutcome.REJECTED, None, None, "Independent emission-OFF attestation absent"
        )
    if request.cancelled.is_set():
        return EDFAWriteResult(request, EDFAWriteOutcome.CANCELLED, None, None, "Cancelled before any write")
    try:
        before_telemetry, before_config = _snapshot(session)
        _check_limits(request, policy, before_telemetry, before_config)
        if physical and before_telemetry.output_power_non_numeric_reply != "Off":
            raise ValueError("Physical SOUR:POW? did not return 'Off'; emission state not qualified for writing")
        before = _read_value(request, before_telemetry, before_config)
    except ValueError as exc:
        # Protocol errors during readback are NOT a safe validation rejection.
        if isinstance(exc, EDFAProtocolError):
            raise
        return EDFAWriteResult(request, EDFAWriteOutcome.REJECTED, None, None, str(exc))
    if request.cancelled.is_set():
        return EDFAWriteResult(request, EDFAWriteOutcome.CANCELLED, before, None, "Cancelled before any write")
    if pre_write_guard is not None:
        pre_write_guard()
    if request.cancelled.is_set():
        return EDFAWriteResult(request, EDFAWriteOutcome.CANCELLED, before, None, "Cancelled before any write")
    command = format_write_command(request)
    try:
        session.write(command)
    except Exception as exc:  # noqa: BLE001 — opaque VISA exceptions imply uncertain instrument state.
        return EDFAWriteResult(
            request,
            EDFAWriteOutcome.UNKNOWN,
            before,
            None,
            f"VISA write raised {type(exc).__name__}; command may have taken effect. Verify instrument physically.",
        )
    try:
        after_telemetry, after_config = _snapshot(session)
        # Any unexpected status transition after an attempted write can mean
        # emission/mode/fault conditions changed; never report CONFIRMED.
        changed = before_config.status.raw_word ^ after_config.status.raw_word
        expected_change = (1 << 18) if request.kind is EDFAWriteKind.POWER_UNITS else 0
        if changed & ~expected_change:
            raise EDFAProtocolError("Status changed after write; active mode/emission/fault state is unknown")
        if physical and after_telemetry.output_power_non_numeric_reply != "Off":
            raise EDFAProtocolError("Output no longer reports 'Off' after write; physical verification required")
        after = _read_value(request, after_telemetry, after_config)
        if (
            request.kind is EDFAWriteKind.POWER
            and after_telemetry.power_setpoint is not None
            and after_telemetry.power_setpoint.unit is not request.unit
        ):
            raise EDFAProtocolError("Power setpoint readback unit changed unexpectedly")
        if request.kind is EDFAWriteKind.POWER_UNITS:
            expected = request.value
            actual = PowerUnit.DBM if after_config.status.logarithmic_power_units else PowerUnit.W
            if actual is not expected:
                return EDFAWriteResult(
                    request,
                    EDFAWriteOutcome.MISMATCH,
                    before,
                    actual,
                    "Device did not confirm requested units; physical verification required",
                    after_telemetry,
                    after_config,
                )
        if not _verified(request, after):
            return EDFAWriteResult(
                request,
                EDFAWriteOutcome.MISMATCH,
                before,
                after,
                "Setpoint readback disagrees with request; physical verification required",
                after_telemetry,
                after_config,
            )
        return EDFAWriteResult(
            request,
            EDFAWriteOutcome.CONFIRMED,
            before,
            after,
            "Device readback matches request within documented numerical tolerance",
            after_telemetry,
            after_config,
        )
    except Exception as exc:  # noqa: BLE001 — arbitrary VISA reply failures leave device state uncertain.
        return EDFAWriteResult(
            request,
            EDFAWriteOutcome.UNKNOWN,
            before,
            None,
            f"Post-write verification failed ({type(exc).__name__}); state unknown; physical verification required.",
        )
