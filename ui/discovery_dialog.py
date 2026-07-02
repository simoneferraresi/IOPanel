import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from hardware.camera import VimbaCam

logger = logging.getLogger("LabApp.DiscoveryDialog")


class CameraDiscoveryDialog(QDialog):
    """
    A dialog that discovers and displays all available Vimba cameras.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
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
        Clears the table and repopulates it by calling the static VimbaCam method.
        """
        self.table.setRowCount(0)  # Clear existing rows
        self.refresh_button.setEnabled(False)
        self.setCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()  # Update UI to show wait cursor

        try:
            cameras_info = VimbaCam.list_cameras()
            if not cameras_info:
                # Add a placeholder row if no cameras are found
                self.table.setRowCount(1)
                item = QTableWidgetItem("No Vimba cameras found on this system.")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(0, 0, item)
                self.table.setSpan(0, 0, 1, 4)  # Span the message across all columns
                return

            self.table.setRowCount(len(cameras_info))
            for row, cam_info in enumerate(cameras_info):
                name = QTableWidgetItem(cam_info.get("name", "N/A"))
                model = QTableWidgetItem(cam_info.get("model", "N/A"))
                serial = QTableWidgetItem(cam_info.get("serial", "N/A"))
                identifier = QTableWidgetItem(cam_info.get("id", "N/A"))

                # Make the identifier bold to draw attention to it
                font = identifier.font()
                font.setBold(True)
                identifier.setFont(font)

                self.table.setItem(row, 0, name)
                self.table.setItem(row, 1, model)
                self.table.setItem(row, 2, serial)
                self.table.setItem(row, 3, identifier)

            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setStretchLastSection(True)

        except Exception as e:
            logger.error(f"Error during camera discovery: {e}", exc_info=True)
            QMessageBox.critical(self, "Discovery Error", f"An error occurred while discovering cameras:\n{e}")
        finally:
            self.refresh_button.setEnabled(True)
            self.unsetCursor()
