import builtins
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tools import check_environment as diagnostics


def write_pe(path: Path, machine: int = 0x8664) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(0x84 + 6)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x84, machine)
    path.write_bytes(data)
    return path


def statuses(checks):
    return {check.id: check.status for check in checks}


def test_python_checks_report_interpreter_and_venv(monkeypatch):
    monkeypatch.setattr(diagnostics.platform, "python_version", lambda: "3.12.8")
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(diagnostics.sys, "version_info", (3, 12, 8))
    monkeypatch.setattr(diagnostics.sys, "prefix", "venv")
    monkeypatch.setattr(diagnostics.sys, "base_prefix", "base")
    result = {check.id: check for check in diagnostics._python_checks()}
    assert result["python.version"].status == diagnostics.Status.OK
    assert result["python.architecture"].observed["bits"] in (32, 64)
    assert result["python.venv"].observed == "venv"


def test_unsupported_python_and_no_venv(monkeypatch):
    monkeypatch.setattr(diagnostics.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(diagnostics.sys, "prefix", "same")
    monkeypatch.setattr(diagnostics.sys, "base_prefix", "same")
    result = {check.id: check for check in diagnostics._python_checks()}
    assert result["python.version"].status == diagnostics.Status.MISCONFIGURED
    assert result["python.venv"].status == diagnostics.Status.NOT_CHECKED


def test_unexpected_lab_architecture_is_reported_without_failure(monkeypatch):
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: "aarch64")
    result = {check.id: check for check in diagnostics._python_checks()}
    assert result["python.lab_profile_architecture"].status == diagnostics.Status.NOT_CHECKED


def test_missing_and_installed_metadata(monkeypatch):
    monkeypatch.setattr(diagnostics, "_version", lambda package: None if package == "numpy" else "2.0")
    result = diagnostics._dependency_checks()
    assert statuses(result)["dependency.numpy"] == diagnostics.Status.MISCONFIGURED
    assert statuses(result)["dependency.pydantic"] == diagnostics.Status.OK
    monkeypatch.setattr(diagnostics, "_version", lambda _: None)
    assert (
        diagnostics._optional_check("vmbpy", "profile", {"1.2.2"}, "Camera software").status
        == diagnostics.Status.MISSING_OPTIONAL
    )


def test_core_dependency_below_required_version_is_misconfigured(monkeypatch):
    monkeypatch.setattr(diagnostics, "_version", lambda package: "1.0" if package == "pydantic" else "99.0")
    assert statuses(diagnostics._dependency_checks())["dependency.pydantic"] == diagnostics.Status.MISCONFIGURED


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.2.2", diagnostics.Status.OK), ("1.0.5", diagnostics.Status.OK), ("1.1.0", diagnostics.Status.NOT_CHECKED)],
)
def test_vmbpy_profiles(monkeypatch, version, expected):
    monkeypatch.setattr(diagnostics, "_version", lambda _: version)
    assert diagnostics._optional_check("vmbpy", "profile", {"1.2.2", "1.0.5"}, "Camera software").status == expected


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (None, diagnostics.Status.MISSING_OPTIONAL),
        ("24.2.2", diagnostics.Status.OK),
        ("23.2.1", diagnostics.Status.NOT_CHECKED),
    ],
)
def test_matlab_engine_profiles(monkeypatch, version, expected):
    monkeypatch.setattr(diagnostics, "_version", lambda _: version)
    assert diagnostics._optional_check("matlabengine", "profile", {"24.2.2"}, "MATLAB").status == expected


def test_both_optional_packages_missing_does_not_break_report(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_version", lambda _: None)
    config = tmp_path / "config.ini"
    config.write_text("[Instruments]\nct400_backend = simulation\n", encoding="utf-8")
    report = diagnostics.collect_diagnostics(config)
    found = statuses([diagnostics.Check(**check) for check in report["checks"]])
    assert found["optional.vmbpy"] == diagnostics.Status.MISSING_OPTIONAL
    assert found["optional.matlabengine"] == diagnostics.Status.MISSING_OPTIONAL


def test_config_valid_missing_malformed_and_invalid(tmp_path):
    valid = tmp_path / "valid config.ini"
    valid.write_text("[Instruments]\nct400_backend = simulation\n", encoding="utf-8")
    checks, _ = diagnostics._config_checks(valid)
    assert statuses(checks)["config.validation"] == diagnostics.Status.OK
    assert statuses(checks)["ct400.dll"] == diagnostics.Status.NOT_CHECKED
    assert diagnostics._config_checks(tmp_path / "missing.ini")[0][0].status == diagnostics.Status.MISCONFIGURED

    malformed = tmp_path / "malformed.ini"
    malformed.write_text("[Instruments\n", encoding="utf-8")
    assert diagnostics._config_checks(malformed)[0][0].status == diagnostics.Status.MISCONFIGURED
    invalid = tmp_path / "invalid.ini"
    invalid.write_text("[ScanDefaults]\nresolution_pm = 0\n", encoding="utf-8")
    assert diagnostics._config_checks(invalid)[0][0].status == diagnostics.Status.MISCONFIGURED
    missing_dll = tmp_path / "missing-dll.ini"
    missing_dll.write_text("[Instruments]\nct400_dll_path = missing path/CT400.dll\n", encoding="utf-8")
    assert statuses(diagnostics._config_checks(missing_dll)[0])["ct400.dll"] == diagnostics.Status.MISCONFIGURED


def test_dll_static_architecture_checks_and_spaces(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics.struct, "calcsize", lambda _: 8)
    path = write_pe(tmp_path / "folder with spaces" / "CT400_lib.dll")
    config = tmp_path / "dll.ini"
    config.write_text(f"[Instruments]\nct400_dll_path = {path}\n", encoding="utf-8")
    assert statuses(diagnostics._config_checks(config)[0])["ct400.dll"] == diagnostics.Status.OK
    wrong = write_pe(tmp_path / "wrong.dll", machine=0x14C)
    config.write_text(f"[Instruments]\nct400_dll_path = {wrong}\n", encoding="utf-8")
    assert statuses(diagnostics._config_checks(config)[0])["ct400.dll"] == diagnostics.Status.MISCONFIGURED


@pytest.mark.parametrize("content", [b"short", b"MZ", b"MZ" + bytes(2)])
def test_invalid_pe_header_is_not_loaded(tmp_path, content):
    path = tmp_path / "invalid.dll"
    path.write_bytes(content)
    assert diagnostics._pe_architecture(path) is None


def test_unreadable_dll_and_non_pe(tmp_path, monkeypatch):
    dll = tmp_path / "not-a-pe.dll"
    dll.write_bytes(b"not pe")
    config = tmp_path / "config.ini"
    config.write_text(f"[Instruments]\nct400_dll_path = {dll}\n", encoding="utf-8")
    assert statuses(diagnostics._config_checks(config)[0])["ct400.dll"] == diagnostics.Status.NOT_CHECKED

    original_open = Path.open

    def selective_open(self, *args, **kwargs):
        if self == dll:
            raise PermissionError
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", selective_open)
    assert statuses(diagnostics._config_checks(config)[0])["ct400.dll"] == diagnostics.Status.MISCONFIGURED


def test_json_schema_and_cli_outputs(tmp_path):
    config = tmp_path / "sim.ini"
    config.write_text("[Instruments]\nct400_backend = simulation\n", encoding="utf-8")
    report = diagnostics.collect_diagnostics(config)
    assert report["schema_version"] == 1
    assert all(
        {"id", "category", "status", "description", "observed", "recommendation"} <= set(check)
        for check in report["checks"]
    )
    result = subprocess.run(
        [sys.executable, "-m", "tools.check_environment", "--json", "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == report
    human = subprocess.run(
        [sys.executable, "-m", "tools.check_environment", "--config", str(config)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert human.stdout.startswith("IOPanel environment diagnostics\n")
    assert "Overall assessment:" in human.stdout
    assert human.stdout.rstrip().endswith("Static diagnostics do not qualify physical hardware.")


def test_hardware_entry_points_are_never_imported_or_called(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "matlab" or name.startswith(("vmbpy", "hardware.ct400", "hardware.camera")):
            raise AssertionError(f"hardware/optional runtime import attempted: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(diagnostics, "_version", lambda _: None)
    config = tmp_path / "sim.ini"
    config.write_text("[Instruments]\nct400_backend = simulation\n", encoding="utf-8")
    report = diagnostics.collect_diagnostics(config)
    assert report["checks"]
