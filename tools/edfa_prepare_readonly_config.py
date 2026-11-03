"""Prepare an isolated, fail-closed EDFA read-only laboratory INI.

This CLI performs configuration I/O only. It never creates a VISA manager,
connects to an instrument, or issues SCPI commands.
"""

from __future__ import annotations

import argparse
import configparser
import io
import os
import tempfile
from pathlib import Path

from config_model import AppConfig, InstrumentsConfig

_EXPECTED_RESOURCE = "GPIB0::12::INSTR"


def _section(parser: configparser.ConfigParser, name: str) -> str | None:
    matches = [item for item in parser.sections() if item.casefold() == name.casefold()]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous duplicate [{name}] sections")
    return matches[0] if matches else None


def _validated_profile(ini: str) -> InstrumentsConfig:
    # Match the application's real INI reader, including interpolation checks.
    reader = configparser.ConfigParser()
    reader.read_string(ini)
    return AppConfig.from_ini_dict({section: dict(reader.items(section)) for section in reader.sections()}).instruments


def prepare_readonly_config(source: Path, destination: Path, log_file: Path) -> Path:
    """Write a validated read-only copy atomically, leaving source untouched.

    An absent, invalid or unsafe configuration never creates the destination.
    An existing destination is never overwritten.
    """
    source = source.resolve(strict=True)
    destination = destination.resolve()
    log_file = log_file.resolve()
    if source == destination:
        raise ValueError("The source and read-only destination must be different files")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Refusing to replace existing evidence: {destination}")
    if not destination.parent.is_dir():
        raise FileNotFoundError(f"Evidence directory does not exist: {destination.parent}")
    if log_file.parent != destination.parent:
        raise ValueError("Diagnostic GUI log must be inside the same evidence directory")
    if not source.is_file():
        raise ValueError("Source configuration must be an existing regular file")

    parser = configparser.ConfigParser(interpolation=None)
    with source.open(encoding="utf-8-sig") as stream:
        parser.read_file(stream)
    instruments = _section(parser, "Instruments")
    if instruments is None:
        raise ValueError("Missing [Instruments] section")
    if parser.get(instruments, "edfa_backend", fallback=None) != "physical":
        raise ValueError("Unexpected EDFA backend; expected physical")
    if parser.get(instruments, "edfa_gpib_resource_name", fallback=None) != _EXPECTED_RESOURCE:
        raise ValueError(f"Unexpected EDFA VISA resource; expected {_EXPECTED_RESOURCE}")

    # Override even a source INI that explicitly opts into M4 parameter writes.
    parser.set(instruments, "edfa_m4_write_enabled", "false")
    logging_section = _section(parser, "Logging")
    if logging_section is None:
        parser.add_section("Logging")
        logging_section = "Logging"
    parser.set(logging_section, "file", str(log_file))
    parser.set(logging_section, "level", "DEBUG")

    buffer = io.StringIO()
    parser.write(buffer)
    content = buffer.getvalue()
    profile = _validated_profile(content)
    if (
        profile.edfa_backend != "physical"
        or profile.edfa_gpib_resource_name != _EXPECTED_RESOURCE
        or profile.edfa_m4_write_enabled
    ):
        raise ValueError("Sanitized configuration fails the EDFA read-only safety policy")

    # A temporary file always contains the sanitized data. Nothing is published
    # as the final INI until parsing and typed validation have both succeeded.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=".edfa-readonly-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        prepared = prepare_readonly_config(args.source, args.destination, args.log_file)
    except (OSError, ValueError, configparser.Error) as exc:
        parser.exit(2, f"EDFA read-only INI preparation refused: {type(exc).__name__}: {exc}\n")
    print(f"Validated, write-disabled EDFA configuration: {prepared}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
