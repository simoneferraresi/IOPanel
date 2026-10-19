"""One-shot, hardware-independent schema-v2 scan CSV/MAT export worker.

The domain-specific worker retains the established six-decimal CSV format and
scan-specific outcome while using common atomic file publication.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Signal, Slot

from logic.export_file_io import export_selected_formats, write_staged_csv_mat
from logic.scan_export import ScanExportPayload
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.scan_export_worker")


@dataclass(frozen=True)
class ScanExportOutcome:
    saved: tuple[Path, ...]
    errors: tuple[str, ...]


def _write_staged_format(
    name: str, destination: Path, payload: ScanExportPayload, *, overwrite_confirmed: bool
) -> None:
    """Keep the scan worker entry point and its existing file-format policy."""
    write_staged_csv_mat(
        name,
        destination,
        payload,
        overwrite_confirmed=overwrite_confirmed,
        csv_format="%.6f",
        temp_prefix=".iopanel-scan-",
        error_context="scan",
    )


class ScanExportWorker(BaseWorker):
    """Write the GUI-authorized immutable payload without accessing Qt widgets."""

    outcome = Signal(object)

    def __init__(
        self,
        payload: ScanExportPayload,
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
            def write_format(name: str, path: Path, overwrite_confirmed: bool) -> None:
                _write_staged_format(name, path, self._payload, overwrite_confirmed=overwrite_confirmed)

            saved, errors = export_selected_formats(
                self._targets,
                self._confirmed_overwrites,
                write_format,
                logger=logger,
                error_log_template="%s scan export failed: %s",
            )
            self.outcome.emit(ScanExportOutcome(saved, errors))
        finally:
            self.finished.emit()
