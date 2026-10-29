"""Hardware-free staged CSV/MAT serialization for completed Power Monitor recordings.

The GUI confirms conflicts and snapshots metadata before creating this worker.
The existing binary64 round-trip CSV policy is now shared with scan exports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Signal, Slot

from logic.export_file_io import CsvMatPayload, export_selected_formats, write_staged_csv_mat
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.power_monitor_export_worker")


@dataclass(frozen=True)
class PowerMonitorExportOutcome:
    saved: tuple[Path, ...]
    errors: tuple[str, ...]


def _write_staged_power_monitor_format(
    name: str, destination: Path, payload: CsvMatPayload, *, overwrite_confirmed: bool
) -> None:
    """Keep the monitor worker entry point and its existing file-format policy."""
    write_staged_csv_mat(
        name,
        destination,
        payload,
        overwrite_confirmed=overwrite_confirmed,
        temp_prefix=".iopanel-monitor-",
        error_context="Power Monitor",
    )


class PowerMonitorExportWorker(BaseWorker):
    """One shot: write the approved formats from a frozen GUI-created payload."""

    outcome = Signal(object)

    def __init__(
        self,
        payload: CsvMatPayload,
        targets: dict[str, Path],
        confirmed_overwrites: frozenset[Path],
    ) -> None:
        super().__init__()
        self._payload = payload
        self._targets = dict(targets)
        self._confirmed_overwrites = confirmed_overwrites

    @Slot()
    def run(self) -> None:
        try:

            def write_format(name: str, destination: Path, overwrite_confirmed: bool) -> None:
                _write_staged_power_monitor_format(
                    name, destination, self._payload, overwrite_confirmed=overwrite_confirmed
                )

            saved, errors = export_selected_formats(
                self._targets,
                self._confirmed_overwrites,
                write_format,
                logger=logger,
                error_log_template="Power Monitor %s export failed: %s",
            )
            self.outcome.emit(PowerMonitorExportOutcome(saved, errors))
        finally:
            self.finished.emit()
