"""Shared export-dialog UI contracts, independent of hardware and MATLAB."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog

from ui.export_dialog_base import ExportDialogBase
from ui.power_monitor_export_dialog import PowerMonitorExportDialog
from ui.scan_export_dialog import ScanExportDialog


def _dialog(kind: str, directory: Path, base_name: str):
    if kind == "scan":
        return ScanExportDialog(directory, base_name, (True, True, False), matlab_available=True)
    return PowerMonitorExportDialog(directory, base_name, (True, True))


@pytest.mark.parametrize("kind", ["scan", "monitor"])
def test_both_dialogs_share_form_labels_and_live_validation(qtbot, tmp_path, kind):
    dialog = _dialog(kind, tmp_path, "experiment")
    qtbot.addWidget(dialog)

    assert isinstance(dialog, ExportDialogBase)
    assert dialog.isModal()
    assert dialog.directory_edit.text() == str(tmp_path)
    assert dialog.base_name_edit.text() == "experiment"
    assert dialog.csv_checkbox.isChecked() and dialog.mat_checkbox.isChecked()
    assert dialog.comment_edit.placeholderText() == "Optional experiment comment"
    assert dialog.save_button.isEnabled()

    dialog.csv_checkbox.setChecked(False)
    dialog.mat_checkbox.setChecked(False)
    assert not dialog.save_button.isEnabled()
    assert dialog.validation_label.text() == "Select at least one export format."

    dialog.mat_checkbox.setChecked(True)
    assert dialog.save_button.isEnabled()

    dialog.base_name_edit.setText("folder/name")
    assert dialog.validation_label.text() == "Base filename cannot contain path separators."
    dialog.base_name_edit.setText("folder\\name")
    assert dialog.validation_label.text() == "Base filename cannot contain path separators."
    dialog.base_name_edit.setText("  ")
    assert dialog.validation_label.text() == "Enter a base filename."

    dialog.base_name_edit.setText("new experiment")
    assert dialog.save_button.isEnabled()
    dialog.directory_edit.setText(str(tmp_path / "missing"))
    assert dialog.validation_label.text() == "Choose an existing output folder."
    assert not dialog.save_button.isEnabled()


@pytest.mark.parametrize("kind", ["scan", "monitor"])
def test_shared_validation_blocks_programmatic_accept_and_preserves_request(qtbot, tmp_path, kind):
    dialog = _dialog(kind, tmp_path, "ok")
    qtbot.addWidget(dialog)

    dialog.base_name_edit.setText("invalid/name")
    dialog._accept_request()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.export_request is None

    dialog.base_name_edit.setText("experiment.CSV")
    dialog.comment_edit.setPlainText("line one\r\nline two")
    dialog._accept_request()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.export_request is not None
    assert dialog.export_request.base_name == "experiment"
    assert dialog.export_request.comment == "line one\nline two"


@pytest.mark.parametrize("kind", ["scan", "monitor"])
def test_cancelled_folder_browse_leaves_current_selection_unchanged(qtbot, monkeypatch, tmp_path, kind):
    dialog = _dialog(kind, tmp_path, "experiment")
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: "")

    dialog._browse_directory()

    assert dialog.directory_edit.text() == str(tmp_path)
    assert dialog.base_name_edit.text() == "experiment"
    assert dialog.export_request is None


def test_scan_fig_only_is_valid_when_matlab_available(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "figure.FIG", (False, False, True), matlab_available=True)
    qtbot.addWidget(dialog)

    assert dialog.fig_checkbox.isChecked()
    assert dialog.fig_checkbox.isEnabled()
    assert dialog.save_button.isEnabled()
    dialog._accept_request()

    assert dialog.export_request is not None
    assert dialog.export_request.base_name == "figure"
    assert not dialog.export_request.csv and not dialog.export_request.mat
    assert dialog.export_request.fig and dialog.export_request.fig_preference


def test_scan_fig_only_is_invalid_without_matlab_but_preference_survives(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "figure", (False, False, True), matlab_available=False)
    qtbot.addWidget(dialog)

    assert dialog.fig_checkbox.isChecked() and not dialog.fig_checkbox.isEnabled()
    assert not dialog.save_button.isEnabled()
    dialog._accept_request()
    assert dialog.export_request is None

    dialog.csv_checkbox.setChecked(True)
    dialog._accept_request()
    assert dialog.export_request is not None
    assert dialog.export_request.csv
    assert not dialog.export_request.fig
    assert dialog.export_request.fig_preference
