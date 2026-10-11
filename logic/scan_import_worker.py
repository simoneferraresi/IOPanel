"""Driver-free, one-shot scan parsing worker.

Only file parsing happens in the worker QThread. No QWidget, CT400, camera,
piezo, MATLAB, or MainWindow state is accessed from this thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Signal, Slot

from logic.scan_import import ImportedScan, ScanImportError, load_scan
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.scan_import_worker")


@dataclass(frozen=True)
class ScanImportOutcome:
    scan: ImportedScan | None
    error: str | None
    unexpected: bool = False


class ScanImportWorker(BaseWorker):
    """Parse one schema-v2 scan; never touch application widgets."""

    outcome = Signal(object)

    def __init__(
        self,
        path: str | Path,
        *,
        loader: Callable[[str | Path], ImportedScan] = load_scan,
    ) -> None:
        super().__init__()
        self._path = Path(path)
        self._loader = loader

    @Slot()
    def run(self) -> None:
        try:
            try:
                result = ScanImportOutcome(self._loader(self._path), None)
            except ScanImportError as error:
                result = ScanImportOutcome(None, str(error))
            except Exception as error:
                logger.exception("Unexpected error reading scan %s", self._path)
                result = ScanImportOutcome(None, str(error), unexpected=True)
            self.outcome.emit(result)
        finally:
            # TaskRunner quits the owning QThread on this signal.
            self.finished.emit()
