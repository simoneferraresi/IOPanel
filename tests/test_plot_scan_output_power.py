"""Native Qt regression tests for the optional CT400 laser output-power overlay."""

import io
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QPointF

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import build_scan_export_v2
from logic.scan_import import load_scan
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from logic.scan_settings import ScanSettings
from ui.plot_widgets import PlotWidget


def _scan(*, values=(0.5, 0.8, 0.6), detector_values=(-20.0, -40.0, -30.0)):
    detectors = (Detector.DE_1,)
    settings = ScanAcquisitionSettings(1550.0, 1550.2, 100, "5", "1", "mW", 1.0, LaserInput.LI_1, detectors)
    return ScanMeasurement(
        settings=settings,
        wavelengths_nm=np.array([1550.0, 1550.1, 1550.2]),
        detector_data=np.array([detector_values]),
        detectors=detectors,
        final_pout=-1.0,
        result_kind=CT400ScanResultKind.SUCCESS,
        raw_result_code=0,
        result_message="",
        backend="hardware.dummy_ct400.DummyCT400",
        simulated=True,
        completed_at_utc=datetime(2026, 10, 29, tzinfo=UTC),
        laser_output_power_dbm=None if values is None else np.asarray(values),
    )


def _cursor(widget, wavelength, detector_db=-30.0):
    widget.show()
    QCoreApplication.processEvents()
    main_view = widget.plot_widget.getViewBox()
    point = main_view.mapViewToScene(QPointF(wavelength, detector_db))
    widget._on_mouse_moved(point)
    return widget.cursor_label.toPlainText().splitlines()


def test_power_toggle_is_disabled_without_data_and_hidden_by_default(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    assert widget.output_power_btn.objectName() == "scanPlotOutputPowerButton"
    assert not widget.output_power_btn.isChecked()
    assert not widget.output_power_btn.isEnabled()
    assert not widget.output_power_axis.isVisible()
    assert not widget.output_power_viewbox.isVisible()

    scan = _scan()
    widget.set_measurement(scan)
    assert widget.output_power_btn.isEnabled()
    assert not widget.output_power_btn.isChecked()
    assert not widget.output_power_axis.isVisible()
    assert not widget.output_power_viewbox.isVisible()
    assert widget.output_power_axis.labelUnits == "dBm"
    assert widget.output_power_axis.labelText == "Laser output power"
    np.testing.assert_array_equal(scan.laser_output_power_dbm, [0.5, 0.8, 0.6])


def test_toggle_uses_independent_right_y_axis_and_linked_x_without_changing_detector_range(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_scan())
    widget.show()
    QCoreApplication.processEvents()

    left_view = widget.plot_widget.getViewBox()
    before_y = left_view.viewRange()[1][:]
    detector_values = widget.detector_plot_items[Detector.DE_1].yData.copy()
    scene_item_count = len(widget.plot_widget.scene().items())

    widget.output_power_btn.click()

    assert widget.output_power_btn.isChecked()
    assert widget.output_power_axis.isVisible()
    assert widget.output_power_viewbox.isVisible()
    np.testing.assert_array_equal(widget.output_power_curve.xData, [1550.0, 1550.1, 1550.2])
    np.testing.assert_array_equal(widget.output_power_curve.yData, [0.5, 0.8, 0.6])
    assert left_view.viewRange()[1] == pytest.approx(before_y)
    assert widget.output_power_viewbox.viewRange()[1][0] <= 0.5
    assert widget.output_power_viewbox.viewRange()[1][1] >= 0.8
    assert widget.plot_widget.getAxis("left").labelText == "Transfer function (dB)"
    np.testing.assert_array_equal(widget.detector_plot_items[Detector.DE_1].yData, detector_values)

    left_view.setXRange(1550.05, 1550.15, padding=0)
    QCoreApplication.processEvents()
    assert widget.output_power_viewbox.viewRange()[0] == pytest.approx(left_view.viewRange()[0])
    assert widget.output_power_viewbox.geometry() == left_view.sceneBoundingRect()

    for _ in range(3):
        widget.output_power_btn.click()
        assert not widget.output_power_axis.isVisible()
        assert not widget.output_power_viewbox.isVisible()
        assert widget.output_power_curve.xData is None or len(widget.output_power_curve.xData) == 0
        widget.output_power_btn.click()
        assert widget.output_power_axis.isVisible()
    assert len(widget.plot_widget.scene().items()) == scene_item_count


def test_enabled_output_power_is_reported_in_crosshair_only_for_current_scan(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    first = _scan(values=(1.2, 1.3, 1.4))
    widget.set_measurement(first)
    widget.freeze_current_trace()
    current = _scan(values=(0.1, -1.25, 0.2))
    widget.set_measurement(current)

    assert not any("Laser output" in line for line in _cursor(widget, 1550.1))

    widget.output_power_btn.click()
    lines = _cursor(widget, 1550.1)
    assert "Det 1: -40.00 dB" in lines
    assert "Laser output: -1.25 dBm" in lines
    assert sum(line.startswith("Laser output:") for line in lines) == 1
    assert widget.reference_measurement is first
    assert "Reference @ 1550.100 nm:" in lines

    widget.output_power_btn.click()
    assert not any("Laser output" in line for line in _cursor(widget, 1550.1))


def test_imported_scan_uses_same_toggle_and_legacy_scan_disables_it(qtbot, tmp_path):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)

    def saved_scan(name, measurement):
        payload = build_scan_export_v2(measurement)
        path = tmp_path / name
        stream = io.StringIO()
        np.savetxt(stream, payload.csv_data, delimiter=",", header=payload.csv_header, comments="", fmt="%.6f")
        path.write_text(stream.getvalue(), encoding="utf-8")
        return load_scan(path)

    imported = saved_scan("powered.csv", _scan(values=(0.1, 0.2, 0.3)))
    assert widget.set_imported_scan(imported)
    assert widget.output_power_btn.isEnabled()
    assert not widget.save_btn.isEnabled()
    widget.output_power_btn.click()
    np.testing.assert_array_equal(widget.output_power_curve.yData, [0.1, 0.2, 0.3])
    assert "Laser output: 0.20 dBm" in _cursor(widget, 1550.1)

    # A new scan without the optional column cannot display a stale trace.
    legacy = saved_scan("legacy.csv", _scan(values=None))
    widget.set_imported_scan(legacy)
    assert not widget.output_power_btn.isEnabled()
    assert not widget.output_power_btn.isChecked()
    assert not widget.output_power_axis.isVisible()
    assert not widget.output_power_viewbox.isVisible()
    assert not any("Laser output" in line for line in _cursor(widget, 1550.1))


def test_missing_or_nonfinite_output_power_is_safe_and_does_not_mutate_acquisition(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    data = _scan(values=(np.nan, 0.5, np.inf))
    widget.set_measurement(data)
    widget.output_power_btn.click()
    np.testing.assert_array_equal(widget.output_power_curve.xData, [1550.1])
    np.testing.assert_array_equal(widget.output_power_curve.yData, [0.5])
    assert "Laser output: n/a" in _cursor(widget, 1550.0)
    assert "Laser output: 0.50 dBm" in _cursor(widget, 1550.1)
    assert np.isnan(data.laser_output_power_dbm[0])
    assert np.isposinf(data.laser_output_power_dbm[2])

    widget.set_measurement(_scan(values=(np.nan, np.inf, -np.inf)))
    assert not widget.output_power_btn.isEnabled()
    assert not widget.output_power_axis.isVisible()
    assert not widget.output_power_btn.isChecked()

    widget.set_measurement(data)
    widget.output_power_btn.click()
    assert widget.output_power_axis.isVisible()
    assert widget.update_plot(np.array([1550.0, 1550.1]), np.array([-20.0, -21.0]))
    assert not widget.output_power_axis.isVisible()
    assert not widget.output_power_btn.isEnabled()
    assert not widget.output_power_btn.isChecked()

    widget.set_measurement(data)
    widget.output_power_btn.click()
    widget.clear_plot()
    assert not widget.output_power_axis.isVisible()
    assert not widget.output_power_btn.isEnabled()
    assert not widget.output_power_btn.isChecked()
    assert widget.output_power_curve.xData is None or not len(widget.output_power_curve.xData)


def test_toggle_preserves_user_visibility_across_powered_scans_and_leaves_frozen_data_untouched(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    first = _scan(values=(0.1, 0.2, 0.3))
    widget.set_measurement(first)
    widget.freeze_current_trace()
    widget.output_power_btn.click()
    second = _scan(values=(0.4, 0.5, 0.6))
    widget.set_measurement(second)

    assert widget.output_power_btn.isChecked()
    np.testing.assert_array_equal(widget.output_power_curve.yData, [0.4, 0.5, 0.6])
    assert widget.reference_measurement is first
    assert set(widget.reference_detector_plot_items) == {Detector.DE_1}
    assert widget.current_measurement is second
