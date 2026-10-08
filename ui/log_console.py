"""Bounded logging bridge and polished dockable log console."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, override

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFont, QIcon, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.typography import make_font

MAX_LOG_RECORDS = 5000
_handler_lock = threading.Lock()
_handlers: dict[str, BoundedLogHandler] = {}

LEVEL_COLORS = {
    "DEBUG": "#64748b",
    "INFO": "#1d4ed8",
    "WARNING": "#a34f08",
    "ERROR": "#b42318",
    "CRITICAL": "#7f1d1d",
}


@dataclass(frozen=True)
class LogEntry:
    """Structured immutable copy of a logging record for the viewer ring."""

    sequence: int
    timestamp: str
    levelno: int
    levelname: str
    logger_name: str
    message: str
    exception_text: str = ""
    stack_text: str = ""

    @property
    def formatted_text(self) -> str:
        header = f"{self.timestamp} | {self.levelname:<8} | {self.logger_name} | {self.message}"
        extra = self.exception_text or self.stack_text
        if self.exception_text and self.stack_text:
            extra = f"{self.exception_text}\n{self.stack_text}"
        if extra:
            header += "\n" + "\n".join(f"    {line}" for line in extra.splitlines())
        return header

    @property
    def text(self) -> str:
        """Compatibility alias for callers that display the plain-text entry."""
        return self.formatted_text


class BoundedLogHandler(logging.Handler):
    """Store structured records in a bounded, thread-safe ring."""

    def __init__(self, capacity: int = MAX_LOG_RECORDS) -> None:
        super().__init__(logging.DEBUG)
        self.capacity = capacity
        self._entries: deque[LogEntry] = deque(maxlen=capacity)
        self._sequence = 0
        self._data_lock = threading.Lock()
        self._formatter = logging.Formatter()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            exception_text = self._formatter.formatException(record.exc_info) if record.exc_info else ""
            stack_text = self._formatter.formatStack(record.stack_info) if record.stack_info else ""
            timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
            with self._data_lock:
                self._sequence += 1
                self._entries.append(
                    LogEntry(
                        sequence=self._sequence,
                        timestamp=timestamp,
                        levelno=record.levelno,
                        levelname=record.levelname,
                        logger_name=record.name,
                        message=message,
                        exception_text=exception_text,
                        stack_text=stack_text,
                    )
                )
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def snapshot(self) -> tuple[list[LogEntry], int]:
        with self._data_lock:
            return list(self._entries), self._sequence


def install_log_handler(logger: logging.Logger | None = None) -> BoundedLogHandler:
    """Install one retained bridge on LabApp without replacing its handlers."""
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


class LogHighlighter(QSyntaxHighlighter):
    """Apply restrained metadata and severity colors to plain log text."""

    def __init__(self, document) -> None:
        super().__init__(document)
        self.timestamp_format = self._format("#64748b")
        self.source_format = self._format("#475569")
        self.message_format = self._format("#1f2937")
        self.traceback_format = self._format("#64748b")
        self.severity_formats = {name: self._format(color, bold=True) for name, color in LEVEL_COLORS.items()}

    @staticmethod
    def _format(color: str, *, bold: bool = False) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        if bold:
            text_format.setFontWeight(QFont.Weight.DemiBold)
        return text_format

    @override
    def highlightBlock(self, text: str) -> None:
        if text.startswith("    "):
            self.setFormat(0, len(text), self.traceback_format)
            return
        first = text.find(" | ")
        second = text.find(" | ", first + 3) if first >= 0 else -1
        third = text.find(" | ", second + 3) if second >= 0 else -1
        if first < 0 or second < 0 or third < 0:
            self.setFormat(0, len(text), self.message_format)
            return
        self.setFormat(0, first, self.timestamp_format)
        level = text[first + 3 : second].strip()
        self.setFormat(first + 3, second - first - 3, self.severity_formats.get(level, self.message_format))
        self.setFormat(second + 3, third - second - 3, self.source_format)
        self.setFormat(third + 3, len(text) - third - 3, self.message_format)


class DiagnosticsHighlighter(QSyntaxHighlighter):
    """Color status tags in the static diagnostics report without altering it."""

    COLORS: ClassVar[dict[str, str]] = {
        "OK": "#166534",
        "MISSING_OPTIONAL": "#64748b",
        "MISCONFIGURED": "#b42318",
        "NOT_CHECKED": "#a34f08",
    }

    def __init__(self, document) -> None:
        super().__init__(document)
        self.formats = {status: LogHighlighter._format(color, bold=True) for status, color in self.COLORS.items()}
        self.heading_format = LogHighlighter._format("#334155", bold=True)
        self.rule_format = LogHighlighter._format("#cbd5e1")

    @override
    def highlightBlock(self, text: str) -> None:
        stripped = text.strip()
        if stripped and set(stripped) == {"-"}:
            self.setFormat(0, len(text), self.rule_format)
            return
        if text.startswith("["):
            end = text.find("]")
            status = text[1:end] if end > 0 else ""
            if status in self.formats:
                self.setFormat(0, end + 1, self.formats[status])
                return
        next_block = self.currentBlock().next()
        if text and next_block.isValid() and next_block.text().strip() and set(next_block.text().strip()) == {"-"}:
            self.setFormat(0, len(text), self.heading_format)


class LogConsole(QWidget):
    """Compact light log viewer with a separate environment diagnostics tab."""

    def __init__(self, logger: logging.Logger | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.logger = logger or logging.getLogger("LabApp")
        self.handler = install_log_handler(self.logger)
        self._cursor = 0
        self._paused = False
        self._visible_entries: deque[LogEntry] = deque(maxlen=MAX_LOG_RECORDS)
        self._displayed_count = 0
        self._dropped_records = 0
        self._total_retained = 0

        self.level_filter = QComboBox(self)
        self.level_filter.addItems(["All", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.level_filter.setMinimumWidth(105)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search logs…")
        self.search.setClearButtonEnabled(True)
        self.pause_button = self._icon_button(":/icons/pause.svg", "Pause logging display")
        self.clear_button = self._icon_button(":/icons/eraser.svg", "Clear displayed logs (file unaffected)")
        self.copy_button = self._icon_button(":/icons/copy.svg", "Copy visible logs")
        self.open_button = self._icon_button(":/icons/folder-open.svg", "Open application log file")

        self.run_diagnostics_button = QPushButton("Run Diagnostics", self)
        self.run_diagnostics_button.setObjectName("RunDiagnosticsButton")
        self.run_diagnostics_button.setToolTip("Run read-only environment diagnostics")
        self.run_diagnostics_button.setAccessibleName("Run environment diagnostics")

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(QLabel("Level", self))
        toolbar.addWidget(self.level_filter)
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.pause_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.open_button)
        toolbar.addSpacing(4)
        toolbar.addWidget(self.run_diagnostics_button)

        self.tabs = QTabWidget(self)
        self.log_page = QWidget(self.tabs)
        log_layout = QVBoxLayout(self.log_page)
        log_layout.setContentsMargins(0, 6, 0, 0)
        self.text = QPlainTextEdit(self.log_page)
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.document().setDocumentMargin(10)
        self.text.setStyleSheet(
            "QPlainTextEdit { background: #ffffff; color: #1f2937; border: 1px solid #e2e8f0; "
            "selection-background-color: #dbeafe; selection-color: #0f172a; }"
        )
        font = make_font("mono", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text.setFont(font)
        self.highlighter = LogHighlighter(self.text.document())
        log_layout.addWidget(self.text, 1)
        self.tabs.addTab(self.log_page, "Logs")

        self.diagnostics_page = QWidget(self.tabs)
        diagnostics_layout = QVBoxLayout(self.diagnostics_page)
        diagnostics_layout.setContentsMargins(10, 8, 10, 8)
        diagnostics_header = QHBoxLayout()
        diagnostics_title = QLabel("Environment Diagnostics", self.diagnostics_page)
        diagnostics_title.setStyleSheet("font-weight: 600; color: #334155;")
        self.copy_diagnostics_button = QPushButton("Copy Diagnostics", self.diagnostics_page)
        self.copy_diagnostics_button.setToolTip("Copy the complete diagnostics report as plain text")
        diagnostics_header.addWidget(diagnostics_title)
        diagnostics_header.addStretch(1)
        diagnostics_header.addWidget(self.copy_diagnostics_button)
        diagnostics_layout.addLayout(diagnostics_header)
        diagnostics_note = QLabel(
            "Static environment checks only. Results do not qualify physical hardware.", self.diagnostics_page
        )
        diagnostics_note.setStyleSheet("color: #64748b;")
        diagnostics_layout.addWidget(diagnostics_note)
        self.diagnostics_text = QPlainTextEdit(self.diagnostics_page)
        self.diagnostics_text.setReadOnly(True)
        self.diagnostics_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.diagnostics_text.document().setDocumentMargin(10)
        self.diagnostics_text.setStyleSheet(
            "QPlainTextEdit { background: #ffffff; color: #1f2937; border: 1px solid #e2e8f0; "
            "selection-background-color: #dbeafe; selection-color: #0f172a; }"
        )
        self.diagnostics_text.setFont(font)
        self.diagnostics_highlighter = DiagnosticsHighlighter(self.diagnostics_text.document())
        diagnostics_layout.addWidget(self.diagnostics_text, 1)
        self.tabs.addTab(self.diagnostics_page, "Diagnostics")

        self.status = QLabel("", self)
        self.status.setStyleSheet("color: #64748b; padding: 2px 3px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 7)
        layout.setSpacing(6)
        layout.addLayout(toolbar)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.status)
        self.setStyleSheet(
            "QWidget { background: #f8fafc; } QComboBox, QLineEdit { background: #ffffff; "
            "border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 6px; color: #1f2937; } "
            "QPushButton#RunDiagnosticsButton { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; "
            "border-radius: 4px; padding: 5px 10px; font-weight: 600; } "
            "QPushButton#RunDiagnosticsButton:hover { background: #dbeafe; }"
        )

        self.level_filter.currentTextChanged.connect(self._filter_changed)
        self.search.textChanged.connect(self._filter_changed)
        self.pause_button.clicked.connect(self._toggle_pause)
        self.clear_button.clicked.connect(self.clear_display)
        self.copy_button.clicked.connect(self.copy_visible)
        self.open_button.clicked.connect(self.open_log_file)
        self.copy_diagnostics_button.clicked.connect(self.copy_diagnostics)
        self.run_diagnostics_button.clicked.connect(self.run_diagnostics)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._drain)
        self._refresh_footer()
        self._timer.start()
        self._drain()

    @staticmethod
    def _icon_button(icon_path: str, accessible_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(accessible_name.replace(" ", ""))
        button.setIcon(QIcon(icon_path))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(32, 30)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        return button

    def _matches(self, entry: LogEntry) -> bool:
        level = self.level_filter.currentText()
        if level != "All" and entry.levelname != level:
            return False
        query = self.search.text().casefold()
        return not query or query in entry.formatted_text.casefold()

    def _refresh_footer(self) -> None:
        state = "Paused" if self._paused else "Live"
        suffix = (
            f" · {self._dropped_records} older viewer records dropped; file log unaffected"
            if self._dropped_records
            else ""
        )
        self.status.setText(f"{state}  ·  {self._displayed_count} visible  ·  {self._total_retained} retained{suffix}")

    def _update_display(self, new_entries: list[LogEntry], *, rebuild: bool, at_bottom: bool) -> None:
        if rebuild:
            matches = [entry for entry in self._visible_entries if self._matches(entry)]
            self._displayed_count = len(matches)
            self.text.setPlainText("\n".join(entry.formatted_text for entry in matches))
        else:
            matches = [entry for entry in new_entries if self._matches(entry)]
            if matches:
                self.text.appendPlainText("\n".join(entry.formatted_text for entry in matches))
                self._displayed_count += len(matches)
        if at_bottom:
            self.text.moveCursor(QTextCursor.MoveOperation.End)
        self._refresh_footer()

    def _drain(self) -> None:
        if self._paused:
            return
        entries, latest = self.handler.snapshot()
        first_available = entries[0].sequence if entries else latest + 1
        dropped = max(0, first_available - self._cursor - 1)
        new_entries = [entry for entry in entries if entry.sequence > self._cursor]
        self._cursor = max(self._cursor, latest)
        if dropped:
            self._dropped_records += dropped
        if not new_entries:
            self._total_retained = len(self._visible_entries)
            return
        at_bottom = self.text.verticalScrollBar().value() >= self.text.verticalScrollBar().maximum() - 1
        overflowed = False
        for entry in new_entries:
            if len(self._visible_entries) == MAX_LOG_RECORDS:
                overflowed = True
                self._dropped_records += 1
            self._visible_entries.append(entry)
        self._total_retained = len(self._visible_entries)
        self._update_display(new_entries, rebuild=overflowed, at_bottom=at_bottom)
        if dropped:
            self._refresh_footer()

    def _filter_changed(self, *_args) -> None:
        self._update_display([], rebuild=True, at_bottom=True)

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setIcon(QIcon(":/icons/play.svg" if self._paused else ":/icons/pause.svg"))
        self.pause_button.setAccessibleName("Resume logging display" if self._paused else "Pause logging display")
        self.pause_button.setToolTip(self.pause_button.accessibleName())
        self._refresh_footer()
        if not self._paused:
            self._drain()

    def clear_display(self) -> None:
        self._visible_entries.clear()
        _entries, self._cursor = self.handler.snapshot()
        self._total_retained = 0
        self._displayed_count = 0
        self.text.clear()
        self._refresh_footer()

    def copy_selected(self) -> None:
        QApplication.clipboard().setText(self.text.textCursor().selectedText().replace("\u2029", "\n"))

    def copy_visible(self) -> None:
        QApplication.clipboard().setText(self.text.toPlainText())

    def copy_diagnostics(self) -> None:
        QApplication.clipboard().setText(self.diagnostics_text.toPlainText())

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

            report_data = collect_diagnostics()
            report = render_report(report_data)
        except Exception as error:
            report = f"Environment diagnostics failed: {type(error).__name__}: {error}"
            self.logger.exception("Environment diagnostics failed.")
        else:
            count = len(report_data.get("checks", []))
            self.logger.info("Environment diagnostics completed; %d checks. See Diagnostics tab.", count)
        self.diagnostics_text.setPlainText(report)
        self.tabs.setCurrentWidget(self.diagnostics_page)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._timer.stop()
        super().closeEvent(event)

    def stop_updates(self) -> None:
        """Stop GUI polling when the owning main window finishes closing."""
        self._timer.stop()
