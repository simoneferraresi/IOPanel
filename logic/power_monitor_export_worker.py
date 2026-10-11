"""Hardware-free staged CSV/MAT serialization for completed Power Monitor recordings.

The GUI confirms conflicts and snapshots metadata before creating this worker.
The worker never touches a QWidget, instrument, or mutable recording.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import Signal, Slot
from scipy.io import savemat

from logic.power_monitor_export import PowerMonitorExportPayload
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.power_monitor_export_worker")


@dataclass(frozen=True)
class PowerMonitorExportOutcome:
    saved: tuple[Path, ...]
    errors: tuple[str, ...]


def _write_staged_power_monitor_format(
    name: str, destination: Path, payload: PowerMonitorExportPayload, *, overwrite_confirmed: bool
) -> None:
    """Commit complete files only; never replace a target without approval."""
    fd, temporary_name = tempfile.mkstemp(prefix=".iopanel-monitor-", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as raw:
            if name == "CSV":
                with io.TextIOWrapper(raw, encoding="utf-8", newline="", write_through=True) as output:
                    np.savetxt(
                        output,
                        payload.csv_data,
                        delimiter=",",
                        header=payload.csv_header,
                        comments="",
                        fmt="%.17g",
                    )
                    output.flush()
                    os.fsync(output.buffer.fileno())
            elif name == "MAT":
                savemat(raw, payload.mat_data, do_compression=True)
                raw.flush()
                os.fsync(raw.fileno())
            else:
                raise ValueError(f"Unsupported Power Monitor export format: {name}")

        if overwrite_confirmed:
            os.replace(temporary, destination)
        else:
            # Atomic create-if-absent, including if another process raced us.
            os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


class PowerMonitorExportWorker(BaseWorker):
    """One shot: write the approved formats from a frozen GUI-created payload."""

    outcome = Signal(object)

    def __init__(
        self,
        payload: PowerMonitorExportPayload,
        targets: dict[str, Path],
        confirmed_overwrites: frozenset[Path],
    ) -> None:
        super().__init__()
        self._payload = payload
        self._targets = dict(targets)
        self._confirmed_overwrites = confirmed_overwrites

    @Slot()
    def run(self) -> None:
        saved: list[Path] = []
        errors: list[str] = []
        try:
            for name, destination in self._targets.items():
                try:
                    _write_staged_power_monitor_format(
                        name,
                        destination,
                        self._payload,
                        overwrite_confirmed=destination in self._confirmed_overwrites,
                    )
                    saved.append(destination)
                except Exception as error:
                    logger.exception("Power Monitor %s export failed: %s", name, destination)
                    errors.append(f"{name}: {error}")
            self.outcome.emit(PowerMonitorExportOutcome(tuple(saved), tuple(errors)))
        finally:
            self.finished.emit()
