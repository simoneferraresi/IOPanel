from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog

from ui.scan_export_dialog import ScanExportDialog


def test_initial_directory_name_and_default_formats(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "scan_1550nm_1560nm", (True, True, False), True)
    qtbot.addWidget(dialog)

    assert dialog.directory_edit.text() == str(tmp_path)
    assert dialog.base_name_edit.text() == "scan_1550nm_1560nm"
    assert dialog.csv_checkbox.isChecked()
    assert dialog.mat_checkbox.isChecked()
    assert not dialog.fig_checkbox.isChecked()
    assert dialog.save_button.isEnabled()


def test_restored_preferences_and_fig_disabled_without_matlab(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "scan", (False, False, True), False)
    qtbot.addWidget(dialog)

    assert not dialog.csv_checkbox.isChecked()
    assert not dialog.mat_checkbox.isChecked()
    assert dialog.fig_checkbox.isChecked()
    assert not dialog.fig_checkbox.isEnabled()
    assert "MATLAB Engine is not available" in dialog.fig_checkbox.toolTip()
    assert not dialog.save_button.isEnabled()

    dialog.csv_checkbox.setChecked(True)
    dialog.save_button.click()
    assert dialog.export_request is not None
    assert dialog.export_request.fig is False
    assert dialog.export_request.fig_preference is True


def test_no_formats_and_blank_filename_block_save(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "scan", (False, False, False), True)
    qtbot.addWidget(dialog)
    assert not dialog.save_button.isEnabled()

    dialog.csv_checkbox.setChecked(True)
    assert dialog.save_button.isEnabled()
    dialog.base_name_edit.setText("   ")
    assert not dialog.save_button.isEnabled()


def test_valid_request_normalizes_extension_and_captures_comment(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "scan.MAT", (False, True, False), True)
    qtbot.addWidget(dialog)
    dialog.comment_edit.setPlainText("first line\r\nsecond line")

    dialog.save_button.click()

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.export_request is not None
    assert dialog.export_request.directory == tmp_path
    assert dialog.export_request.base_name == "scan"
    assert (dialog.export_request.csv, dialog.export_request.mat, dialog.export_request.fig) == (False, True, False)
    assert dialog.export_request.fig_preference is False
    assert dialog.export_request.comment == "first line\nsecond line"


def test_cancel_produces_no_request(qtbot, tmp_path):
    dialog = ScanExportDialog(tmp_path, "scan", (True, True, False), True)
    qtbot.addWidget(dialog)

    dialog.reject()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.export_request is None


def test_browse_updates_dialog_directory_only(qtbot, tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    dialog = ScanExportDialog(tmp_path, "scan", (True, True, False), True)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(chosen))

    dialog._browse_directory()

    assert Path(dialog.directory_edit.text()) == chosen
    assert dialog.export_request is None
