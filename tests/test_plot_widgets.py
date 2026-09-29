import numpy as np

from ui.control_panel import ScanSettings
from ui.plot_widgets import PlotWidget


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
