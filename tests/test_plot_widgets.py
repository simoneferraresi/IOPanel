from datetime import UTC, datetime

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui import plot_widgets
from ui.alignment_panel import AlignmentPanel
from ui.control_panel import ScanSettings
from ui.plot_widgets import PlotWidget, derive_scan_export_targets


class _CapturedSurface:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def setGLOptions(self, _options):
        pass


@pytest.mark.parametrize(
    ("x", "y", "z"),
    [
        ([10, 20], [1, 2, 3], [[11, 12, 13], [21, 22, 23]]),
        ([10, 20, 30], [1, 2], [[11, 12], [21, 22], [31, 32]]),
        ([0, 1], [10, 20], [[1, 2], [100, 200]]),
    ],
)
def test_plot3d_surface_preserves_mapping_xy_orientation(qtbot, monkeypatch, x, y, z):
    captured = {}

    def make_surface(**kwargs):
        captured.update(kwargs)
        return _CapturedSurface(**kwargs)

    monkeypatch.setattr(plot_widgets.gl, "GLSurfacePlotItem", make_surface)
    widget = plot_widgets.Plot3DWidget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget.view, "addItem", lambda _item: None)

    widget.update_plot(np.asarray(x), np.asarray(y), np.asarray(z))

    surface_z = captured["z"]
    assert surface_z.shape == (len(x), len(y))
    z_array = np.asarray(z)
    z_span = z_array.max() - z_array.min()
    expected_z = (z_array - z_array.min()) * (max(max(x) - min(x), max(y) - min(y)) * 0.3 / z_span)
    np.testing.assert_allclose(surface_z, expected_z)
    np.testing.assert_array_equal(captured["x"], np.asarray(x) - (min(x) + max(x)) / 2)
    np.testing.assert_array_equal(captured["y"], np.asarray(y) - (min(y) + max(y)) / 2)


def test_plot3d_surface_colors_follow_same_xy_orientation(qtbot, monkeypatch):
    class FakeColorMap:
        @staticmethod
        def map(values, _mode):
            return np.stack((values, values + 10, values + 20, np.ones_like(values)), axis=-1)

    captured = {}
    monkeypatch.setattr(
        plot_widgets.gl, "GLSurfacePlotItem", lambda **kwargs: captured.update(kwargs) or _CapturedSurface(**kwargs)
    )
    monkeypatch.setattr(plot_widgets.pg.colormap, "get", lambda _name: FakeColorMap())
    widget = plot_widgets.Plot3DWidget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget.view, "addItem", lambda _item: None)
    z = np.array([[1, 2, 3], [21, 22, 23]])

    widget.update_plot(np.array([10, 20]), np.array([1, 2, 3]), z)

    assert captured["z"].shape == captured["colors"].shape[:2] == z.shape
    np.testing.assert_array_equal(captured["colors"][..., 0], (z - z.min()) / (z.max() - z.min()))
    np.testing.assert_array_equal(captured["colors"][..., 1], captured["colors"][..., 0] + 10)


def test_plot3d_rejects_mapping_shape_mismatch(qtbot):
    widget = plot_widgets.Plot3DWidget()
    qtbot.addWidget(widget)

    with pytest.raises(ValueError, match=r"z shape \(3, 2\).*expected \(2, 3\)"):
        widget.update_plot(np.array([10, 20]), np.array([1, 2, 3]), np.ones((3, 2)))


def test_mapping_finished_reports_peak_using_x_then_y_indices(monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox

    peak_titles = []
    panel = SimpleNamespace(
        plot3d_widget=SimpleNamespace(
            update_plot=lambda *_args: None,
            title_label=SimpleNamespace(setText=peak_titles.append),
        ),
        reset_buttons=lambda: None,
        status_label=SimpleNamespace(setText=lambda *_args: None),
        map_progress=SimpleNamespace(setVisible=lambda *_args: None),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    grid = np.array([[1, 2, 900], [3, 4, 5]])

    AlignmentPanel.on_mapping_finished(panel, np.array([10, 20]), np.array([1, 2, 3]), grid)

    assert "X=10.00, Y=3.00" in peak_titles[0]


@pytest.mark.parametrize("z", [np.full((2, 3), 7.0), np.full((1, 3), 7.0), np.full((3, 1), 7.0)])
def test_plot3d_flat_surface_preserves_orientation(qtbot, monkeypatch, z):
    captured = {}
    monkeypatch.setattr(
        plot_widgets.gl, "GLSurfacePlotItem", lambda **kwargs: captured.update(kwargs) or _CapturedSurface(**kwargs)
    )
    widget = plot_widgets.Plot3DWidget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget.view, "addItem", lambda _item: None)
    monkeypatch.setattr(widget.grid_item, "setSpacing", lambda **_kwargs: None)
    x = np.arange(z.shape[0], dtype=float)
    y = np.arange(z.shape[1], dtype=float)

    widget.update_plot(x, y, z)

    assert captured["z"].shape == z.shape
    np.testing.assert_array_equal(captured["z"], np.zeros_like(z))


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


@pytest.mark.parametrize("_iteration", range(50))
def test_status_clear_timer_is_owned_restartable_and_stopped_on_cleanup(qtbot, _iteration):
    widget = PlotWidget(ScanSettings())
    status_label = widget.matlab_status_label
    label_destroyed = QSignalSpy(status_label.destroyed)
    widget.pending_saves = 0
    widget.saved_files_list = []
    widget.error_list = []
    status_label.setText("scan.csv saved.")

    widget._check_all_saves_done()
    status_timer = widget._status_clear_timer
    timeout_count = QSignalSpy(status_timer.timeout)

    assert status_timer.parent() is widget
    assert status_timer.isSingleShot()
    assert status_timer.interval() == 3000
    assert status_timer.isActive()

    qtbot.wait(100)
    first_remaining_time = status_timer.remainingTime()
    status_label.setText("mat.csv saved.")
    widget._check_all_saves_done()
    assert status_timer.remainingTime() > first_remaining_time

    widget.close()
    assert not status_timer.isActive()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()

    assert label_destroyed.count() == 1
    qtbot.wait(20)
    assert timeout_count.count() == 0


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


def _detector_measurement(wavelengths, detector_data, detectors, *, final_pout=None, completed_at_utc=None):
    settings = ScanAcquisitionSettings(
        1510.0, 1520.0, 25, "7", "3", "dBm", 1.9952623149688795, LaserInput.LI_2, tuple(detectors)
    )
    return ScanMeasurement(
        settings, np.asarray(wavelengths), np.asarray(detector_data), tuple(detectors), final_pout,
        CT400ScanResultKind.SUCCESS, 0, "", "hardware.dummy_ct400.DummyCT400", True,
        completed_at_utc or datetime.now(UTC),
    )


def _plotted_data(item):
    x_data, y_data = item.getData()
    if x_data is None or y_data is None:
        return np.array([]), np.array([])
    return x_data, y_data


def test_set_measurement_plots_detector_rows_by_identity_and_keeps_first_row_compatibility(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    wavelengths = np.array([1510.0, 1515.0, 1520.0])
    powers = np.array([[-3.0, -4.0, -5.0], [-30.0, -40.0, -50.0]])
    measurement = _detector_measurement(wavelengths, powers, (Detector.DE_3, Detector.DE_1))

    assert widget.set_measurement(measurement)

    assert set(widget.detector_plot_items) >= {Detector.DE_1, Detector.DE_3}
    for detector, row in zip(measurement.detectors, measurement.detector_data, strict=True):
        item = widget.detector_plot_items[detector]
        assert item.isVisible()
        np.testing.assert_array_equal(_plotted_data(item)[0], wavelengths)
        np.testing.assert_array_equal(_plotted_data(item)[1], row)
    assert [label.text for _sample, label in widget.detector_legend.items] == ["Det 3", "Det 1"]
    np.testing.assert_array_equal(widget.current_powers, powers[0])
    assert widget.current_measurement is measurement
    assert widget.current_wavelengths is measurement.wavelengths_nm
    assert widget.current_output_power is None
    assert widget.save_btn.isEnabled()
    assert widget.freeze_btn.isEnabled()


def test_detector_style_and_label_follow_identity_when_row_order_changes(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    wavelengths = np.array([1.0, 2.0])
    widget.set_measurement(_detector_measurement(wavelengths, [[3, 4], [1, 2]], (Detector.DE_3, Detector.DE_1)))
    styles = {detector: item.opts["pen"].color().name() for detector, item in widget.detector_plot_items.items()}
    widget.set_measurement(_detector_measurement(wavelengths, [[1, 2], [3, 4]], (Detector.DE_1, Detector.DE_3)))

    assert {detector: item.opts["pen"].color().name() for detector, item in widget.detector_plot_items.items()} == styles
    assert [label.text for _sample, label in widget.detector_legend.items] == ["Det 1", "Det 3"]
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_1])[1], [1, 2])
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_3])[1], [3, 4])


def test_set_measurement_removes_stale_live_detector_and_legend_entry(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    wavelengths = np.array([1.0, 2.0])
    widget.set_measurement(
        _detector_measurement(wavelengths, [[1, 2], [3, 4], [5, 6]], (Detector.DE_1, Detector.DE_2, Detector.DE_3))
    )
    widget.set_measurement(_detector_measurement(wavelengths, [[7, 8], [9, 10]], (Detector.DE_1, Detector.DE_3)))

    assert not widget.detector_plot_items[Detector.DE_2].isVisible()
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_2])[0], [])
    assert [label.text for _sample, label in widget.detector_legend.items] == ["Det 1", "Det 3"]


def test_set_measurement_filters_nonfinite_points_independently_per_detector(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    wavelengths = np.array([1.0, 2.0, 3.0, np.inf])
    powers = np.array([[10.0, 11.0, 12.0, 13.0], [20.0, np.nan, 22.0, 23.0], [np.nan, np.inf, np.nan, 0.0]])
    measurement = _detector_measurement(
        wavelengths, powers, (Detector.DE_1, Detector.DE_2, Detector.DE_3)
    )

    assert widget.set_measurement(measurement)

    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_1])[0], [1, 2, 3])
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_1])[1], [10, 11, 12])
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_2])[0], [1, 3])
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_2])[1], [20, 22])
    assert not widget.detector_plot_items[Detector.DE_3].isVisible()
    np.testing.assert_array_equal(measurement.detector_data, powers)
    assert widget.current_measurement is measurement


def test_multi_detector_autorange_uses_all_live_rows_and_excludes_frozen_snapshot(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference_x = np.array([100.0, 200.0])
    widget.set_measurement(
        _detector_measurement(reference_x, [[-100.0, 0.0], [-80.0, -10.0]], (Detector.DE_1, Detector.DE_2))
    )
    widget.freeze_current_trace()
    live_x = np.array([1510.0, 1520.0])
    live = np.array([[-30.0, -20.0], [5.0, 15.0]])
    widget.set_measurement(_detector_measurement(live_x, live, (Detector.DE_1, Detector.DE_2)))

    x_range, y_range = _view_range(widget)
    assert x_range[0] <= live_x.min() and x_range[1] >= live_x.max()
    assert y_range[0] <= live.min() and y_range[1] >= live.max()
    assert x_range[0] > reference_x.max()
    for detector, row in zip((Detector.DE_1, Detector.DE_2), [[-100.0, 0.0], [-80.0, -10.0]], strict=True):
        np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[detector])[1], row)


def test_set_measurement_does_not_autorange_when_all_detector_rows_are_nonfinite(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    initial_range = _view_range(widget)
    measurement = _detector_measurement(
        [1.0, 2.0], [[np.nan, np.inf], [-np.inf, np.nan]], (Detector.DE_1, Detector.DE_2)
    )

    assert widget.set_measurement(measurement)

    assert _view_range(widget) == initial_range
    assert all(not item.isVisible() for item in widget.detector_plot_items.values())


def test_freeze_snapshots_all_detectors_and_re_freeze_replaces_snapshot(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    wavelengths = np.array([1510.0, 1520.0])
    first = _detector_measurement(wavelengths, [[1, 2], [3, 4]], (Detector.DE_1, Detector.DE_3))
    widget.set_measurement(first)
    widget.freeze_current_trace()
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_1])[1], [1, 2])
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_3])[1], [3, 4])
    assert (
        widget.reference_detector_plot_items[Detector.DE_1].opts["pen"].color().name()
        == widget.detector_plot_items[Detector.DE_1].opts["pen"].color().name()
    )
    assert widget.reference_detector_plot_items[Detector.DE_1].opts["pen"].widthF() < widget.detector_plot_items[
        Detector.DE_1
    ].opts["pen"].widthF()

    second = _detector_measurement(wavelengths, [[11, 12], [13, 14]], (Detector.DE_1, Detector.DE_3))
    widget.set_measurement(second)
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_1])[1], [1, 2])
    widget.freeze_current_trace()

    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_1])[1], [11, 12])
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_3])[1], [13, 14])
    assert set(widget.reference_detector_plot_items) == {Detector.DE_1, Detector.DE_3}


def test_clear_plot_clears_detector_items_legend_and_measurement_state(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    measurement = _detector_measurement([1.0, 2.0], [[3, 4], [5, 6]], (Detector.DE_1, Detector.DE_3))
    widget.set_measurement(measurement)
    widget.freeze_current_trace()

    widget.clear_plot()

    for item in (*widget.detector_plot_items.values(), *widget.reference_detector_plot_items.values()):
        assert not item.isVisible()
        x_data, y_data = _plotted_data(item)
        assert x_data.size == 0 and y_data.size == 0
    assert widget.detector_legend is not None and not widget.detector_legend.isVisible()
    assert widget.detector_legend.items == []
    assert widget.current_wavelengths is None
    assert widget.current_powers is None
    assert widget.current_output_power is None
    assert widget.current_measurement is None
    assert not widget.save_btn.isEnabled()
    assert not widget.freeze_btn.isEnabled()


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
                                  "hardware.dummy_ct400.DummyCT400", True, datetime.now(UTC))
    widget.set_measurement(measurement)
    return widget


def _measurement(wavelengths, detector_data):
    settings = ScanAcquisitionSettings(1510.0, 1520.0, 25, "7", "3", "dBm", 1.9952623149688795,
                                      LaserInput.LI_2, (Detector.DE_1,))
    return ScanMeasurement(settings, np.asarray(wavelengths), np.asarray(detector_data), (Detector.DE_1,), None,
                           CT400ScanResultKind.SUCCESS, 0, "", "hardware.dummy_ct400.DummyCT400", True,
                           datetime.now(UTC))


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
    assert "# SchemaVersion: 2" in csv_text
    assert "# ActiveDetectorCount: 1" in csv_text
    assert "# ActiveDetectors: DE_1" in csv_text
    assert "# OpticalDetectorMask(DE1-DE4): 1,0,0,0" in csv_text


def test_multidetector_csv_and_mat_export_schema_v2(qtbot, monkeypatch, tmp_path):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    measurement = _detector_measurement(
        [1510.0, 1515.0, 1520.0],
        [[30.0, 31.0, 32.0], [10.0, 11.0, 12.0]],
        (Detector.DE_3, Detector.DE_1),
        final_pout=-20.0,
        completed_at_utc=datetime(2026, 9, 29, 12, 34, 56, tzinfo=UTC),
    )
    widget.set_measurement(measurement)
    csv_path = tmp_path / "multi.detector.v2.csv"
    _stub_save_dialog(monkeypatch, csv_path)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)

    widget.save_scan_data()

    lines = csv_path.read_text(encoding="utf-8").splitlines()
    expected_header = (
        "# WL_[nm], Transfer_function_Det_1_[dB], Transfer_function_Det_2_[dB], "
        "Transfer_function_Det_3_[dB], Transfer_function_Det_4_[dB]"
    )
    assert next(line for line in lines if line.startswith("# WL_[nm]")) == expected_header
    assert "# SchemaVersion: 2" in lines
    assert "# ActiveDetectorCount: 2" in lines
    assert "# ActiveDetectors: DE_3,DE_1" in lines
    assert "# OpticalDetectorMask(DE1-DE4): 1,0,1,0" in lines
    assert "# CompletedAtUTC: 2026-09-29T12:34:56Z" in lines
    assert sum(line.startswith("# Pout(dBm):") for line in lines) == 1
    assert not any("Pout_[dBm]" in line for line in lines)
    assert not any("Power_Det1_[dB]" in line or "Power_Det_1_[dB]" in line for line in lines)
    exported = np.loadtxt(csv_path, delimiter=",", comments="#")
    np.testing.assert_array_equal(exported[:, 0], [1510.0, 1515.0, 1520.0])
    np.testing.assert_array_equal(exported[:, 1], [10.0, 11.0, 12.0])
    np.testing.assert_array_equal(exported[:, 2], [0.0, 0.0, 0.0])
    np.testing.assert_array_equal(exported[:, 3], [30.0, 31.0, 32.0])
    np.testing.assert_array_equal(exported[:, 4], [0.0, 0.0, 0.0])

    import scipy.io as sio

    loaded = sio.loadmat(tmp_path / "multi.detector.v2.mat")
    assert int(loaded["schema_version"].item()) == 2
    np.testing.assert_array_equal(loaded["wl_nm"].ravel(), [1510.0, 1515.0, 1520.0])
    np.testing.assert_array_equal(loaded["transfer_function_det_1_dB"].ravel(), [10.0, 11.0, 12.0])
    np.testing.assert_array_equal(loaded["transfer_function_det_2_dB"].ravel(), [0.0, 0.0, 0.0])
    np.testing.assert_array_equal(loaded["transfer_function_det_3_dB"].ravel(), [30.0, 31.0, 32.0])
    np.testing.assert_array_equal(loaded["transfer_function_det_4_dB"].ravel(), [0.0, 0.0, 0.0])
    assert int(loaded["active_detector_count"].item()) == 2
    np.testing.assert_array_equal(loaded["active_detector_ids"].ravel(), [3, 1])
    np.testing.assert_array_equal(loaded["active_detector_mask"].ravel(), [1, 0, 1, 0])
    assert loaded["completed_at_utc"].item() == "2026-09-29T12:34:56Z"
    assert int(loaded["res_pm"].item()) == 25
    assert loaded["speed_nms"].item() == "7"
    assert loaded["lp_set"].item() == "3"
    assert loaded["lp_unit"].item() == "dBm"
    assert loaded["laser_input"].item() == "LI_2"
    assert loaded["backend"].item() == "hardware.dummy_ct400.DummyCT400"
    assert bool(loaded["simulated"].item())
    assert loaded["result_kind"].item() == "SUCCESS"
    assert int(loaded["raw_result_code"].item()) == 0
    assert loaded["pout_dBm"].item() == -20.0
    assert "pow_dBm" not in loaded


def test_de5_export_warns_before_dialog_and_keeps_save_enabled(qtbot, monkeypatch, tmp_path):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_detector_measurement([1510.0, 1520.0], [[1.0, 2.0]], (Detector.DE_5,)))
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(
        plot_widgets.QInputDialog,
        "getMultiLineText",
        lambda *_args: pytest.fail("DE5 refusal must happen before the comment prompt"),
    )
    monkeypatch.setattr(
        plot_widgets.QFileDialog,
        "getSaveFileName",
        lambda *_args: pytest.fail("DE5 refusal must happen before opening the save dialog"),
    )
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: pytest.fail("FIG must not be queued"))

    widget.save_scan_data()

    assert warnings and "external/BNC" in warnings[0]
    assert "unit and SetBNC configuration" in warnings[0]
    assert list(tmp_path.iterdir()) == []
    assert widget.save_btn.isEnabled()


def test_empty_detector_export_is_rejected_without_opening_dialog(qtbot, monkeypatch):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_detector_measurement([1510.0, 1520.0], np.empty((0, 2)), ()))
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(
        plot_widgets.QInputDialog,
        "getMultiLineText",
        lambda *_args: pytest.fail("empty detector data must be rejected before the comment prompt"),
    )
    monkeypatch.setattr(
        plot_widgets.QFileDialog,
        "getSaveFileName",
        lambda *_args: pytest.fail("empty detector data must be rejected before opening the save dialog"),
    )

    widget.save_scan_data()

    assert warnings and "no wavelength-resolved detector data" in warnings[0]
    assert widget.save_btn.isEnabled()


def test_measurement_owns_read_only_array_copies():
    settings = ScanAcquisitionSettings(1, 2, 10, "1", "1", "mW", 1, LaserInput.LI_1, (Detector.DE_1,))
    wavelengths = np.array([1.0, 2.0])
    powers = np.array([[-3.0, -4.0]])
    measurement = ScanMeasurement(settings, wavelengths, powers, (Detector.DE_1,), None,
                                  CT400ScanResultKind.SUCCESS, 0, "", "test.Backend", False,
                                  datetime.now(UTC))
    wavelengths[0] = 99
    powers[0, 0] = 99
    np.testing.assert_array_equal(measurement.wavelengths_nm, [1, 2])
    np.testing.assert_array_equal(measurement.detector_data, [[-3, -4]])
    with pytest.raises(ValueError):
        measurement.detector_data[0, 0] = 0


def _stub_save_dialog(monkeypatch, path):
    monkeypatch.setattr(plot_widgets.QFileDialog, "getSaveFileName", lambda *_args: (str(path), "CSV File (*.csv)"))
    monkeypatch.setattr(plot_widgets.QInputDialog, "getMultiLineText", lambda *_args: ("", True))


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
    monkeypatch.setattr(
        plot_widgets.QInputDialog,
        "getMultiLineText",
        lambda *_args: pytest.fail("declined overwrite must happen before the comment prompt"),
    )
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

    assert "# Resolution(pm): 25" in csv_path.read_text(encoding="utf-8")
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


def test_comment_prompt_is_blank_each_time_and_exports_to_csv_and_mat(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    paths = iter((tmp_path / "first.csv", tmp_path / "second.csv"))
    monkeypatch.setattr(
        plot_widgets.QFileDialog,
        "getSaveFileName",
        lambda *_args: (str(next(paths)), "CSV File (*.csv)"),
    )
    comments = iter(("first note", 'Second, "annotated"\r\npass → stable #2'))
    prompts = []

    def prompt(*args):
        prompts.append(args)
        return next(comments), True

    monkeypatch.setattr(plot_widgets.QInputDialog, "getMultiLineText", prompt)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)

    widget.save_scan_data()
    widget.save_scan_data()

    assert len(prompts) == 2
    assert all(args[1:3] == ("Experiment Comment", "Optional comment for this scan export:") for args in prompts)
    assert all(args[3] == "" for args in prompts)
    assert "# Comment: \"first note\"" in (tmp_path / "first.csv").read_text(encoding="utf-8")

    import json
    import scipy.io as sio

    first_mat = sio.loadmat(tmp_path / "first.mat")["comment"]
    assert "".join(np.asarray(first_mat).astype(str).ravel().tolist()) == "first note"
    second_csv = (tmp_path / "second.csv").read_text(encoding="utf-8")
    encoded = next(line.removeprefix("# Comment: ") for line in second_csv.splitlines() if line.startswith("# Comment: "))
    normalized = 'Second, "annotated"\npass → stable #2'
    assert json.loads(encoded) == normalized
    loaded = sio.loadmat(tmp_path / "second.mat")["comment"]
    assert "".join(np.asarray(loaded).astype(str).ravel().tolist()) == normalized
    assert widget.save_btn.isEnabled()


def test_comment_cancel_preserves_existing_bundle_and_does_not_queue_fig(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    paths = derive_scan_export_targets(tmp_path / "cancel.csv", include_fig=True)
    initial = {}
    for path in paths.values():
        path.write_bytes((path.suffix + " original").encode())
        initial[path] = path.read_bytes()
    _stub_save_dialog(monkeypatch, tmp_path / "cancel.csv")
    monkeypatch.setattr(plot_widgets.QMessageBox, "question", lambda *_args: plot_widgets.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(plot_widgets.QInputDialog, "getMultiLineText", lambda *_args: ("discard me", False))
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: pytest.fail("cancelled comment must not queue FIG"))

    widget.save_scan_data()

    assert {path: path.read_bytes() for path in initial} == initial
    assert widget.save_btn.isEnabled()


def test_filename_cancel_does_not_prompt_for_comment(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    monkeypatch.setattr(plot_widgets.QFileDialog, "getSaveFileName", lambda *_args: ("", ""))
    monkeypatch.setattr(
        plot_widgets.QInputDialog,
        "getMultiLineText",
        lambda *_args: pytest.fail("filename cancellation must happen before the comment prompt"),
    )

    widget.save_scan_data()

    assert list(tmp_path.iterdir()) == []
    assert widget.save_btn.isEnabled()


def test_comment_acceptance_precedes_payload_build_and_builder_failure_writes_nothing(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    path = tmp_path / "builder-fails.csv"
    _stub_save_dialog(monkeypatch, path)
    events = []
    monkeypatch.setattr(plot_widgets.QInputDialog, "getMultiLineText", lambda *_args: events.append("comment") or ("note", True))
    monkeypatch.setattr(plot_widgets, "build_scan_export_v2", lambda *_args, **_kwargs: events.append("build") or (_ for _ in ()).throw(ValueError("bad payload")))
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *_args: None)
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: pytest.fail("payload failure must precede FIG queue"))
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)

    widget.save_scan_data()

    assert events == ["comment", "build"]
    assert not path.exists() and not path.with_suffix(".mat").exists() and not path.with_suffix(".fig").exists()
    assert widget.save_btn.isEnabled()
