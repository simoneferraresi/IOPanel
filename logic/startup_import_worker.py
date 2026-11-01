"""Cold-start import worker: no native hardware operations or QWidget creation.

MainWindow is imported *only* after the initial Qt shell has been shown. The
heavy Python module graph may load on a background thread; construction of
all QWidgets stays on the Qt GUI thread after this worker has retired.
"""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from PySide6.QtCore import Signal, Slot

from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.startup_import")


@dataclass(frozen=True)
class MainWindowImportOutcome:
    window_class: Any | None
    error: str | None = None


class MainWindowImportWorker(BaseWorker):
    imported = Signal(object)

    def __init__(self, importer: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._importer = importer

    @override
    @Slot()
    def run(self) -> None:
        try:
            logger.info("Importing MainWindow and its dependencies off the Qt GUI thread...")
            window_class = self._importer()
            self.imported.emit(MainWindowImportOutcome(window_class))
        except Exception:
            details = traceback.format_exc()
            logger.exception("MainWindow import failed")
            self.imported.emit(MainWindowImportOutcome(None, details))
        finally:
            self.finished.emit()
