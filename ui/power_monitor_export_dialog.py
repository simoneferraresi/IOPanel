"""Power Monitor-specific CSV/MAT destination and annotation dialog."""

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)


@dataclass(frozen=True)
class PowerMonitorExportRequest:
    directory: Path
    base_name: str
    csv: bool
    mat: bool
    comment: str


class PowerMonitorExportDialog(QDialog):
    """Collect a validated folder, logical basename, formats and save-time comment."""

    def __init__(self, directory: Path, base_name: str, formats: tuple[bool, bool], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save Power Monitor Recording")
        self.setModal(True)
        self._request: PowerMonitorExportRequest | None = None

        self.directory_edit = QLineEdit(str(directory))
        self.browse_button = QPushButton("Browse…")
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory_edit)
        directory_row.addWidget(self.browse_button)

        self.base_name_edit = QLineEdit(base_name)
        self.csv_checkbox = QCheckBox("CSV")
        self.mat_checkbox = QCheckBox("MAT")
        self.csv_checkbox.setChecked(formats[0])
        self.mat_checkbox.setChecked(formats[1])

        formats_row = QHBoxLayout()
        formats_row.addWidget(self.csv_checkbox)
        formats_row.addWidget(self.mat_checkbox)
        formats_row.addStretch(1)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Optional experiment comment")
        self.comment_edit.setMinimumHeight(90)

        form = QFormLayout()
        form.addRow("Output folder:", directory_row)
        form.addRow("Base filename:", self.base_name_edit)
        form.addRow("Formats:", formats_row)
        form.addRow("Experiment comment:", self.comment_edit)

        self.validation_label = QLabel()
        self.validation_label.setWordWrap(True)
        self.validation_label.setStyleSheet("color: #b00020;")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        self.save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.validation_label)
        layout.addWidget(self.buttons)

        self.browse_button.clicked.connect(self._browse_directory)
        self.buttons.rejected.connect(self.reject)
        self.save_button.clicked.connect(self._accept_request)
        self.directory_edit.textChanged.connect(self._update_validation)
        self.base_name_edit.textChanged.connect(self._update_validation)
        self.csv_checkbox.toggled.connect(self._update_validation)
        self.mat_checkbox.toggled.connect(self._update_validation)
        self._update_validation()

    @property
    def export_request(self) -> PowerMonitorExportRequest | None:
        return self._request

    def _browse_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.directory_edit.text())
        if directory:
            self.directory_edit.setText(directory)

    def _validated_values(self) -> tuple[Path, str] | None:
        directory_text = self.directory_edit.text().strip()
        base_name = self.base_name_edit.text().strip()
        directory = Path(directory_text).expanduser() if directory_text else Path()
        if not directory_text or not directory.is_dir():
            return None
        if not base_name or "/" in base_name or "\\" in base_name:
            return None
        if not (self.csv_checkbox.isChecked() or self.mat_checkbox.isChecked()):
            return None
        return directory, base_name

    def _update_validation(self) -> None:
        directory_text = self.directory_edit.text().strip()
        base_name = self.base_name_edit.text().strip()
        if not directory_text or not Path(directory_text).expanduser().is_dir():
            message = "Choose an existing output folder."
        elif not base_name:
            message = "Enter a base filename."
        elif "/" in base_name or "\\" in base_name:
            message = "Base filename cannot contain path separators."
        elif not (self.csv_checkbox.isChecked() or self.mat_checkbox.isChecked()):
            message = "Select at least one export format."
        else:
            message = ""
        self.validation_label.setText(message)
        self.save_button.setEnabled(not message)

    def _accept_request(self) -> None:
        values = self._validated_values()
        if values is None:
            self._update_validation()
            return
        directory, base_name = values
        if Path(base_name).suffix.lower() in {".csv", ".mat"}:
            base_name = Path(base_name).with_suffix("").name
        self._request = PowerMonitorExportRequest(
            directory=directory,
            base_name=base_name,
            csv=self.csv_checkbox.isChecked(),
            mat=self.mat_checkbox.isChecked(),
            comment=self.comment_edit.toPlainText(),
        )
        self.accept()
