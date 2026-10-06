from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtTest import QSignalSpy

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui import plot_widgets
from ui.alignment_panel import AlignmentPanel
from ui.control_panel import ScanSettings
from ui.plot_widgets import (
    HistogramWidget,
    Plot3DWidget,
    PlotWidget,
    PowerMonitorTraceWidget,
    derive_scan_export_targets,
)
from ui.typography import PLOT_TITLE_POINT_SIZE, install_application_fonts


def test_plot_titles_share_canonical_size_weight_and_family(qapp):
    install_application_fonts(qapp)
    scan = PlotWidget(ScanSettings())
    histogram = HistogramWidget(None, ["Det 1"])
    trace = PowerMonitorTraceWidget()
    power_map = Plot3DWidget()
    widgets = [scan, histogram, trace]
    for widget in widgets:
        title = widget.plot_widget.getPlotItem().titleLabel
        assert title.opts["size"] == f"{PLOT_TITLE_POINT_SIZE}pt"
        assert title.opts["bold"] is True
        assert title.opts["family"] == qapp.font().family()

    scan.set_plot_title("SIMULATED CT400 DATA", color="darkorange")
    simulated_style = scan.plot_widget.getPlotItem().titleLabel.opts
    assert simulated_style["size"] == f"{PLOT_TITLE_POINT_SIZE}pt"
    assert simulated_style["bold"] is True
    assert simulated_style["color"] == "darkorange"

    assert power_map.title_label.text() == "Power Map"
    assert power_map.title_label.font().family() == qapp.font().family()
    assert power_map.title_label.font().pointSize() == PLOT_TITLE_POINT_SIZE
    assert power_map.title_label.font().bold()
    assert power_map.title_label.alignment() & Qt.AlignmentFlag.AlignCenter

    for widget in [*widgets, power_map]:
        widget.close()


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


def test_power_monitor_histogram_uses_detector_border_fill_and_text_palette(qtbot):
    widget = HistogramWidget(None, ["Det 1", "Det 2", "Det 3", "Det 4"])
    qtbot.addWidget(widget)

    expected_border = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a"]
    expected_fill = ["#76c4ad", "#e89f67", "#aca9d1", "#f07eb8"]
    expected_max_text = ["#e40000"] * 4
    assert [pen.color().name() for pen in widget.bars.opts["pens"]] == expected_border
    assert [brush.color().name() for brush in widget.bars.opts["brushes"]] == expected_fill
    assert [item.color.name() for item in widget.max_texts] == expected_max_text
    assert [item.color.name() for item in widget.current_texts] == ["#555555"] * 4
    assert widget.max_pen.style().name == "DashLine"
    widget.close()


def test_power_monitor_histogram_colors_follow_detector_identity_when_labels_reordered(qtbot):
    widget = HistogramWidget(None, ["Det 4", "Det 2", "Det 1", "Det 3"])
    qtbot.addWidget(widget)
    assert [pen.color().name() for pen in widget.bars.opts["pens"]] == [
        "#e7298a",
        "#d95f02",
        "#1b9e77",
        "#7570b3",
    ]
    assert [item.color.name() for item in widget.max_texts] == ["#e40000"] * 4
    widget.close()


def _histogram_label_gaps_px(widget):
    pixel_height = abs(widget.plot_widget.getViewBox().viewPixelSize()[1])
    max_gap = (widget.max_texts[0].pos().y() - widget.max_values[0]) / pixel_height
    current_gap = (widget.current_values[0] - widget.current_texts[0].pos().y()) / pixel_height
    return max_gap, current_gap


def test_power_monitor_histogram_value_labels_use_pixel_gap_and_correct_direction(qtbot):
    widget = HistogramWidget(None, ["Det 1"])
    qtbot.addWidget(widget)
    widget.resize(640, 480)
    widget.show()
    qtbot.waitExposed(widget)

    widget._update_values(np.array([-20.0]))
    widget._update_y_axis_scale()
    widget._update_visual_elements()
    qtbot.wait(20)

    max_text = widget.max_texts[0]
    current_text = widget.current_texts[0]
    assert max_text.anchor == QPointF(0.5, 1.0)
    assert current_text.anchor == QPointF(0.5, 0.0)
    assert max_text.pos().y() == pytest.approx(widget.max_values[0])
    assert current_text.pos().y() == pytest.approx(widget.current_values[0])
    np.testing.assert_allclose(_histogram_label_gaps_px(widget), [widget._VALUE_LABEL_GAP_PX] * 2, atol=0.1)
    widget.close()


def test_power_monitor_histogram_label_pixel_gap_survives_resize_and_y_range_change(qtbot):
    widget = HistogramWidget(None, ["Det 1"])
    qtbot.addWidget(widget)
    widget.resize(640, 700)
    widget.show()
    qtbot.waitExposed(widget)
    widget._update_values(np.array([-20.0]))
    widget._update_y_axis_scale()
    widget._update_visual_elements()
    qtbot.wait(20)
    tall_gap = _histogram_label_gaps_px(widget)

    widget.resize(640, 320)
    qtbot.wait(50)
    short_gap = _histogram_label_gaps_px(widget)
    np.testing.assert_allclose(tall_gap, [widget._VALUE_LABEL_GAP_PX] * 2, atol=0.1)
    np.testing.assert_allclose(short_gap, [widget._VALUE_LABEL_GAP_PX] * 2, atol=0.1)
    assert widget.max_texts[0].pos().y() == pytest.approx(widget.max_values[0])
    assert widget.current_texts[0].pos().y() == pytest.approx(widget.current_values[0])
    assert widget.max_texts[0].anchor == QPointF(0.5, 1.0)
    assert widget.current_texts[0].anchor == QPointF(0.5, 0.0)

    widget.plot_widget.setYRange(-70, 10, padding=0)
    qtbot.wait(20)
    np.testing.assert_allclose(_histogram_label_gaps_px(widget), [widget._VALUE_LABEL_GAP_PX] * 2, atol=0.1)
    assert widget.max_texts[0].pos().y() == pytest.approx(widget.max_values[0])
    assert widget.current_texts[0].pos().y() == pytest.approx(widget.current_values[0])

    widget.reset_maxima()
    qtbot.wait(20)
    pixel_height = abs(widget.plot_widget.getViewBox().viewPixelSize()[1])
    reset_gap = (widget.current_values[0] - widget.current_texts[0].pos().y()) / pixel_height
    assert widget.current_texts[0].isVisible()
    assert widget.current_texts[0].pos().y() == pytest.approx(widget.current_values[0])
    assert reset_gap == pytest.approx(widget._VALUE_LABEL_GAP_PX, abs=0.1)
    widget.close()


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
        settings,
        np.asarray(wavelengths),
        np.asarray(detector_data),
        tuple(detectors),
        final_pout,
        CT400ScanResultKind.SUCCESS,
        0,
        "",
        "hardware.dummy_ct400.DummyCT400",
        True,
        completed_at_utc or datetime.now(UTC),
    )


def _plotted_data(item):
    x_data, y_data = item.getData()
    if x_data is None or y_data is None:
        return np.array([]), np.array([])
    return x_data, y_data


def _left_axis_label(widget):
    return widget.plot_widget.getAxis("left").labelText


def test_scan_y_axis_label_tracks_measurement_preview_and_clear_modes(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    measurement = _detector_measurement([1550.0, 1551.0], [[-18.0, -19.0]], (Detector.DE_1,))

    assert _left_axis_label(widget) == "Power (dB)"

    widget.set_measurement(measurement)
    assert _left_axis_label(widget) == "Transfer function (dB)"

    assert widget.update_plot(np.array([1550.0, 1551.0]), np.array([-18.0, -19.0]))
    assert _left_axis_label(widget) == "Power (dB)"

    widget.set_measurement(measurement)
    assert _left_axis_label(widget) == "Transfer function (dB)"

    widget.clear_plot()
    assert _left_axis_label(widget) == "Power (dB)"


def test_invalid_generic_preview_resets_measurement_axis_semantics(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_detector_measurement([1550.0, 1551.0], [[-18.0, -19.0]], (Detector.DE_1,)))
    assert _left_axis_label(widget) == "Transfer function (dB)"

    result = widget.update_plot(np.array([1550.0, 1551.0]), np.array([-18.0]))

    assert not result
    assert widget.current_measurement is None
    assert _left_axis_label(widget) == "Power (dB)"


def _move_plot_cursor(widget, x, y):
    widget.show()
    QCoreApplication.processEvents()
    view_box = widget.plot_widget.plotItem.vb
    (x_min, x_max), (y_min, y_max) = view_box.viewRange()
    x = min(max(x, x_min), x_max)
    y = min(max(y, y_min), y_max)
    widget._on_mouse_moved(view_box.mapViewToScene(QPointF(x, y)))


def test_completed_multidetector_crosshair_reports_identity_in_acquisition_order(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    measurement = _detector_measurement(
        [1550.0, 1550.237, 1550.5],
        [[-31.07, -32.0, -33.0], [-18.42, -19.0, -20.0]],
        (Detector.DE_3, Detector.DE_1),
    )
    widget.set_measurement(measurement)

    _move_plot_cursor(widget, 1550.24, -25.0)

    assert widget.v_line.isVisible()
    assert widget.v_line.value() == pytest.approx(1550.237)
    assert not widget.h_line.isVisible()
    label = widget.cursor_label.toPlainText()
    assert label.splitlines() == ["λ: 1550.237 nm", "Det 3: -32.00 dB", "Det 1: -19.00 dB"]


def test_frozen_detector_cursor_uses_independent_nearest_wavelength_and_detector_order(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference = _detector_measurement(
        [1550.000, 1550.235, 1550.490],
        [[-10.0, -11.0, -12.0], [-20.0, -21.0, -22.0], [-30.0, -31.0, -32.0]],
        (Detector.DE_2, Detector.DE_4, Detector.DE_1),
    )
    live = _detector_measurement(
        [1550.000, 1550.237, 1550.500],
        [[-40.0, -41.0, -42.0], [-50.0, -51.0, -52.0]],
        (Detector.DE_3, Detector.DE_1),
    )
    widget.set_measurement(reference)
    widget.freeze_current_trace()
    assert widget.reference_measurement is reference
    widget.set_measurement(live)
    assert widget.current_measurement is live
    assert widget.reference_measurement is reference

    _move_plot_cursor(widget, 1550.237, -25.0)

    assert widget.v_line.value() == pytest.approx(1550.237)
    assert not widget.h_line.isVisible()
    assert widget.cursor_label.toPlainText().splitlines() == [
        "\u03bb: 1550.237 nm",
        "Live:",
        "Det 3: -41.00 dB",
        "Det 1: -51.00 dB",
        "Reference @ 1550.235 nm:",
        "Det 2: -11.00 dB",
        "Det 4: -21.00 dB",
        "Det 1: -31.00 dB",
    ]


@pytest.mark.parametrize("live_wavelength", [1549.5, 1551.0])
def test_frozen_cursor_reports_out_of_range_without_clamping_to_endpoint(qtbot, live_wavelength):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference = _detector_measurement(
        [-float("inf"), 1550.0, 1550.2, 1550.4, float("nan"), float("inf")],
        [[-100.0, -10.0, -11.0, -12.0, -200.0, -300.0]],
        (Detector.DE_3,),
    )
    live = _detector_measurement([live_wavelength], [[-18.42]], (Detector.DE_1,))
    widget.set_measurement(reference)
    widget.freeze_current_trace()
    widget.set_measurement(live)

    _move_plot_cursor(widget, live_wavelength, -10.0)

    label_lines = widget.cursor_label.toPlainText().splitlines()
    assert label_lines[0] == f"\u03bb: {live_wavelength:.3f} nm"
    assert label_lines[1:] == ["Live:", "Det 1: -18.42 dB", "Reference: out of range"]
    assert "Det 3:" not in widget.cursor_label.toPlainText()


@pytest.mark.parametrize("boundary", [1550.0, 1550.4])
def test_frozen_cursor_treats_reference_range_boundaries_as_in_range(qtbot, boundary):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference = _detector_measurement(
        [1550.0, 1550.2, 1550.4],
        [[-10.0, -11.0, -12.0]],
        (Detector.DE_3,),
    )
    live = _detector_measurement([boundary], [[-18.42]], (Detector.DE_1,))
    widget.set_measurement(reference)
    widget.freeze_current_trace()
    widget.set_measurement(live)

    _move_plot_cursor(widget, boundary, -10.0)

    assert widget.cursor_label.toPlainText().splitlines()[-2:] == [
        f"Reference @ {boundary:.3f} nm:",
        "Det 3: -10.00 dB" if boundary == 1550.0 else "Det 3: -12.00 dB",
    ]


def test_frozen_cursor_preserves_live_single_detector_horizontal_line(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference = _detector_measurement([1550.0], [[-35.0]], (Detector.DE_3,))
    live = _detector_measurement([1550.0], [[-18.42]], (Detector.DE_1,))
    widget.set_measurement(reference)
    widget.freeze_current_trace()
    widget.set_measurement(live)

    _move_plot_cursor(widget, 1550.0, -10.0)

    assert widget.h_line.isVisible()
    assert widget.h_line.value() == pytest.approx(-18.42)
    assert widget.cursor_label.toPlainText().splitlines()[-2:] == [
        "Reference @ 1550.000 nm:",
        "Det 3: -35.00 dB",
    ]


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf")])
def test_frozen_cursor_marks_nonfinite_reference_detector_value_n_a(qtbot, bad_value):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference = _detector_measurement([1550.0], [[bad_value]], (Detector.DE_3,))
    widget.set_measurement(reference)
    widget.freeze_current_trace()
    widget.set_measurement(_detector_measurement([1550.0], [[-18.42]], (Detector.DE_1,)))

    _move_plot_cursor(widget, 1550.0, -10.0)

    assert widget.h_line.isVisible()
    assert widget.h_line.value() == pytest.approx(-18.42)
    assert widget.cursor_label.toPlainText().splitlines()[-1] == "Det 3: n/a"


def test_frozen_cursor_reports_unavailable_when_reference_has_no_finite_wavelength(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    reference = _detector_measurement([float("nan"), float("inf")], [[-30.0, -31.0]], (Detector.DE_3,))
    widget.set_measurement(reference)
    widget.freeze_current_trace()
    widget.set_measurement(_detector_measurement([1550.0], [[-18.42]], (Detector.DE_1,)))

    _move_plot_cursor(widget, 1550.0, -10.0)

    assert "Det 1: -18.42 dB" in widget.cursor_label.toPlainText()
    assert widget.cursor_label.toPlainText().splitlines()[-1] == "Reference: n/a"


def test_completed_single_detector_crosshair_snaps_horizontal_line_and_uses_db(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_detector_measurement([1550.0, 1550.5], [[-18.42, -20.0]], (Detector.DE_1,)))

    _move_plot_cursor(widget, 1550.1, -10.0)

    assert widget.v_line.value() == pytest.approx(1550.0)
    assert widget.h_line.isVisible()
    assert widget.h_line.value() == pytest.approx(-18.42)
    assert widget.cursor_label.toPlainText() == "λ: 1550.000 nm\nDet 1: -18.42 dB"


def test_generic_preview_crosshair_keeps_single_trace_readout(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.update_plot(np.array([1550.0, 1550.5]), np.array([-18.42, -20.0]))

    _move_plot_cursor(widget, 1550.1, -10.0)

    assert widget.v_line.value() == pytest.approx(1550.0)
    assert widget.h_line.isVisible()
    assert widget.h_line.value() == pytest.approx(-18.42)
    assert widget.cursor_label.toPlainText() == "λ: 1550.000 nm\nP: -18.42 dBm"


def test_generic_preview_freeze_keeps_generic_cursor_and_has_no_measurement_reference(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    wavelengths = np.array([1550.0, 1550.5])
    powers = np.array([-18.42, -20.0])
    widget.update_plot(wavelengths, powers)
    widget.freeze_current_trace()

    assert widget.reference_measurement is None
    np.testing.assert_array_equal(widget.reference_plot_item.getData()[0], wavelengths)
    np.testing.assert_array_equal(widget.reference_plot_item.getData()[1], powers)
    _move_plot_cursor(widget, 1550.1, -10.0)
    assert widget.cursor_label.toPlainText().splitlines() == ["\u03bb: 1550.000 nm", "P: -18.42 dBm"]


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -float("inf")])
def test_multidetector_crosshair_marks_nonfinite_detector_n_a(qtbot, bad_value):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_detector_measurement([1550.0], [[-18.42], [bad_value]], (Detector.DE_1, Detector.DE_3)))

    _move_plot_cursor(widget, 1550.0, -10.0)

    assert not widget.h_line.isVisible()
    assert widget.cursor_label.toPlainText().splitlines() == ["λ: 1550.000 nm", "Det 1: -18.42 dB", "Det 3: n/a"]


def test_crosshair_switches_between_measurement_and_generic_preview(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(
        _detector_measurement([1550.0, 1550.5], [[-18.0, -19.0], [-30.0, -31.0]], (Detector.DE_1, Detector.DE_3))
    )
    _move_plot_cursor(widget, 1550.0, -10.0)
    assert "Det 3:" in widget.cursor_label.toPlainText()

    widget.update_plot(np.array([1549.0, 1551.0]), np.array([-5.0, -6.0]))
    _move_plot_cursor(widget, 1550.8, -1.0)
    assert widget.cursor_label.toPlainText() == "λ: 1551.000 nm\nP: -6.00 dBm"
    assert widget.h_line.value() == pytest.approx(-6.0)

    widget.set_measurement(
        _detector_measurement([1550.0, 1550.5], [[-12.0, -13.0], [-22.0, -23.0]], (Detector.DE_1, Detector.DE_3))
    )
    _move_plot_cursor(widget, 1550.0, -10.0)
    assert "Det 3:" in widget.cursor_label.toPlainText()


def test_clear_plot_hides_crosshair_and_empty_or_nonfinite_wavelengths_are_safe(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.set_measurement(_detector_measurement([1550.0], [[-18.0]], (Detector.DE_1,)))
    _move_plot_cursor(widget, 1550.0, -10.0)
    widget.clear_plot()
    assert not widget.v_line.isVisible()
    assert not widget.h_line.isVisible()
    assert not widget.cursor_label.isVisible()

    widget.set_measurement(_detector_measurement([], np.empty((1, 0)), (Detector.DE_1,)))
    _move_plot_cursor(widget, 1550.0, -10.0)
    assert not widget.v_line.isVisible()
    assert not widget.h_line.isVisible()
    assert not widget.cursor_label.isVisible()

    widget.set_measurement(_detector_measurement([float("nan")], [[-18.0]], (Detector.DE_1,)))
    _move_plot_cursor(widget, 1550.0, -10.0)
    assert not widget.v_line.isVisible()
    assert not widget.h_line.isVisible()
    assert not widget.cursor_label.isVisible()


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

    assert {
        detector: item.opts["pen"].color().name() for detector, item in widget.detector_plot_items.items()
    } == styles
    assert [label.text for _sample, label in widget.detector_legend.items] == ["Det 1", "Det 3"]
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_1])[1], [1, 2])
    np.testing.assert_array_equal(_plotted_data(widget.detector_plot_items[Detector.DE_3])[1], [3, 4])


def test_matlab_fig_payload_preserves_detector_identity_order_and_colors():
    import json

    measurement = _detector_measurement([1510.0, 1520.0], [[30.0, 31.0], [10.0, 11.0]], (Detector.DE_3, Detector.DE_1))

    payload = json.loads(plot_widgets.build_matlab_fig_payload(measurement))

    assert payload["wavelengths_nm"] == [1510.0, 1520.0]
    assert payload["traces"] == [
        {"detector_id": 3, "label": "Det 3", "values": [30.0, 31.0], "color": "#33a02c"},
        {"detector_id": 1, "label": "Det 1", "values": [10.0, 11.0], "color": "#1f78b4"},
    ]


def test_matlab_fig_payload_omits_inactive_detectors_and_keeps_nonfinite_values():
    import json

    measurement = _detector_measurement([1510.0, 1520.0], [[float("nan"), float("inf")]], (Detector.DE_1,))

    payload = json.loads(plot_widgets.build_matlab_fig_payload(measurement))

    assert len(payload["traces"]) == 1
    assert payload["traces"][0]["detector_id"] == 1
    assert np.isnan(payload["traces"][0]["values"][0])
    assert payload["traces"][0]["values"][1] == float("inf")


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
    measurement = _detector_measurement(wavelengths, powers, (Detector.DE_1, Detector.DE_2, Detector.DE_3))

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
    assert widget.reference_measurement is first
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_1])[1], [1, 2])
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_3])[1], [3, 4])
    assert (
        widget.reference_detector_plot_items[Detector.DE_1].opts["pen"].color().name()
        == widget.detector_plot_items[Detector.DE_1].opts["pen"].color().name()
    )
    assert (
        widget.reference_detector_plot_items[Detector.DE_1].opts["pen"].widthF()
        < widget.detector_plot_items[Detector.DE_1].opts["pen"].widthF()
    )

    second = _detector_measurement(wavelengths, [[11, 12], [13, 14]], (Detector.DE_1, Detector.DE_3))
    widget.set_measurement(second)
    assert widget.current_measurement is second
    assert widget.reference_measurement is first
    np.testing.assert_array_equal(_plotted_data(widget.reference_detector_plot_items[Detector.DE_1])[1], [1, 2])
    widget.freeze_current_trace()
    assert widget.reference_measurement is second

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
    assert widget.reference_measurement is None
    assert not widget.save_btn.isEnabled()
    assert not widget.freeze_btn.isEnabled()


@pytest.mark.parametrize("selected", ["foo", "foo.csv", "foo.MAT", "foo.Fig"])
@pytest.mark.parametrize(
    ("formats", "extensions"),
    [
        ((True, False, False), [".csv"]),
        ((False, True, False), [".mat"]),
        ((False, False, True), [".fig"]),
        ((True, True, False), [".csv", ".mat"]),
        ((True, False, True), [".csv", ".fig"]),
        ((False, True, True), [".mat", ".fig"]),
        ((True, True, True), [".csv", ".mat", ".fig"]),
    ],
)
def test_derive_scan_export_targets_selects_only_requested_formats(selected, formats, extensions):
    targets = derive_scan_export_targets(
        selected, include_csv=formats[0], include_mat=formats[1], include_fig=formats[2]
    )
    expected_stem = "foo"
    assert [path.suffix for path in targets.values()] == extensions
    assert {path.stem for path in targets.values()} == {expected_stem}


def test_derive_scan_export_targets_does_not_strip_unrelated_extensions():
    targets = derive_scan_export_targets("foo.txt", include_csv=True, include_mat=False, include_fig=False)
    assert targets == {"CSV": Path("foo.txt.csv")}


def _prepare_export_widget(qtbot, settings=None):
    widget = PlotWidget(ScanSettings(), settings=settings)
    qtbot.addWidget(widget)
    settings = ScanAcquisitionSettings(
        1510.0, 1520.0, 25, "7", "3", "dBm", 1.9952623149688795, LaserInput.LI_2, (Detector.DE_1,)
    )
    measurement = ScanMeasurement(
        settings,
        np.array([1510.0, 1520.0]),
        np.array([[-20.0, -21.0]]),
        (Detector.DE_1,),
        None,
        CT400ScanResultKind.SUCCESS,
        0,
        "",
        "hardware.dummy_ct400.DummyCT400",
        True,
        datetime.now(UTC),
    )
    widget.set_measurement(measurement)
    return widget


def _stub_save_dialog(
    monkeypatch, directory, base_name="scan", *, formats=(True, True, False), accepted=True, comment=""
):
    from PySide6.QtWidgets import QDialog

    from ui.scan_export_dialog import ScanExportRequest

    class Dialog:
        DialogCode = QDialog.DialogCode

        def __init__(self, *_args):
            self.export_request = (
                ScanExportRequest(
                    Path(directory), base_name, formats[0], formats[1], formats[2] and _args[3], formats[2], comment
                )
                if accepted
                else None
            )

        def exec(self):
            return self.DialogCode.Accepted if accepted else self.DialogCode.Rejected

    monkeypatch.setattr(plot_widgets, "ScanExportDialog", Dialog)


def _silence_export_messages(monkeypatch):
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *_args: None)


def test_csv_mat_formats_never_start_matlab_and_write_both(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    _stub_save_dialog(monkeypatch, tmp_path, formats=(True, True, False))
    _silence_export_messages(monkeypatch)
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: pytest.fail("CSV+MAT must not start MATLAB"))
    widget.save_scan_data()
    assert (tmp_path / "scan.csv").exists() and (tmp_path / "scan.mat").exists()
    assert not (tmp_path / "scan.fig").exists()


@pytest.mark.parametrize(
    ("formats", "expected"), [((True, False, False), {"scan.csv"}), ((False, True, False), {"scan.mat"})]
)
def test_csv_or_mat_only_writes_selected_file(qtbot, monkeypatch, tmp_path, formats, expected):
    widget = _prepare_export_widget(qtbot)
    _stub_save_dialog(monkeypatch, tmp_path, formats=formats)
    _silence_export_messages(monkeypatch)
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        widget, "_ensure_matlab_engine_started", lambda: pytest.fail("non-FIG save must not start MATLAB")
    )
    widget.save_scan_data()
    assert {path.name for path in tmp_path.iterdir()} == expected
    assert widget.save_btn.isEnabled()


def test_fig_only_startup_failure_saves_nothing_and_restores_button(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    _stub_save_dialog(monkeypatch, tmp_path, formats=(False, False, True))
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    calls = []
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: calls.append(True) or False)
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    widget.save_scan_data()
    assert calls == [True]
    assert not list(tmp_path.iterdir())
    assert warnings and "FIG:" in warnings[0]
    assert widget.save_btn.isEnabled()


def test_all_formats_writes_csv_mat_and_queues_selected_fig(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    _stub_save_dialog(monkeypatch, tmp_path, formats=(True, True, True))
    _silence_export_messages(monkeypatch)
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: True)
    invoked = {}

    class Signal:
        def connect(self, _slot):
            pass

    class Thread:
        started = Signal()
        finished = Signal()

        def __init__(self, *_args):
            pass

        def start(self):
            pass

        def isRunning(self):
            return False

        def deleteLater(self):
            pass

    class Worker:
        finished_saving = Signal()

        def moveToThread(self, _thread):
            pass

        def deleteLater(self):
            pass

    monkeypatch.setattr(plot_widgets, "QThread", Thread)
    monkeypatch.setattr(plot_widgets, "MatlabSaveWorker", Worker)
    monkeypatch.setattr(plot_widgets, "Q_ARG", lambda _type, value: value)
    monkeypatch.setattr(
        plot_widgets.QMetaObject, "invokeMethod", lambda _worker, _method, _connection, *args: invoked.update(args=args)
    )
    widget.save_scan_data()
    assert {path.name for path in tmp_path.iterdir()} == {"scan.csv", "scan.mat"}
    assert str(tmp_path / "scan.fig") in [arg for arg in invoked["args"] if isinstance(arg, str)]
    assert widget.pending_saves == 1


def test_overwrite_decline_writes_nothing_and_lists_only_selected_conflict(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    conflict = tmp_path / "scan.mat"
    conflict.write_bytes(b"original")
    _stub_save_dialog(monkeypatch, tmp_path, formats=(True, True, False))
    questions = []
    monkeypatch.setattr(
        plot_widgets.QMessageBox,
        "question",
        lambda *args: questions.append(args[2]) or plot_widgets.QMessageBox.StandardButton.No,
    )
    widget.save_scan_data()
    assert conflict.read_bytes() == b"original"
    assert not (tmp_path / "scan.csv").exists()
    assert str(conflict) in questions[0] and "scan.fig" not in questions[0]
    assert widget.save_btn.isEnabled()


def test_comment_and_settings_are_recorded_for_accepted_request(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    from app_settings import AppSettings

    settings = AppSettings(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    output = tmp_path / "out"
    output.mkdir()
    widget = _prepare_export_widget(qtbot, settings)
    _stub_save_dialog(monkeypatch, output, "event.csv", formats=(True, True, False), comment="first\r\nsecond")
    _silence_export_messages(monkeypatch)
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: pytest.fail("CSV must not start MATLAB"))
    widget.save_scan_data()
    assert settings.scan_export_directory() == output.resolve()
    assert settings.scan_export_formats() == (True, True, False)
    assert r'# Comment: "first\nsecond"' in (output / "event.csv").read_text(encoding="utf-8")
    import scipy.io as sio

    mat_comment = sio.loadmat(output / "event.mat")["comment"]
    assert "".join(np.asarray(mat_comment).astype(str).ravel().tolist()) == "first\nsecond"


def test_dialog_cancel_does_not_write_or_persist(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    from app_settings import AppSettings

    settings = AppSettings(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    widget = _prepare_export_widget(qtbot, settings)
    _stub_save_dialog(monkeypatch, tmp_path, accepted=False)
    widget.save_scan_data()
    assert not list(tmp_path.glob("*.csv"))
    assert settings.scan_export_formats() == (True, True, False)
    assert settings._settings.value("Paths/scan_export") is None
    assert widget.save_btn.isEnabled()


def test_multidetector_csv_mat_preserve_schema_v2_identity(qtbot, monkeypatch, tmp_path):
    import scipy.io as sio

    widget = _prepare_export_widget(qtbot)
    measurement = _detector_measurement(
        [1510.0, 1515.0, 1520.0],
        [[30.0, 31.0, 32.0], [10.0, 11.0, 12.0]],
        (Detector.DE_3, Detector.DE_1),
        final_pout=-20.0,
        completed_at_utc=datetime(2026, 9, 29, 12, 34, 56, tzinfo=UTC),
    )
    widget.set_measurement(measurement)
    _stub_save_dialog(monkeypatch, tmp_path, "multi.detector.v2", formats=(True, True, False))
    _silence_export_messages(monkeypatch)
    monkeypatch.setattr(widget, "_ensure_matlab_engine_started", lambda: pytest.fail("CSV+MAT must not start MATLAB"))

    widget.save_scan_data()

    csv_text = (tmp_path / "multi.detector.v2.csv").read_text(encoding="utf-8")
    assert "# SchemaVersion: 2" in csv_text
    assert "# ActiveDetectors: DE_3,DE_1" in csv_text
    assert "# OpticalDetectorMask(DE1-DE4): 1,0,1,0" in csv_text
    data = sio.loadmat(tmp_path / "multi.detector.v2.mat")
    assert int(data["schema_version"].item()) == 2
    np.testing.assert_array_equal(data["active_detector_ids"].ravel(), [3, 1])
    np.testing.assert_array_equal(data["active_detector_mask"].ravel(), [1, 0, 1, 0])
    np.testing.assert_array_equal(data["transfer_function_det_1_dB"].ravel(), [10, 11, 12])
    np.testing.assert_array_equal(data["transfer_function_det_3_dB"].ravel(), [30, 31, 32])
    assert data["completed_at_utc"].item() == "2026-09-29T12:34:56Z"
    assert data["pout_dBm"].item() == -20.0


def test_partial_export_failure_keeps_successful_format(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    _stub_save_dialog(monkeypatch, tmp_path, formats=(True, True, False))
    monkeypatch.setattr(
        plot_widgets.np, "savetxt", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
    )
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(
        plot_widgets.QMessageBox, "information", lambda *_args: pytest.fail("partial save is not full success")
    )

    widget.save_scan_data()

    assert not (tmp_path / "scan.csv").exists()
    assert (tmp_path / "scan.mat").exists()
    assert warnings and "CSV: disk full" in warnings[0]
    assert widget.save_btn.isEnabled()


def test_payload_validation_error_writes_nothing_and_restores_button(qtbot, monkeypatch, tmp_path):
    widget = _prepare_export_widget(qtbot)
    _stub_save_dialog(monkeypatch, tmp_path, formats=(True, False, False))
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(
        plot_widgets, "build_scan_export_v2", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload"))
    )

    widget.save_scan_data()

    assert not list(tmp_path.iterdir())
    assert warnings and "bad payload" in warnings[0]
    assert widget.save_btn.isEnabled()


def test_unavailable_matlab_preserves_saved_fig_preference(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings

    from app_settings import AppSettings

    settings = AppSettings(QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat))
    settings.set_scan_export_formats(csv=True, mat=True, fig=True)
    output = tmp_path / "out"
    output.mkdir()
    widget = _prepare_export_widget(qtbot, settings)
    _stub_save_dialog(monkeypatch, output, formats=(True, True, True))
    _silence_export_messages(monkeypatch)
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", False)
    monkeypatch.setattr(
        widget, "_ensure_matlab_engine_started", lambda: pytest.fail("unavailable FIG cannot start MATLAB")
    )

    widget.save_scan_data()

    assert {path.name for path in output.iterdir()} == {"scan.csv", "scan.mat"}
    assert settings.scan_export_formats() == (True, True, True)
