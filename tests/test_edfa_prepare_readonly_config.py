"""Behavioral, hardware-independent qualification of the EDFA read-only INI copier."""

from __future__ import annotations

import configparser
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.edfa_prepare_readonly_config import prepare_readonly_config

BASE = (
    "[Instruments]\n"
    "edfa_backend = physical\n"
    "edfa_gpib_resource_name = GPIB0::12::INSTR\n"
    "edfa_m4_write_enabled = false\n"
)


def _prepare(tmp_path: Path, text: str = BASE) -> tuple[Path, Path, Path]:
    folder = tmp_path / "session con spazi – misure é"
    folder.mkdir()
    source = folder / "laboratory original.ini"
    source.write_text(text, encoding="utf-8-sig")
    run = folder / "isolated output"
    run.mkdir()
    return source, run / "config.gpib.readonly.ini", run / "IOPanel-GUI.log"


def _read(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    with path.open(encoding="utf-8") as stream:
        parser.read_file(stream)
    return parser


def test_valid_copy_forces_write_off_and_redirects_logging_without_changing_source(tmp_path: Path) -> None:
    source, destination, logfile = _prepare(
        tmp_path,
        BASE.replace("edfa_m4_write_enabled = false", "edfa_m4_write_enabled = true")
        + "\n[Logging]\nlevel = INFO\nfile = old.log\n",
    )
    original = source.read_bytes()
    digest = hashlib.sha256(original).hexdigest()

    assert prepare_readonly_config(source, destination, logfile) == destination
    result = _read(destination)
    assert result.getboolean("Instruments", "edfa_m4_write_enabled") is False
    assert result.get("Instruments", "edfa_backend") == "physical"
    assert result.get("Instruments", "edfa_gpib_resource_name") == "GPIB0::12::INSTR"
    assert result.get("Logging", "file") == str(logfile)
    assert result.get("Logging", "level") == "DEBUG"
    assert source.read_bytes() == original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert not list(destination.parent.glob(".edfa-readonly-*.tmp"))


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ("[Logging]\nlevel = DEBUG\n", "Missing"),
        (BASE.replace("edfa_backend = physical", "edfa_backend = simulation"), "backend"),
        (BASE.replace("GPIB0::12::INSTR", "GPIB0::11::INSTR"), "resource"),
        ("[Instruments]\nthis is not an assignment\n", "ParsingError"),
        (BASE + "edfa_gpib_timeout_ms = 4\n", "validation"),
        (BASE + "\n[instruments]\nedfa_backend = simulation\n", "duplicate"),
    ],
)
def test_unsafe_or_malformed_input_is_rejected_without_destination(
    tmp_path: Path, content: str, problem: str
) -> None:
    source, destination, logfile = _prepare(tmp_path, content)
    original = source.read_bytes()
    with pytest.raises((ValueError, configparser.Error), match=f"(?i){problem}"):
        prepare_readonly_config(source, destination, logfile)
    assert not destination.exists()
    assert source.read_bytes() == original
    assert not list(destination.parent.glob(".edfa-readonly-*.tmp"))


def test_missing_source_does_not_create_destination(tmp_path: Path) -> None:
    source, destination, logfile = _prepare(tmp_path)
    source.unlink()
    with pytest.raises(FileNotFoundError):
        prepare_readonly_config(source, destination, logfile)
    assert not destination.exists()


def test_existing_destination_is_preserved_and_not_overwritten(tmp_path: Path) -> None:
    source, destination, logfile = _prepare(tmp_path)
    destination.write_text("previous evidence", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing"):
        prepare_readonly_config(source, destination, logfile)
    assert destination.read_text(encoding="utf-8") == "previous evidence"


def test_source_equals_destination_is_rejected(tmp_path: Path) -> None:
    source, _, logfile = _prepare(tmp_path)
    with pytest.raises(ValueError, match="different"):
        prepare_readonly_config(source, source, logfile)


def test_log_outside_evidence_directory_is_rejected(tmp_path: Path) -> None:
    source, destination, _ = _prepare(tmp_path)
    with pytest.raises(ValueError, match="same evidence directory"):
        prepare_readonly_config(source, destination, tmp_path / "wrong.log")
    assert not destination.exists()


def test_cli_with_unicode_paths_and_missing_config_produces_correct_exit_codes(tmp_path: Path) -> None:
    source, destination, logfile = _prepare(tmp_path)
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "tools.edfa_prepare_readonly_config",
        "--source",
        str(source),
        "--destination",
        str(destination),
        "--log-file",
        str(logfile),
    ]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert _read(destination).get("Logging", "file") == str(logfile)

    destination.unlink()
    source.unlink()
    failed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    assert failed.returncode == 2
    assert "preparation refused" in failed.stderr
    assert not destination.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 is Windows-only")
def test_windows_powershell_51_native_cli_argument_roundtrip(tmp_path: Path) -> None:
    """Exercise actual PowerShell native argument marshalling, not source-string checks."""
    shell = shutil.which("powershell.exe")
    uv = shutil.which("uv")
    if not shell or not uv:
        pytest.skip("Windows PowerShell 5.1 or uv is unavailable")
    source, destination, logfile = _prepare(tmp_path)
    root = Path(__file__).resolve().parents[1]
    command = (
        "& uv run --no-sync python -m tools.edfa_prepare_readonly_config "
        f"--source '{source}' --destination '{destination}' --log-file '{logfile}'; "
        "exit $LASTEXITCODE"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _read(destination).get("Logging", "file") == str(logfile)
    assert _read(destination).getboolean("Instruments", "edfa_m4_write_enabled") is False
