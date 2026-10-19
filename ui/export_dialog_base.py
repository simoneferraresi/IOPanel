"""Shared, UI-only folder/format/comment form for CSV/MAT export dialogs.

Each workflow retains its own request dataclass and optional export formats.
This base class never accesses instruments, MATLAB, persisted settings or files.
"""

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


class ExportDialogBase(QDialog):
    """Common folder, basename, enabled-format and annotation interaction."""

    def __init__(
        self,
        *,
        title: str,
        directory: Path,
        base_name: str,
        csv: bool,
        mat: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        self.directory_edit = QLineEdit(str(directory))
        self.browse_button = QPushButton("Browse…")
        directory_row = QHBoxLayout()
        directory_row.addWidget(self.directory_edit)
        directory_row.addWidget(self.browse_button)

        self.base_name_edit = QLineEdit(base_name)
        self._formats_row = QHBoxLayout()
        self._format_checkboxes: list[QCheckBox] = []
        self.csv_checkbox = self._add_format_checkbox("CSV", csv)
        self.mat_checkbox = self._add_format_checkbox("MAT", mat)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Optional experiment comment")
        self.comment_edit.setMinimumHeight(90)

        form = QFormLayout()
        form.addRow("Output folder:", directory_row)
        form.addRow("Base filename:", self.base_name_edit)
        form.addRow("Formats:", self._formats_row)
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

    def _add_format_checkbox(self, label: str, checked: bool) -> QCheckBox:
        checkbox = QCheckBox(label)
        checkbox.setChecked(checked)
        self._formats_row.addWidget(checkbox)
        self._format_checkboxes.append(checkbox)
        return checkbox

    def _finish_initialization(self) -> None:
        """Call once after subclasses add any optional format checkboxes."""
        self._formats_row.addStretch(1)
        self.browse_button.clicked.connect(self._browse_directory)
        self.buttons.rejected.connect(self.reject)
        self.save_button.clicked.connect(self._accept_request)
        self.directory_edit.textChanged.connect(self._update_validation)
        self.base_name_edit.textChanged.connect(self._update_validation)
        for checkbox in self._format_checkboxes:
            checkbox.toggled.connect(self._update_validation)
        self._update_validation()

    def _browse_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder", self.directory_edit.text())
        if directory:
            self.directory_edit.setText(directory)

    def _any_enabled_format_selected(self) -> bool:
        return any(checkbox.isEnabled() and checkbox.isChecked() for checkbox in self._format_checkboxes)

    def _validation_message(self) -> str:
        directory_text = self.directory_edit.text().strip()
        base_name = self.base_name_edit.text().strip()
        if not directory_text or not Path(directory_text).expanduser().is_dir():
            return "Choose an existing output folder."
        if not base_name:
            return "Enter a base filename."
        if "/" in base_name or "\\" in base_name:
            return "Base filename cannot contain path separators."
        if not self._any_enabled_format_selected():
            return "Select at least one export format."
        return ""

    def _validated_values(self) -> tuple[Path, str] | None:
        if self._validation_message():
            return None
        return Path(self.directory_edit.text().strip()).expanduser(), self.base_name_edit.text().strip()

    def _update_validation(self) -> None:
        message = self._validation_message()
        self.validation_label.setText(message)
        self.save_button.setEnabled(not message)

    @staticmethod
    def _strip_recognized_suffix(base_name: str, extensions: frozenset[str]) -> str:
        path = Path(base_name)
        return path.with_suffix("").name if path.suffix.lower() in extensions else base_name

    def _accept_request(self) -> None:
        """Build a workflow-specific request in a subclass."""
        raise NotImplementedError
