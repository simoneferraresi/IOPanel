from config_model import AppConfig
from ui.ct400_calibration_dialog import CT400CalibrationDialog


def test_dialog_previews_exact_matrix_and_keeps_physical_start_disabled(qtbot):
    dialog = CT400CalibrationDialog(AppConfig(), enabled_detectors=("DE_1",))
    qtbot.addWidget(dialog)

    assert dialog.matrix.rowCount() == 18
    assert dialog.matrix.item(0, 0).text() == "A"
    assert dialog.matrix.item(14, 0).text() == "F"
    assert dialog.matrix.item(15, 0).text() == "D"
    assert dialog.matrix.item(0, 2).text() == "10 nm/s"
    assert dialog.matrix.item(15, 2).text() == "5 nm/s"
    assert dialog.matrix.item(12, 5).text() == "DE_1, DE_2, DE_3, DE_4"
    assert not dialog.start_button.isEnabled()
    assert not dialog.confirm_extra_detectors.isChecked()
    assert "not current instrument readback" in dialog.instrument_summary.text()
    assert "not physically verified laser limits" in dialog.instrument_summary.text()


def test_dialog_marks_configuration_range_mismatch_without_claiming_readback(qtbot):
    config = AppConfig()
    config.scan_defaults.max_wavelength_nm = 1550
    dialog = CT400CalibrationDialog(config)
    qtbot.addWidget(dialog)

    assert "cases: C, E, F, D" in dialog.validation.text()
    assert "not connected-laser validation" in dialog.validation.text()
