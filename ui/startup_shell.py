"""Minimal Qt startup shell; never import the full UI graph here."""

from __future__ import annotations

from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QLabel, QMainWindow, QProgressBar, QVBoxLayout, QWidget


class StartupShell(QMainWindow):
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("iopanelStartupShell")
        self.setWindowTitle("IOPanel — Starting")
        self.setMinimumSize(460, 180)
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(14)
        self.title_label = QLabel("IOPanel")
        title_font = self.title_label.font()
        title_font.setPointSize(19)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        layout.addWidget(self.title_label)

        self.status_label = QLabel("Loading the application interface…")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        progress = QProgressBar()
        progress.setObjectName("startupProgress")
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        layout.addWidget(progress)
        layout.addStretch()
        self.setCentralWidget(root)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.cancelled.emit()
        super().closeEvent(event)
