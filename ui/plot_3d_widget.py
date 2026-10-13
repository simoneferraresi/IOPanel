from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pyqtgraph.opengl as gl
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QSurfaceFormat, QVector3D
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.typography import plot_title_font


class Plot3DWidget(QWidget):
    """
    A widget for displaying a 3D surface plot, including a title, axes,
    and hooks for an external color bar.
    """

    # Signal to update the color bar: cmap object, min value, max value
    colormap_updated = Signal(object, float, float)

    # This factor scales the Z-axis to make height variations visible.
    # Tune this value to adjust the "peakiness" of the plot.
    Z_SCALE_FACTOR = 200.0

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel("Power Map")
        self.title_label.setFont(plot_title_font())
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.view = gl.GLViewWidget()
        surface_format = QSurfaceFormat()
        surface_format.setVersion(2, 1)
        surface_format.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
        self.view.setFormat(surface_format)
        self.view.opts["distance"] = 40
        self.view.opts["elevation"] = 30
        self.view.opts["azimuth"] = -30
        layout.addWidget(self.view, stretch=1)

        # --- NEW: Initialize plot items that will be updated later ---
        self.surface_plot = None
        self.grid_item = gl.GLGridItem()
        self.x_axis = gl.GLLinePlotItem()
        self.y_axis = gl.GLLinePlotItem()
        self.z_axis = gl.GLLinePlotItem()
        self.x_label = gl.GLTextItem()
        self.y_label = gl.GLTextItem()
        self.z_label = gl.GLTextItem()

        # Add the items to the view once
        self.view.addItem(self.grid_item)
        self.view.addItem(self.x_axis)
        self.view.addItem(self.y_axis)
        self.view.addItem(self.z_axis)
        self.view.addItem(self.x_label)
        self.view.addItem(self.y_label)
        self.view.addItem(self.z_label)

    @Slot(np.ndarray, np.ndarray, np.ndarray)
    def update_plot(self, x: np.ndarray, y: np.ndarray, z: np.ndarray):
        """
        Updates the plot with new data, including scaled axes and labels.
        x, y: 1D arrays of coordinates in micrometers.
        z: 2D array of power values in milliwatts.
        """
        if self.surface_plot:
            self.view.removeItem(self.surface_plot)

        if x is None or y is None or z is None or z.size == 0:
            self.title_label.setText("Power Map (No Data)")
            return

        expected_z_shape = (len(x), len(y))
        if z.ndim != 2 or z.shape != expected_z_shape:
            raise ValueError(f"Mapping z shape {z.shape} does not match expected {expected_z_shape} (len(x), len(y))")

        self.title_label.setText("Power Map (mW)")

        x_min, x_max = x.min(), x.max()
        y_min, y_max = y.min(), y.max()
        z_min, z_max = z.min(), z.max()

        # --- 1. DYNAMIC Z-AXIS AUTO-SCALING ---
        xy_span = max(x_max - x_min, y_max - y_min)
        if xy_span == 0:
            xy_span = 1.0  # Avoid division by zero

        # If z is flat, don't scale it, otherwise compute a dynamic scale factor
        z_span = z_max - z_min
        if z_span > 1e-9:
            # We want the visual height of the peak to be a fraction of the plot's width
            # 0.3 is a good starting point, tune as needed.
            desired_z_height = xy_span * 0.3
            z_scale_factor = desired_z_height / z_span
            z_scaled = z * z_scale_factor
        else:
            # If the surface is flat, no scaling is needed.
            z_scaled = z.copy()

        # Center the plot visually by shifting the data
        x_shifted = x - (x_max + x_min) / 2
        y_shifted = y - (y_max + y_min) / 2
        z_scaled_shifted = z_scaled - z_scaled.min()

        # --- 2. UPDATE THE PLOT SURFACE ---
        if z_span < 1e-9:  # Flat surface
            color = (0.8, 0.8, 0.8, 1.0)
            self.surface_plot = gl.GLSurfacePlotItem(
                x=x_shifted, y=y_shifted, z=z_scaled_shifted, color=color, shader="shaded"
            )
        else:
            cmap = pg.colormap.get("viridis")
            normalized_z = (z - z_min) / z_span
            colors = cmap.map(normalized_z, "float")
            self.surface_plot = gl.GLSurfacePlotItem(
                x=x_shifted, y=y_shifted, z=z_scaled_shifted, colors=colors, shader="shaded"
            )

        self.surface_plot.setGLOptions("opaque")
        self.view.addItem(self.surface_plot)

        # --- 3. CONFIGURE AXES AND GRID TO MATCH DATA ---
        # Configure the grid to match the shifted data ranges
        self.grid_item.resetTransform()
        self.grid_item.setSize(x=max(abs(x_shifted)), y=max(abs(y_shifted)))
        self.grid_item.setSpacing(x=max(abs(x_shifted)) / 5, y=max(abs(y_shifted)) / 5)

        # Configure the axis lines
        axis_len_x = x_max - x_min
        axis_len_y = y_max - y_min
        axis_len_z = z_scaled.max() - z_scaled.min()
        self.view.setCameraPosition(
            pos=QVector3D(0, 0, axis_len_z / 2), distance=max(xy_span * 2.5, axis_len_z * 2.5, 1.0)
        )

        # X-Axis Line and Label
        x_axis_points = np.array([[x_shifted.min(), y_shifted.min(), 0], [x_shifted.max(), y_shifted.min(), 0]])
        self.x_axis.setData(pos=x_axis_points, color=(1, 1, 1, 1), width=2)
        self.x_label.setData(pos=(x_shifted.max(), y_shifted.min() - axis_len_y * 0.2, 0), text="X (\u00b5m)")

        # Y-Axis Line and Label
        y_axis_points = np.array([[x_shifted.min(), y_shifted.min(), 0], [x_shifted.min(), y_shifted.max(), 0]])
        self.y_axis.setData(pos=y_axis_points, color=(1, 1, 1, 1), width=2)
        self.y_label.setData(
            pos=(x_shifted.min() - axis_len_x * 0.2, y_shifted.max() + axis_len_y * 0.5, 0), text="Y (\u00b5m)"
        )

        # Z-Axis Line and Label
        z_axis_points = np.array(
            [[x_shifted.min(), y_shifted.min(), 0], [x_shifted.min(), y_shifted.min(), axis_len_z]]
        )
        self.z_axis.setData(pos=z_axis_points, color=(1, 1, 1, 1), width=2)
        self.z_label.setData(pos=(x_shifted.min(), y_shifted.min(), axis_len_z * 1.05), text="Power (a.u.)")

        # --- 4. UPDATE THE COLOR BAR ---
        self.colormap_updated.emit(cmap if z_span > 1e-9 else None, z_min, z_max)
