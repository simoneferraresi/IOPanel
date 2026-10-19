"""Shared hardware-independent CSV/MAT export and atomic file publication.

The two CT400 export domains retain separate schemas, numerical formatting,
GUI workers and outcomes. Only the staging, serialization and per-format
failure-isolation mechanics are shared.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

import numpy as np


class CsvMatPayload(Protocol):
    """The common read-only fields provided by both export payloads."""

    @property
    def csv_data(self) -> np.ndarray: ...

    @property
    def csv_header(self) -> str: ...

    @property
    def mat_data(self) -> dict[str, object]: ...


def write_staged_csv_mat(
    name: str,
    destination: Path,
    payload: CsvMatPayload,
    *,
    overwrite_confirmed: bool,
    csv_format: str,
    temp_prefix: str,
    error_context: str,
) -> None:
    """Publish a complete file on the destination filesystem.

    A confirmed replacement uses atomic os.replace. For unconfirmed paths,
    os.link performs exclusive create-if-absent publication, including when
    another process creates the destination while we are writing.
    """
    fd, staged_name = tempfile.mkstemp(prefix=temp_prefix, suffix=".tmp", dir=destination.parent)
    stage = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            if name == "CSV":
                with io.TextIOWrapper(raw, encoding="utf-8", newline="", write_through=True) as stream:
                    np.savetxt(
                        stream,
                        payload.csv_data,
                        delimiter=",",
                        header=payload.csv_header,
                        comments="",
                        fmt=csv_format,
                    )
                    stream.flush()
                    os.fsync(stream.buffer.fileno())
            elif name == "MAT":
                # CSV-only exports never need to import SciPy.
                from scipy.io import savemat

                savemat(raw, payload.mat_data, do_compression=True)
                raw.flush()
                os.fsync(raw.fileno())
            else:
                raise ValueError(f"Unsupported {error_context} export format: {name}")

        if overwrite_confirmed:
            os.replace(stage, destination)
        else:
            os.link(stage, destination)
    finally:
        stage.unlink(missing_ok=True)


def export_selected_formats(
    targets: Mapping[str, Path],
    confirmed_overwrites: frozenset[Path],
    write_format: Callable[[str, Path, bool], None],
    *,
    logger: logging.Logger,
    error_log_template: str,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    """Save independent formats and return their ordered successes/errors.

    A failure in one format must not suppress an independent requested format.
    The caller still owns the Qt outcome/finished signals and thread lifetime.
    """
    saved: list[Path] = []
    errors: list[str] = []
    for name, destination in targets.items():
        try:
            write_format(name, destination, destination in confirmed_overwrites)
            saved.append(destination)
        except Exception as exc:
            logger.exception(error_log_template, name, destination)
            errors.append(f"{name}: {exc}")
    return tuple(saved), tuple(errors)
