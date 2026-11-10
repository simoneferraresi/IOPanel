"""Hardware-free checks for the camera-first, non-scrolling vertical layout."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from hardware.pm5020_types import PowerChannel
from ui.active_page_tabs import ActivePageTabWidget
from ui.plot_widgets import HistogramWidget, PowerMonitorTraceWidget
from ui.pm5020_panel import PM5020ChannelControls


def _page(control_height: int) -> tuple[QWidget, QWidget]:
    page = QWidget()
    layout = QHBoxLayout(page)
    controls = QWidget()
    controls.setMinimumSize(180, control_height)
    plot = QWidget()
    plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
    layout.addWidget(controls)
    layout.addWidget(plot, stretch=1)
    return page, controls


def test_tabs_use_visible_page_minimum_not_tallest_previously_visited(qtbot):
    tabs = ActivePageTabWidget()
    qtbot.addWidget(tabs)
    small, _ = _page(180)
    large, _ = _page(600)
    tabs.addTab(small, "Power Monitor")
    tabs.addTab(large, "Auto Alignment")

    small_height = tabs.minimumSizeHint().height()
    tabs.setCurrentIndex(1)
    assert tabs.minimumSizeHint().height() >= small_height + 350
    tabs.setCurrentIndex(0)
    assert tabs.minimumSizeHint().height() == small_height


def test_splitter_shrinks_to_controls_without_hiding_them(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)
    camera = QWidget()
    camera.setMinimumHeight(80)
    tabs = ActivePageTabWidget()
    small, controls = _page(180)
    large, _ = _page(600)
    tabs.addTab(small, "Power Monitor")
    tabs.addTab(large, "Large controls")
    splitter.addWidget(camera)
    splitter.addWidget(tabs)
    layout.addWidget(splitter)
    host.resize(900, 800)
    host.show()
    QApplication.processEvents()

    tabs.setCurrentIndex(1)
    QApplication.processEvents()
    tabs.setCurrentIndex(0)
    QApplication.processEvents()
    splitter.setSizes([650, 150])
    QApplication.processEvents()

    assert splitter.sizes()[1] < 400
    assert controls.height() >= controls.minimumHeight()
    assert not host.findChildren(QScrollArea)


def test_power_monitor_plot_surfaces_do_not_claim_vertical_minimum(qtbot):
    histogram = HistogramWidget(None, ["Det 1"])
    trace = PowerMonitorTraceWidget()
    qtbot.addWidget(histogram)
    qtbot.addWidget(trace)
    for widget in (histogram, histogram.plot_widget, trace, trace.plot_widget):
        assert widget.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Ignored
    assert not histogram.findChildren(QScrollArea)
    assert not trace.findChildren(QScrollArea)


def test_pm5020_plots_can_shrink_below_previous_155_pixel_floor(qtbot):
    channel = PM5020ChannelControls(PowerChannel.CH1, default_wavelength_nm=1550.0, default_unit="mW")
    qtbot.addWidget(channel)
    assert channel.plot.minimumHeight() == 0
    assert channel.plot.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Ignored
    assert not channel.findChildren(QScrollArea)
