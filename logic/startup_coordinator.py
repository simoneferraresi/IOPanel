"""Own a responsive startup shell until the heavy MainWindow import retires."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication

from logic.startup_import_worker import MainWindowImportOutcome, MainWindowImportWorker
from logic.task_runner import TaskRunner
from ui.startup_shell import StartupShell


class MainWindowStartupCoordinator(QObject):
    _thread_finished = Signal()

    def __init__(
        self,
        application: QApplication,
        *,
        importer: Callable[[], Any],
        on_loaded: Callable[[Any], None],
        on_failure: Callable[[str], None],
        logger: logging.Logger,
    ) -> None:
        super().__init__(application)
        self._application = application
        self._importer = importer
        self._on_loaded = on_loaded
        self._on_failure = on_failure
        self._logger = logger
        self.shell = StartupShell()
        self._cancelled = False
        self._task: TaskRunner | None = None
        self._outcome: MainWindowImportOutcome | None = None
        self.shell.cancelled.connect(self._cancel)
        self._thread_finished.connect(self._complete)

    def start(self) -> None:
        # Without this guard closing the only visible shell while imports are
        # running would abandon an active native QThread and its Qt callbacks.
        self._application.setQuitOnLastWindowClosed(False)
        self.shell.show()
        self._logger.info("Startup shell shown; full GUI imports deferred.")
        QTimer.singleShot(0, self, self._begin_import)

    @Slot()
    def _begin_import(self) -> None:
        if self._cancelled:
            return
        worker = MainWindowImportWorker(self._importer)
        holder: list[MainWindowImportOutcome | None] = [None]
        worker.imported.connect(
            lambda outcome: holder.__setitem__(0, outcome),
            Qt.ConnectionType.DirectConnection,
        )
        task = TaskRunner(worker)
        self._task = task
        # QThread.finished is the lifetime boundary, not worker.imported.
        # The signal forwards the captured outcome to the GUI thread.
        task.worker_thread.finished.connect(
            lambda holder=holder: self._record_finished(holder[0]),
            Qt.ConnectionType.DirectConnection,
        )
        task.start()

    def _record_finished(self, outcome: MainWindowImportOutcome | None) -> None:
        """Callable from the worker thread: emit only, never touch Qt widgets."""
        self._outcome = outcome
        self._thread_finished.emit()

    @Slot()
    def _complete(self) -> None:
        self._task = None
        if self._cancelled:
            self._application.quit()
            return
        outcome = self._outcome
        if outcome is None or outcome.window_class is None:
            self.shell.status_label.setText("Could not load IOPanel.")
            self._on_failure(outcome.error if outcome is not None else "Import worker returned no result")
            return

        try:
            self._logger.info("MainWindow imports complete; creating full application interface.")
            self._on_loaded(outcome.window_class)
        except Exception:
            self._logger.exception("Could not construct MainWindow after its imports completed")
            self._on_failure("MainWindow construction failed; see the application log for details.")
            return
        # The full MainWindow is shown before this shell is hidden. Restore
        # normal Qt auto-quit so subsequent user closure triggers safe teardown.
        self.shell.hide()
        self.shell.deleteLater()
        self._application.setQuitOnLastWindowClosed(True)
        self._logger.info("Full MainWindow shown; startup shell retired.")

    @Slot()
    def _cancel(self) -> None:
        self._cancelled = True
        if self._task is None:
            QTimer.singleShot(0, self._application.quit)
        else:
            self._logger.info("Startup shell closed; waiting for in-flight Python imports before quitting.")
