"""Bounded, thread-safe logging history and dockable Qt viewer."""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

MAX_LOG_RECORDS = 5000
_handler_lock = threading.Lock()
_handlers: dict[str, BoundedLogHandler] = {}


@dataclass(frozen=True)
class LogEntry:
    sequence: int
    text: str


class BoundedLogHandler(logging.Handler):
    """Store formatted records in a bounded ring, safe for worker emitters."""

    def __init__(self, capacity: int = MAX_LOG_RECORDS) -> None:
        super().__init__(logging.DEBUG)
        self.capacity = capacity
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._sequence = 0
        self._data_lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%H:%M:%S"))

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            with self._data_lock:
                self._sequence += 1
                self._entries.append(LogEntry(self._sequence, text))
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def snapshot(self) -> tuple[list[LogEntry], int]:
        with self._data_lock:
            return list(self._entries), self._sequence


def install_log_handler(logger: logging.Logger | None = None) -> BoundedLogHandler:
    """Install one retained bridge on the LabApp logger without replacing handlers."""
    target = logger or logging.getLogger("LabApp")
    with _handler_lock:
        existing = _handlers.get(target.name)
        if existing is not None:
            return existing
        for handler in target.handlers:
            if isinstance(handler, BoundedLogHandler):
                _handlers[target.name] = handler
                return handler
        handler = BoundedLogHandler()
        target.addHandler(handler)
        _handlers[target.name] = handler
        return handler


class LogConsole(QWidget):
    """Controls and bounded display for LabApp log records."""

    def __init__(self, logger: logging.Logger | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.handler = install_log_handler(logger)
        self._cursor = 0
        self._paused = False
        self._cleared_at = 0
        self._visible_entries: deque[LogEntry] = deque(maxlen=MAX_LOG_RECORDS)
        self._last_rendered_sequence = 0

        self.level_filter = QComboBox(self)
        self.level_filter.addItems(["All", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search logs…")
        self.pause_button = QPushButton("Pause", self)
        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # Multiline tracebacks count as multiple blocks; this is a generous
        # hard cap for pathological sessions beyond the 5,000-record ring.
        self.text.document().setMaximumBlockCount(MAX_LOG_RECORDS * 10)
        font = QFont("Consolas")
        font.setPointSize(9)
        self.text.setFont(font)
        self.status = QLabel("", self)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Level:", self))
        toolbar.addWidget(self.level_filter)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.pause_button)
        for label, callback in (
            ("Clear Display", self.clear_display),
            ("Copy Selected", self.copy_selected),
            ("Copy Visible Logs", self.copy_visible),
            ("Open Log File", self.open_log_file),
            ("Run Environment Diagnostics", self.run_diagnostics),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(toolbar)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.status)

        self.level_filter.currentTextChanged.connect(self._refresh)
        self.search.textChanged.connect(self._refresh)
        self.pause_button.clicked.connect(self._toggle_pause)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._drain)
        self._timer.start()
        self._drain()

    def _drain(self) -> None:
        if self._paused:
            return
        entries, latest = self.handler.snapshot()
        first_available = entries[0].sequence if entries else latest + 1
        dropped = max(0, first_available - self._cursor - 1)
        new_entries = [entry for entry in entries if entry.sequence > self._cursor]
        self._cursor = max(self._cursor, latest)
        if not new_entries:
            return
        at_bottom = self.text.verticalScrollBar().value() >= self.text.verticalScrollBar().maximum() - 1
        for entry in new_entries:
            if entry.sequence > self._cleared_at:
                self._visible_entries.append(entry)
        self._refresh(append=True, at_bottom=at_bottom)
        if dropped:
            self.status.setText(f"Viewer buffer dropped {dropped} older record(s); file logging continues.")

    def _matches(self, entry: LogEntry) -> bool:
        level = self.level_filter.currentText()
        if level != "All" and f"| {level:<8} |" not in entry.text:
            return False
        query = self.search.text().casefold()
        return not query or query in entry.text.casefold()

    def _refresh(self, *_args, append: bool = False, at_bottom: bool = True) -> None:
        if append and self.level_filter.currentText() == "All" and not self.search.text():
            entries = [entry for entry in self._visible_entries if entry.sequence > self._last_rendered_sequence]
            lines = [entry.text for entry in entries]
            self._last_rendered_sequence = self._visible_entries[-1].sequence if self._visible_entries else 0
            if lines:
                self.text.appendPlainText("\n".join(lines))
            if at_bottom:
                self.text.moveCursor(QTextCursor.MoveOperation.End)
            return
        lines = [entry.text for entry in self._visible_entries if self._matches(entry)]
        self.text.setPlainText("\n".join(lines))
        if at_bottom:
            self.text.moveCursor(QTextCursor.MoveOperation.End)
        self._last_rendered_sequence = self._visible_entries[-1].sequence if self._visible_entries else 0

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setText("Resume" if self._paused else "Pause")
        if not self._paused:
            self._drain()

    def clear_display(self) -> None:
        self._visible_entries.clear()
        _entries, self._cleared_at = self.handler.snapshot()
        self._cursor = self._cleared_at
        self._last_rendered_sequence = self._cursor
        self.text.clear()
        self.status.setText("Display cleared. Log files are unchanged.")

    def copy_selected(self) -> None:
        QApplication.clipboard().setText(self.text.textCursor().selectedText().replace("\u2029", "\n"))

    def copy_visible(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def open_log_file(self) -> None:
        try:
            path = Path(getattr(self, "log_file", Path("lab_app.log"))).expanduser().resolve()
            if not path.is_file():
                QMessageBox.information(self, "Log File", f"Log file does not exist yet:\n{path}")
                return
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise OSError("The operating system declined to open the file.")
        except (OSError, RuntimeError) as error:
            QMessageBox.warning(self, "Could Not Open Log File", str(error))

    def run_diagnostics(self) -> None:
        try:
            from tools.check_environment import collect_diagnostics, render_report

            report = render_report(collect_diagnostics())
        except Exception as error:  # noqa: BLE001
            # Diagnostic failures should not close the application.
            report = f"Environment diagnostics failed: {type(error).__name__}: {error}"
        self.handler.emit(logging.LogRecord("LabApp.diagnostics", logging.INFO, __file__, 0, report, (), None))

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        super().closeEvent(event)

    def stop_updates(self) -> None:
        """Stop GUI polling when the owning main window finishes closing."""
        self._timer.stop()
