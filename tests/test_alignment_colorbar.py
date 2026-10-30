"""Preserve the appearance/colormap contract of the extracted alignment colorbar."""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt

from ui.alignment_colorbar import ColorBarWidget


def test_alignment_colorbar_gradient_labels_and_visibility(qtbot):
    colorbar = ColorBarWidget()
    qtbot.addWidget(colorbar)
    colorbar.show()
    assert colorbar.maximumWidth() == 100
    assert colorbar.max_label.alignment() == Qt.AlignmentFlag.AlignCenter
    assert colorbar.min_label.alignment() == Qt.AlignmentFlag.AlignCenter
    assert isinstance(colorbar.gradient_widget, pg.GradientWidget)

    cmap = pg.colormap.get("viridis")
    colorbar.update_colormap(cmap, 0.1256, 12.34567)
    assert colorbar.max_label.text() == "12.346 mW"
    assert colorbar.min_label.text() == "0.126 mW"
    assert not colorbar.gradient_widget.isHidden()

    colorbar.update_colormap(None, 0.0, 0.0)
    assert colorbar.max_label.text() == "0.000 mW"
    assert colorbar.min_label.text() == "0.000 mW"
    assert colorbar.gradient_widget.isHidden()


def test_old_plot_widget_colorbar_import_remains_same_class():
    from ui.plot_widgets import ColorBarWidget as HistoricalColorBar

    assert HistoricalColorBar is ColorBarWidget
