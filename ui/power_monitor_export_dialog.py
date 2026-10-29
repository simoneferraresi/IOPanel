"""Power Monitor-specific CSV/MAT destination and annotation dialog."""

from dataclasses import dataclass
from pathlib import Path

from ui.export_dialog_base import ExportDialogBase


@dataclass(frozen=True)
class PowerMonitorExportRequest:
    directory: Path
    base_name: str
    csv: bool
    mat: bool
    comment: str


class PowerMonitorExportDialog(ExportDialogBase):
    """Collect a validated folder, logical basename, formats and save-time comment."""

    def __init__(
        self,
        directory: Path,
        base_name: str,
        formats: tuple[bool, bool],
        parent=None,
        *,
        title: str = "Save Power Monitor Recording",
    ) -> None:
        super().__init__(
            title=title,
            directory=directory,
            base_name=base_name,
            csv=formats[0],
            mat=formats[1],
            parent=parent,
        )
        self._request: PowerMonitorExportRequest | None = None
        self._finish_initialization()

    @property
    def export_request(self) -> PowerMonitorExportRequest | None:
        return self._request

    def _accept_request(self) -> None:
        values = self._validated_values()
        if values is None:
            self._update_validation()
            return
        directory, base_name = values
        base_name = self._strip_recognized_suffix(base_name, frozenset({".csv", ".mat"}))
        self._request = PowerMonitorExportRequest(
            directory=directory,
            base_name=base_name,
            csv=self.csv_checkbox.isChecked(),
            mat=self.mat_checkbox.isChecked(),
            comment=self.comment_edit.toPlainText(),
        )
        self.accept()
