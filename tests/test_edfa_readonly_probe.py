"""Commissioning helper must remain inert unless explicitly authorized."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config_model import AppConfig
from hardware.edfa_types import EDFAOpticalPower, EDFAStatus, EDFATelemetry, PowerUnit
from tools.edfa_readonly_probe import run, summarize_telemetry


def _config(physical: bool) -> AppConfig:
    return AppConfig.from_ini_dict(
        {
            "Instruments": {
                "edfa_backend": "physical" if physical else "simulation",
                "edfa_gpib_resource_name": "GPIB0::12::INSTR",
            }
        }
    )


def test_dry_run_does_not_construct_backend() -> None:
    def fail(*args, **kwargs):
        raise AssertionError("should not construct any device")

    assert (
        run(_config(True), connect_readonly=False, operator_confirmed=False, backend_factory=fail)["mode"] == "dry-run"
    )


def test_physical_probe_requires_operator_and_physical_profile() -> None:
    def fail(*args, **kwargs):
        raise AssertionError("should not construct any device")

    with pytest.raises(ValueError, match="operator-confirmed"):
        run(_config(True), connect_readonly=True, operator_confirmed=False, backend_factory=fail)
    with pytest.raises(ValueError, match="physical"):
        run(_config(False), connect_readonly=True, operator_confirmed=True, backend_factory=fail)


def test_authorized_readonly_probe_owns_and_releases_one_backend() -> None:
    created = []

    class FakeEDFA:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))
            self.calls = []
            self.closed = False

        def connect(self):
            self.calls.append("connect")

        def identify(self):
            self.calls.append("identify")
            return "IPG EAD-2K-C"

        def read_telemetry(self):
            self.calls.append("read")
            return EDFATelemetry(
                status=EDFAStatus(1 << 0),
                output_power=EDFAOpticalPower(0.0, PowerUnit.W),
                diode_current_a=0.0,
                case_temperature_c=25.0,
                acquired_at_utc=datetime.now(UTC),
            )

        def close(self):
            self.calls.append("close")
            self.closed = True

    objects = []

    def factory(*args, **kwargs):
        obj = FakeEDFA(*args, **kwargs)
        objects.append(obj)
        return obj

    result = run(_config(True), connect_readonly=True, operator_confirmed=True, backend_factory=factory)
    assert result["mode"] == "physical-readonly"
    assert result["telemetry"]["status_raw_word"] == 1
    assert result["telemetry"]["note"].startswith("READ-ONLY")
    assert objects[0].calls == ["connect", "identify", "read", "close"]
    assert created[0][0] == ("GPIB0::12::INSTR",)


def test_telemetry_summary_never_claims_emission_safe() -> None:
    telemetry = EDFATelemetry(
        status=EDFAStatus((1 << 2) | (1 << 4)),
        output_power=EDFAOpticalPower(0, PowerUnit.W),
        diode_current_a=0,
        case_temperature_c=25,
        acquired_at_utc=datetime.now(UTC),
    )
    result = summarize_telemetry(telemetry)
    assert result["interlock_open"] and result["input_power_low"]
    assert "safe" in str(result["note"]).lower()
