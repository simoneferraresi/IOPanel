import logging
import threading

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from hardware.camera import VIMBA_AVAILABLE, VimbaCam

logger = logging.getLogger("LabApp.DiscoveryDialog")


class CameraDiscoveryDialog(QDialog):
    """
    A dialog that discovers and displays all available Vimba cameras.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._discovery_cancel_event = threading.Event()
        self._discovery_task: _CameraDiscoveryTask | None = None
        self.setWindowTitle("Camera Discovery")
        self.setMinimumSize(600, 300)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Informational label
        info_label = QLabel(
            "The following Vimba-compatible cameras were detected on your system.\n"
            "You can select and copy (Ctrl+C) the 'Identifier' to use in your config.ini file."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Table to display camera info
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Name", "Model", "Serial Number", "Identifier (ID)"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # Read-only
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        layout.addWidget(self.table)

        # Buttons layout
        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh List")
        self.refresh_button.setIcon(QIcon(":/icons/refresh.svg"))
        self.refresh_button.setToolTip("Refresh the detected camera list")
        self.refresh_button.setAccessibleName("Refresh camera discovery list")
        self.refresh_button.clicked.connect(self.populate_table)
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch()

        # Standard dialog buttons (e.g., Close)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)
        button_layout.addWidget(button_box)
        layout.addLayout(button_layout)

        # Initial population of the table
        self.populate_table()

    def populate_table(self):
        """
        Starts camera discovery in the global worker pool so GigE settling does
        not block the dialog's GUI thread.
        """
        self.table.setRowCount(0)  # Clear existing rows
        self.refresh_button.setEnabled(False)
        self.setCursor(Qt.CursorShape.WaitCursor)
        if not VIMBA_AVAILABLE:
            self._show_message("Vimba driver unavailable. Install the camera extra and Allied Vision SDK.")
            self._finish_discovery()
            return

        self._discovery_cancel_event.clear()
        task = _CameraDiscoveryTask(self._discovery_cancel_event)
        task.signals.updated.connect(self._show_cameras)
        task.signals.failed.connect(self._show_error)
        task.signals.finished.connect(self._finish_discovery)
        self._discovery_task = task
        QThreadPool.globalInstance().start(task)

    def _show_cameras(self, cameras_info):
        if not cameras_info:
            self._show_message("No Vimba cameras found on this system.")
            return
        self.table.setRowCount(len(cameras_info))
        for row, cam_info in enumerate(cameras_info):
            values = (
                cam_info.get("name", "N/A"),
                cam_info.get("model", "N/A"),
                cam_info.get("serial", "N/A"),
                cam_info.get("id", "N/A"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 3:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _show_message(self, message: str):
        self.table.setRowCount(1)
        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.setItem(0, 0, item)
        self.table.setSpan(0, 0, 1, 4)

    def _show_error(self, error: str):
        logger.error("Error during camera discovery: %s", error)
        self._show_message(f"Camera discovery failed: {error}")

    def _finish_discovery(self):
        self.refresh_button.setEnabled(True)
        self.unsetCursor()
        self._discovery_task = None

    def closeEvent(self, event: QCloseEvent):
        self._discovery_cancel_event.set()
        super().closeEvent(event)


class _DiscoverySignals(QObject):
    updated = Signal(object)
    failed = Signal(str)
    finished = Signal()


class _CameraDiscoveryTask(QRunnable):
    def __init__(self, cancel_event: threading.Event):
        super().__init__()
        self.cancel_event = cancel_event
        self.signals = _DiscoverySignals()

    def run(self):
        try:
            VimbaCam.list_cameras(on_update=self.signals.updated.emit, cancel_event=self.cancel_event)
        except Exception as exc:  # noqa: BLE001 - report unexpected worker errors in the dialog.
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()
