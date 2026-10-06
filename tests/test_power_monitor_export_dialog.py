from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog

from ui.power_monitor_export_dialog import PowerMonitorExportDialog


def _dialog(tmp_path, name="recording", formats=(True, True)):
    return PowerMonitorExportDialog(tmp_path, name, formats)


def test_dialog_initial_values_and_blank_comment(qtbot, tmp_path):
    dialog = _dialog(tmp_path)
    qtbot.addWidget(dialog)
    assert dialog.directory_edit.text() == str(tmp_path)
    assert dialog.csv_checkbox.isChecked()
    assert dialog.mat_checkbox.isChecked()
    assert dialog.comment_edit.toPlainText() == ""
    assert dialog.save_button.isEnabled()


@pytest.mark.parametrize(
    ("name", "formats", "expected_message"),
    [
        ("  ", (True, True), "Enter a base filename."),
        ("folder/name", (True, True), "path separators"),
        ("folder\\name", (True, True), "path separators"),
        ("recording", (False, False), "Select at least one"),
    ],
)
def test_dialog_validates_filename_and_format_selection(qtbot, tmp_path, name, formats, expected_message):
    dialog = _dialog(tmp_path, name, formats)
    qtbot.addWidget(dialog)
    assert not dialog.save_button.isEnabled()
    assert expected_message in dialog.validation_label.text()


def test_dialog_rejects_nonexistent_directory(qtbot, tmp_path):
    dialog = _dialog(tmp_path / "missing")
    qtbot.addWidget(dialog)
    assert not dialog.save_button.isEnabled()
    assert "existing output folder" in dialog.validation_label.text()


@pytest.mark.parametrize("name", ["recording.csv", "recording.CSV", "recording.mat", "recording.MAT"])
def test_dialog_normalizes_csv_and_mat_suffixes(qtbot, tmp_path, name):
    dialog = _dialog(tmp_path, name, (True, False))
    qtbot.addWidget(dialog)
    dialog.comment_edit.setPlainText("test µ\r\nline")
    dialog._accept_request()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.export_request.base_name == "recording"
    assert dialog.export_request.csv is True
    assert dialog.export_request.mat is False
    assert dialog.export_request.comment == "test µ\nline"


def test_dialog_preserves_other_extensions_and_cancel_has_no_request(qtbot, tmp_path):
    dialog = _dialog(tmp_path, "recording.raw", (False, True))
    qtbot.addWidget(dialog)
    dialog._accept_request()
    assert dialog.export_request.base_name == "recording.raw"

    cancelled = _dialog(tmp_path)
    qtbot.addWidget(cancelled)
    cancelled.reject()
    assert cancelled.export_request is None


def test_browse_changes_only_directory_field(qtbot, tmp_path, monkeypatch):
    dialog = _dialog(tmp_path, "original")
    qtbot.addWidget(dialog)
    target = tmp_path / "chosen"
    target.mkdir()
    seen = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: seen.append(True) or str(target))
    dialog._browse_directory()
    assert seen == [True]
    assert Path(dialog.directory_edit.text()) == target
    assert dialog.base_name_edit.text() == "original"
