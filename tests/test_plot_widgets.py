from datetime import datetime, timezone

import numpy as np
import pytest

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui import plot_widgets
from ui.control_panel import ScanSettings
from ui.plot_widgets import PlotWidget, derive_scan_export_targets


def _view_range(widget):
    return widget.plot_widget.plotItem.vb.viewRange()


def test_update_plot_autoranges_to_new_finite_trace(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)

    wavelengths = np.array([1510.0, 1520.0, 1530.0])
    powers = np.array([-45.0, -12.0, -30.0])
    widget.update_plot(wavelengths, powers)

    np.testing.assert_array_equal(widget.plot_data_item.getData()[0], wavelengths)
    np.testing.assert_array_equal(widget.plot_data_item.getData()[1], powers)
    x_range, y_range = _view_range(widget)
    assert x_range[0] <= wavelengths.min() and x_range[1] >= wavelengths.max()
    assert y_range[0] <= powers.min() and y_range[1] >= powers.max()


def test_update_plot_does_not_autorange_empty_or_nonfinite_trace(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    initial_range = _view_range(widget)

    widget.update_plot(np.array([]), np.array([]))
    widget.update_plot(np.array([1.0, 2.0]), np.array([np.nan, np.inf]))

    assert _view_range(widget) == initial_range
    x_data, y_data = widget.plot_data_item.getData()
    assert x_data is None or x_data.size == 0
    assert y_data is None or y_data.size == 0


def test_autorange_keeps_frozen_reference_and_fits_only_live_trace(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference_x = np.array([100.0, 200.0])
    reference_y = np.array([-100.0, 0.0])
    widget.update_plot(reference_x, reference_y)
    widget.freeze_current_trace()

    live_x = np.array([1510.0, 1520.0])
    live_y = np.array([-30.0, -20.0])
    widget.update_plot(live_x, live_y)

    np.testing.assert_array_equal(widget.reference_plot_item.getData()[0], reference_x)
    np.testing.assert_array_equal(widget.reference_plot_item.getData()[1], reference_y)
    x_range, y_range = _view_range(widget)
    assert x_range[0] <= live_x.min() and x_range[1] >= live_x.max()
    assert y_range[0] <= live_y.min() and y_range[1] >= live_y.max()
    assert x_range[0] > reference_x.max()


@pytest.mark.parametrize(
    ("selected", "expected_stem"),
    [
        ("chip.v2.csv", "chip.v2"),
        ("chip.v2.mat", "chip.v2"),
        ("chip.csv", "chip"),
        ("chip", "chip"),
        ("2026.09.29.deviceA.run03.csv", "2026.09.29.deviceA.run03"),
    ],
)
def test_derive_scan_export_targets_preserves_logical_stem(selected, expected_stem):
    targets = derive_scan_export_targets(selected)
    assert targets == {"CSV": type(targets["CSV"])(f"{expected_stem}.csv"), "MAT": type(targets["MAT"])(f"{expected_stem}.mat")}


def _prepare_export_widget(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    settings = ScanAcquisitionSettings(1510.0, 1520.0, 25, "7", "3", "dBm", 1.9952623149688795,
                                       LaserInput.LI_2, (Detector.DE_1,))
    measurement = ScanMeasurement(settings, np.array([1510.0, 1520.0]), np.array([[-20.0, -21.0]]),
                                  (Detector.DE_1,), None, CT400ScanResultKind.SUCCESS, 0, "",
                                  "hardware.dummy_ct400.DummyCT400", True, datetime.now(timezone.utc))
    widget.set_measurement(measurement)
    return widget


def test_export_metadata_comes_from_completed_measurement_after_ui_edits(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    # Reproduce the old bug: shared UI state changes after the plot was populated.
    widget.shared_settings.resolution = "900"
    widget.shared_settings.motor_speed = "99"
    widget.shared_settings.laser_power = "42"
    widget.shared_settings.power_unit = "mW"
    path = tmp_path / "snapshot.csv"
    _stub_save_dialog(monkeypatch, path)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *_args: None)

    widget.save_scan_data()

    csv_text = path.read_text(encoding="utf-8")
    assert "# Resolution(pm): 25" in csv_text
    assert "# Speed(nm/s): 7" in csv_text
    assert "# LaserPower: 3 dBm" in csv_text
    assert "# Input: LI_2" in csv_text


def test_measurement_owns_read_only_array_copies():
    settings = ScanAcquisitionSettings(1, 2, 10, "1", "1", "mW", 1, LaserInput.LI_1, (Detector.DE_1,))
    wavelengths = np.array([1.0, 2.0])
    powers = np.array([[-3.0, -4.0]])
    measurement = ScanMeasurement(settings, wavelengths, powers, (Detector.DE_1,), None,
                                  CT400ScanResultKind.SUCCESS, 0, "", "test.Backend", False,
                                  datetime.now(timezone.utc))
    wavelengths[0] = 99
    powers[0, 0] = 99
    np.testing.assert_array_equal(measurement.wavelengths_nm, [1, 2])
    np.testing.assert_array_equal(measurement.detector_data, [[-3, -4]])
    with pytest.raises(ValueError):
        measurement.detector_data[0, 0] = 0


def _stub_save_dialog(monkeypatch, path):
    monkeypatch.setattr(plot_widgets.QFileDialog, "getSaveFileName", lambda *_args: (str(path), "CSV File (*.csv)"))


@pytest.mark.parametrize("conflicting", ["csv", "mat", "both"])
def test_export_declined_overwrite_preserves_existing_bundle(qtbot, monkeypatch, tmp_path, conflicting):
    widget = _prepare_export_widget(qtbot)
    csv_path = tmp_path / "chip.v2.csv"
    mat_path = tmp_path / "chip.v2.mat"
    initial = {}
    if conflicting in ("csv", "both"):
        csv_path.write_bytes(b"original csv")
        initial[csv_path] = b"original csv"
    if conflicting in ("mat", "both"):
        mat_path.write_bytes(b"original mat")
        initial[mat_path] = b"original mat"
    _stub_save_dialog(monkeypatch, csv_path)
    monkeypatch.setattr(plot_widgets.QMessageBox, "question", lambda *_args: plot_widgets.QMessageBox.StandardButton.No)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: pytest.fail("unexpected success dialog"))

    widget.save_scan_data()

    assert {p: p.read_bytes() for p in initial} == initial
    assert not csv_path.exists() or csv_path in initial
    assert not mat_path.exists() or mat_path in initial
    assert widget.save_btn.isEnabled()


def test_export_accepted_overwrite_writes_dotted_csv_and_mat(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    csv_path = tmp_path / "chip.v2.csv"
    mat_path = tmp_path / "chip.v2.mat"
    csv_path.write_bytes(b"old csv")
    mat_path.write_bytes(b"old mat")
    _stub_save_dialog(monkeypatch, csv_path)
    monkeypatch.setattr(plot_widgets.QMessageBox, "question", lambda *_args: plot_widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)

    widget.save_scan_data()

    assert csv_path.read_text(encoding="utf-8").startswith("# Resolution")
    assert mat_path.read_bytes().startswith(b"MATLAB 5.0 MAT-file")
    assert widget.save_btn.isEnabled()


def test_export_reports_partial_write_failure(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    csv_path = tmp_path / "chip.csv"
    mat_path = tmp_path / "chip.mat"
    _stub_save_dialog(monkeypatch, csv_path)
    monkeypatch.setattr(plot_widgets.np, "savetxt", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: pytest.fail("false all-saved success"))

    widget.save_scan_data()

    assert not csv_path.exists()
    assert mat_path.exists()
    assert warnings and "CSV: disk full" in warnings[0]
