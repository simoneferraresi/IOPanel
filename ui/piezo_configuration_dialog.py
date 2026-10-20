"""Operator-facing passive piezo inventory; never opens instruments or writes hardware configuration."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config_model import InstrumentsConfig
from hardware.piezo_ports import enumerate_piezo_ports, unique_usb_serials


class PiezoConfigurationDialog(QDialog):
    """Review USB-interface identities before manually binding stage roles."""

    def __init__(self, config: InstrumentsConfig, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Configure Piezo Controllers")
        self.setMinimumWidth(800)
        self.config = config
        self.port_table = QTableWidget(self)
        self.port_table.setColumnCount(6)
        self.port_table.setHorizontalHeaderLabels(
            ["COM port", "Windows description", "USB serial", "VID:PID", "Manufacturer", "Product"]
        )
        self.port_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.port_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.port_table.horizontalHeader().setStretchLastSection(True)

        self.left_combo = QComboBox(self)
        self.right_combo = QComboBox(self)
        self.left_combo.currentIndexChanged.connect(lambda _index: self._update_copy_enabled())
        self.right_combo.currentIndexChanged.connect(lambda _index: self._update_copy_enabled())
        self.verified = QCheckBox(
            "I have independently verified which physical controller drives Left and Right.", self
        )
        self.copy_button = QPushButton("Copy config.ini bindings", self)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_bindings)
        self.verified.toggled.connect(self._update_copy_enabled)

        root = QVBoxLayout(self)
        root.addWidget(
            QLabel(
                "This is a passive Windows USB/COM inventory. USB serials identify the interface, "
                "not necessarily the controller behind a serial adapter. No DLL, stage or laser is operated.",
                self,
            )
        )
        root.addWidget(self.port_table)
        form = QFormLayout()
        form.addRow("Physical Left stage:", self.left_combo)
        form.addRow("Physical Right stage:", self.right_combo)
        root.addLayout(form)
        root.addWidget(self.verified)
        root.addWidget(
            QLabel(
                "Only unique USB serials can be bound. 'Legacy COM' preserves the existing config.ini port. "
                "Review the mapping before copying; manually paste the generated settings into [Instruments] "
                "and restart IOPanel. This dialog never saves or connects hardware.",
                self,
            )
        )
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh passive inventory", self)
        self.refresh_button.clicked.connect(self._refresh)
        row.addWidget(self.refresh_button)
        row.addWidget(self.copy_button)
        root.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        ports = enumerate_piezo_ports()
        unique = unique_usb_serials(ports)
        self.port_table.setRowCount(len(ports))
        for index, port in enumerate(ports):
            cells = (
                port.device,
                port.description,
                port.usb_serial or "(not exposed)",
                f"{port.vid:04X}:{port.pid:04X}" if port.vid is not None and port.pid is not None else "",
                port.manufacturer,
                port.product,
            )
            for col, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.port_table.setItem(index, col, item)

        for combo, current in (
            (self.left_combo, self.config.piezo_left_usb_serial),
            (self.right_combo, self.config.piezo_right_usb_serial),
        ):
            combo.clear()
            combo.addItem("Legacy configured COM port (no USB binding)", "")
            for port in ports:
                if port.usb_serial.casefold() in unique:
                    combo.addItem(f"{port.device} — USB serial {port.usb_serial}", port.usb_serial)
            position = combo.findData(current) if current else 0
            combo.setCurrentIndex(position if position >= 0 else 0)
        self.verified.setChecked(False)
        self._update_copy_enabled()

    def _update_copy_enabled(self) -> None:
        left = self.left_combo.currentData() or ""
        right = self.right_combo.currentData() or ""
        distinct = not (left and right and left.casefold() == right.casefold())
        self.copy_button.setEnabled(self.verified.isChecked() and distinct)

    def _copy_bindings(self) -> None:
        left = self.left_combo.currentData() or ""
        right = self.right_combo.currentData() or ""
        if left and right and left.casefold() == right.casefold():
            QMessageBox.warning(self, "Duplicate identity", "Left and Right cannot use the same USB serial.")
            return
        lines = f"piezo_left_usb_serial = {left}\npiezo_right_usb_serial = {right}"
        QApplication.clipboard().setText(lines)
        self.copy_button.setText("Copied to clipboard")
