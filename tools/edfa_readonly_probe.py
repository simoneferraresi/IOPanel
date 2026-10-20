"""Operator-gated, stand-alone *read-only* EDFA GPIB commissioning helper.

Normal execution is a dry run: no VISA import, no resource manager, no query.
Physical operation requires two explicit flags and a reviewed local INI file.
Never executes on IOPanel startup; does not open the CT400 or the PM5020.
"""

from __future__ import annotations

import argparse
import configparser
import json
from pathlib import Path

from config_model import AppConfig
from hardware.edfa_types import EDFAOpticalPower, EDFATelemetry
from hardware.ipg_ead2kc import IPGEAD2KCGPIB


def read_configuration(path: Path) -> AppConfig:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as file:
        parser.read_file(file)
    return AppConfig.from_ini_dict({section: dict(parser.items(section)) for section in parser.sections()})


def _power(power: EDFAOpticalPower | None) -> dict[str, str | float] | None:
    return None if power is None else {"value": power.value, "unit": power.unit.value}


def summarize_telemetry(report: EDFATelemetry) -> dict[str, object]:
    return {
        "status_raw_word": report.status.raw_word,
        "control_mode": report.status.control_mode.name if report.status.control_mode else None,
        "interlock_open": report.status.interlock_open,
        "overtemperature": report.status.overtemperature,
        "input_power_low": report.status.input_power_low,
        "back_reflection_alarm": report.status.back_reflection_alarm,
        "emission_starting": report.status.emission_starting,
        "three_second_startup": report.status.three_second_startup,
        "output_power": _power(report.output_power),
        "input_power": _power(report.input_power),
        "minimum_input_power": _power(report.minimum_input_power),
        "power_setpoint": _power(report.power_setpoint),
        "diode_current_a": report.diode_current_a,
        "case_temperature_c": report.case_temperature_c,
        "back_reflection_percent_of_max": report.back_reflection_percent_of_max,
        "captured_utc": report.acquired_at_utc.isoformat(),
        "note": "READ-ONLY OBSERVATIONS; NOT CERTIFICATION OF SAFE EMISSION OR OPTICAL STATE",
    }


def run(
    config: AppConfig, *, connect_readonly: bool, operator_confirmed: bool, backend_factory=IPGEAD2KCGPIB
) -> dict[str, object]:
    profile = config.instruments
    if not connect_readonly:
        return {
            "mode": "dry-run",
            "edfa_backend": profile.edfa_backend,
            "resource_configured": bool(profile.edfa_gpib_resource_name),
            "message": "No VISA resource manager created; no GPIB command sent.",
        }
    if not operator_confirmed:
        raise ValueError("Physical EDFA query requires --operator-confirmed (remote/local state may change)")
    if profile.edfa_backend != "physical":
        raise ValueError("Configure edfa_backend = physical explicitly in a reviewed lab INI")
    if not profile.edfa_gpib_resource_name:
        raise ValueError("Configure the verified EDFA GPIB address; no address discovery is supported")
    device = backend_factory(
        profile.edfa_gpib_resource_name,
        timeout_ms=profile.edfa_gpib_timeout_ms,
        write_termination=profile.edfa_gpib_write_termination,
        read_termination=profile.edfa_gpib_read_termination,
    )
    try:
        device.connect()
        identity = device.identify()
        report = device.read_telemetry()
        return {"mode": "physical-readonly", "resource": profile.edfa_gpib_resource_name, "identity": identity,
                "telemetry": summarize_telemetry(report)}
    finally:
        device.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="IPG EAD-2K-C read-only GPIB validation (default: dry run)")
    parser.add_argument("--config", type=Path, default=Path("config.ini"))
    parser.add_argument("--connect-readonly", action="store_true", help="Explicitly open the configured GPIB device")
    parser.add_argument("--operator-confirmed", action="store_true", help="Confirm supervised physical setup and approval")
    arguments = parser.parse_args()
    try:
        result = run(
            read_configuration(arguments.config),
            connect_readonly=arguments.connect_readonly,
            operator_confirmed=arguments.operator_confirmed,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must report errors while still closing VISA resources.
        parser.exit(2, f"EDFA read-only probe failed: {type(exc).__name__}: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
