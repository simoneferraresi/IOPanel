"""Focused dialog for choosing scan export targets and an optional comment."""

from dataclasses import dataclass
from pathlib import Path

from ui.export_dialog_base import ExportDialogBase


@dataclass(frozen=True)
class ScanExportRequest:
    directory: Path
    base_name: str
    csv: bool
    mat: bool
    fig: bool
    fig_preference: bool
    comment: str


class ScanExportDialog(ExportDialogBase):
    """Collect a validated output directory, logical stem, formats, and comment."""

    def __init__(
        self,
        directory: Path,
        base_name: str,
        formats: tuple[bool, bool, bool],
        matlab_available: bool,
        parent=None,
    ) -> None:
        csv, mat, fig = formats
        super().__init__(
            title="Save Scan",
            directory=directory,
            base_name=base_name,
            csv=csv,
            mat=mat,
            parent=parent,
        )
        self._request: ScanExportRequest | None = None
        self.fig_checkbox = self._add_format_checkbox("FIG", fig)
        self.fig_checkbox.setEnabled(matlab_available)
        if not matlab_available:
            self.fig_checkbox.setToolTip("MATLAB Engine is not available")
        self._finish_initialization()

    @property
    def export_request(self) -> ScanExportRequest | None:
        return self._request

    def _accept_request(self) -> None:
        values = self._validated_values()
        if values is None:
            self._update_validation()
            return
        directory, base_name = values
        base_name = self._strip_recognized_suffix(base_name, frozenset({".csv", ".mat", ".fig"}))
        self._request = ScanExportRequest(
            directory=directory,
            base_name=base_name,
            csv=self.csv_checkbox.isChecked(),
            mat=self.mat_checkbox.isChecked(),
            fig=self.fig_checkbox.isEnabled() and self.fig_checkbox.isChecked(),
            fig_preference=self.fig_checkbox.isChecked(),
            comment=self.comment_edit.toPlainText(),
        )
        self.accept()
