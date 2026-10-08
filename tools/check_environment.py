"""Read-only diagnostics for the Python environment and IOPanel configuration.

This module deliberately uses package metadata and static file inspection. It
does not import camera/MATLAB SDKs or any application hardware adapters.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import platform
import re
import struct
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib import metadata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config_model import AppConfig, ConfigSemanticError


class Status(StrEnum):
    OK = "OK"
    MISSING_OPTIONAL = "MISSING_OPTIONAL"
    MISCONFIGURED = "MISCONFIGURED"
    NOT_CHECKED = "NOT_CHECKED"


@dataclass(frozen=True)
class Check:
    id: str
    category: str
    status: Status
    description: str
    observed: Any = None
    recommendation: str | None = None


def _check(
    identifier: str,
    category: str,
    status: Status,
    description: str,
    observed: Any = None,
    recommendation: str | None = None,
) -> Check:
    return Check(identifier, category, status, description, observed, recommendation)


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", version)[:4]]
    return tuple(parts + [0] * (3 - len(parts)))


def _pe_architecture(path: Path) -> str | None:
    """Return PE machine architecture, or None for non-PE/truncated files."""
    with path.open("rb") as file:
        if file.read(2) != b"MZ":
            return None
        file.seek(0x3C)
        offset_data = file.read(4)
        if len(offset_data) != 4:
            return None
        offset = struct.unpack("<I", offset_data)[0]
        file.seek(offset)
        if file.read(4) != b"PE\0\0":
            return None
        machine_data = file.read(2)
        if len(machine_data) != 2:
            return None
        return {0x8664: "x86_64", 0x14C: "x86", 0xAA64: "arm64"}.get(struct.unpack("<H", machine_data)[0], "unknown")


def _python_checks() -> list[Check]:
    version = platform.python_version()
    bits = struct.calcsize("P") * 8
    virtual_env = sys.prefix if sys.prefix != sys.base_prefix else None
    supported = sys.version_info >= (3, 12)
    return [
        _check(
            "python.version",
            "Python environment",
            Status.OK if supported else Status.MISCONFIGURED,
            f"Python {version}; IOPanel requires Python 3.12 or newer.",
            version,
            None if supported else "Use Python 3.12 or newer.",
        ),
        _check("python.executable", "Python environment", Status.OK, "Interpreter executable path.", sys.executable),
        _check(
            "python.architecture",
            "Python environment",
            Status.OK,
            f"{platform.machine()} ({bits}-bit interpreter).",
            {"machine": platform.machine(), "bits": bits},
        ),
        _check(
            "python.lab_profile_architecture",
            "Python environment",
            Status.OK
            if platform.system() == "Windows" and platform.machine().lower() in {"amd64", "x86_64"} and bits == 64
            else Status.NOT_CHECKED,
            "Interpreter matches the documented Windows x64 laboratory architecture."
            if platform.system() == "Windows" and platform.machine().lower() in {"amd64", "x86_64"} and bits == 64
            else "Interpreter does not match the documented Windows x64 laboratory profile; this is not a general IOPanel failure.",
            {"expected": "Windows x64", "observed": f"{platform.system()} {platform.machine()} {bits}-bit"},
        ),
        _check("python.os", "Python environment", Status.OK, platform.platform(), platform.system()),
        _check(
            "python.venv",
            "Python environment",
            Status.OK if virtual_env else Status.NOT_CHECKED,
            "A virtual environment is active." if virtual_env else "No virtual environment detected.",
            virtual_env,
            None if virtual_env else "Use an isolated project environment for development.",
        ),
        _check(
            "python.project",
            "Python environment",
            Status.OK,
            "Project requires Python >=3.12; qualified laboratory profile is Windows x64 / Python 3.12.8.",
            {"requires_python": ">=3.12", "qualified_profile": "Windows x64 / Python 3.12.8"},
        ),
    ]


def _dependency_checks() -> list[Check]:
    requirements = {"PySide6": "6.6.0", "numpy": "1.23.0", "scipy": "1.9.0", "pyqtgraph": "0.13.0", "pydantic": "2.0.0"}
    checks = []
    for name, minimum in requirements.items():
        version = _version(name)
        valid = version is not None and _version_tuple(version) >= _version_tuple(minimum)
        checks.append(
            _check(
                f"dependency.{name.lower()}",
                "Core dependencies",
                Status.OK if valid else Status.MISCONFIGURED,
                f"{name} {version} installed (project requirement >= {minimum})."
                if valid
                else f"Required package {name} is missing or older than {minimum}.",
                version,
                None if valid else "Run `uv sync --locked`.",
            )
        )
    return checks


def _optional_check(name: str, profile: str, compatible: set[str], category: str) -> Check:
    version = _version(name)
    if version is None:
        return _check(
            f"optional.{name}",
            category,
            Status.MISSING_OPTIONAL,
            f"{name} is not installed; its optional feature is unavailable.",
            None,
            f"Install the {name} package only if you need this feature.",
        )
    matches = version in compatible
    return _check(
        f"optional.{name}",
        category,
        Status.OK if matches else Status.NOT_CHECKED,
        f"{name} {version} metadata found; {profile}. Native runtime compatibility and feature operation were not tested.",
        {"version": version, "documented_versions": sorted(compatible), "native_runtime_verified": False},
        None if matches else f"Review the documented {profile} before laboratory use.",
    )


def _config_checks(config_path: Path) -> tuple[list[Check], AppConfig | None]:
    try:
        parser = configparser.ConfigParser()
        with config_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        raw = {section: dict(parser.items(section)) for section in parser.sections()}
        app_config = AppConfig.from_ini_dict(raw)
    except FileNotFoundError:
        return [
            _check(
                "config.file",
                "CT400 configuration",
                Status.MISCONFIGURED,
                "Configuration file was not found.",
                str(config_path),
                "Provide a valid file with --config.",
            )
        ], None
    except (OSError, UnicodeError, configparser.Error) as error:
        return [
            _check(
                "config.file",
                "CT400 configuration",
                Status.MISCONFIGURED,
                f"Configuration file could not be read or parsed ({type(error).__name__}).",
                str(config_path),
                "Check file permissions, encoding, and INI syntax.",
            )
        ], None
    except (ConfigSemanticError, ValidationError) as error:
        # Include validation messages (field-level, no config values are emitted).
        return [
            _check(
                "config.validation",
                "CT400 configuration",
                Status.MISCONFIGURED,
                f"Configuration values are invalid ({type(error).__name__}).",
                str(config_path),
                "Correct the indicated section or setting in the configuration file.",
            )
        ], None

    checks = [
        _check(
            "config.validation",
            "CT400 configuration",
            Status.OK,
            "Configuration syntax and settings passed model validation.",
            str(config_path),
        )
    ]
    dll_setting = app_config.instruments.ct400_dll_path
    if app_config.instruments.ct400_backend == "simulation":
        checks.append(
            _check(
                "ct400.dll",
                "CT400 configuration",
                Status.NOT_CHECKED,
                "CT400 backend is simulation; DLL existence was not required.",
                None,
            )
        )
    elif not dll_setting:
        checks.append(
            _check(
                "ct400.dll",
                "CT400 configuration",
                Status.MISCONFIGURED,
                "No CT400 DLL path is configured.",
                None,
                "Set Instruments.ct400_dll_path when using the physical CT400 backend.",
            )
        )
    else:
        path = Path(dll_setting).expanduser()
        try:
            if not path.is_file():
                checks.append(
                    _check(
                        "ct400.dll",
                        "CT400 configuration",
                        Status.MISCONFIGURED,
                        "Configured CT400 DLL does not exist.",
                        str(path),
                        "Correct the DLL path in the configuration.",
                    )
                )
            else:
                architecture = _pe_architecture(path)
                machine = platform.machine().lower()
                expected = (
                    "arm64" if machine in {"arm64", "aarch64"} else "x86_64" if struct.calcsize("P") == 8 else "x86"
                )
                if architecture is None:
                    checks.append(
                        _check(
                            "ct400.dll",
                            "CT400 configuration",
                            Status.NOT_CHECKED,
                            "DLL file exists, but its PE architecture could not be read.",
                            str(path),
                            "Verify the vendor DLL file; it was not loaded.",
                        )
                    )
                elif architecture != expected:
                    checks.append(
                        _check(
                            "ct400.dll",
                            "CT400 configuration",
                            Status.MISCONFIGURED,
                            f"DLL architecture {architecture} does not match interpreter architecture {expected}.",
                            {"path": str(path), "architecture": architecture, "expected": expected},
                            "Use the CT400 DLL matching this Python interpreter.",
                        )
                    )
                else:
                    checks.append(
                        _check(
                            "ct400.dll",
                            "CT400 configuration",
                            Status.OK,
                            f"DLL exists and PE architecture matches ({architecture}); hardware operation is unverified.",
                            {"path": str(path), "architecture": architecture, "hardware_verified": False},
                        )
                    )
        except OSError as error:
            checks.append(
                _check(
                    "ct400.dll",
                    "CT400 configuration",
                    Status.MISCONFIGURED,
                    f"Configured DLL could not be inspected ({type(error).__name__}).",
                    str(path),
                    "Check file permissions and the configured path.",
                )
            )
    return checks, app_config


def collect_diagnostics(config_path: Path | None = None) -> dict[str, Any]:
    config_path = config_path or Path("config.ini")
    config_checks, _ = _config_checks(config_path)
    camera_env = [name for name in ("GENICAM_GENTL64_PATH", "VIMBA_X_HOME", "VIMBA_HOME") if name in os.environ]
    matlab_env = [name for name in ("MATLABROOT", "MATLAB_HOME") if name in os.environ]
    checks = (
        _python_checks()
        + _dependency_checks()
        + [
            _optional_check(
                "vmbpy", "VmbPy 1.2.2 preferred or 1.0.5 historical profile", {"1.2.2", "1.0.5"}, "Camera software"
            ),
            _check(
                "camera.runtime",
                "Camera software",
                Status.NOT_CHECKED,
                "Vimba X runtime presence is not established by package metadata; no SDK was initialized.",
                {
                    "relevant_environment_variables_present": camera_env,
                    "runtime_verified": False,
                    "camera_operation_verified": False,
                },
                "Install/configure the documented Vimba X profile if using physical cameras.",
            ),
            _optional_check("matlabengine", "qualified MATLAB R2024b profile (24.2.2)", {"24.2.2"}, "MATLAB Engine"),
            _check(
                "matlab.runtime",
                "MATLAB Engine",
                Status.NOT_CHECKED,
                "MATLAB installation and Engine startup were not inspected or tested.",
                {
                    "engine_started": False,
                    "matlab_installation_verified": False,
                    "installation_environment_variables_present": matlab_env,
                },
            ),
        ]
        + config_checks
    )
    return {"schema_version": 1, "checks": [asdict(check) for check in checks]}


def render_report(report: dict[str, Any]) -> str:
    lines = ["IOPanel environment diagnostics", ""]
    previous = None
    for check in report["checks"]:
        if check["category"] != previous:
            previous = check["category"]
            lines.extend((previous, "-" * len(previous)))
        lines.append(f"[{check['status']}] {check['description']}")
        if check["recommendation"]:
            lines.append(f"  Action: {check['recommendation']}")
    statuses = {check["status"] for check in report["checks"]}
    overall = (
        "MISCONFIGURED"
        if Status.MISCONFIGURED in statuses
        else "REVIEW"
        if Status.NOT_CHECKED in statuses
        else "READY (static checks only)"
    )
    lines.extend(("", f"Overall assessment: {overall}", "Static diagnostics do not qualify physical hardware."))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only IOPanel environment diagnostics")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--config", type=Path, default=Path("config.ini"), help="configuration file to inspect")
    args = parser.parse_args(argv)
    report = collect_diagnostics(args.config)
    print(json.dumps(report, indent=2) if args.json else render_report(report))
    return 1 if any(check["status"] == Status.MISCONFIGURED for check in report["checks"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
