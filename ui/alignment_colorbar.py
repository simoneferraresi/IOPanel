"""Alignment colorbar isolated from scan plots and MATLAB Engine imports.

The gradient keeps PyQtGraph's original interaction and rendering semantics.
Only widget construction (always on the Qt GUI thread) imports PyQtGraph;
importing AlignmentPanel itself does not transitively import scan plotting.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class ColorBarWidget(QWidget):
    """A custom widget to display a color bar with min/max labels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumWidth(100)  # Keep it narrow
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 10, 5, 10)

        self.max_label = QLabel("Max")
        self.max_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        import pyqtgraph as pg

        self.gradient_widget = pg.GradientWidget(orientation="right")

        self.min_label = QLabel("Min")
        self.min_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.max_label)
        layout.addWidget(self.gradient_widget)
        layout.addWidget(self.min_label)

    @Slot(object, float, float)
    def update_colormap(self, cmap, min_val, max_val):
        """Updates the gradient and labels."""
        if cmap:
            self.gradient_widget.setColorMap(cmap)
            self.gradient_widget.setVisible(True)
        else:
            # If no colormap (flat surface), hide the gradient
            self.gradient_widget.setVisible(False)

        self.max_label.setText(f"{max_val:.3f} mW")
        self.min_label.setText(f"{min_val:.3f} mW")
