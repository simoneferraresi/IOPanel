from __future__ import annotations

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Signal
from PySide6.QtGui import QOffscreenSurface, QOpenGLContext
from PySide6.QtWidgets import QLabel, QWidget

from app_settings import AppSettings
from config_model import AppConfig
from ui.alignment_panel import AlignmentPanel
from ui.main_window import MainWindow


class _ViewerStub(QWidget):
    colormap_updated = Signal(object, object)

    def __init__(self, parent=None, *, fail_update=False):
        super().__init__(parent)
        self.title_label = QLabel("", self)
        self.surface_plot = None
        self.fail_update = fail_update
        self.updates = []

    def update_plot(self, x_coords, y_coords, z_grid):
        if self.fail_update:
            self.fail_update = False
            raise RuntimeError("injected map replay failure")
        self.updates.append((x_coords, y_coords, z_grid))


def _install_viewer_stub(panel, monkeypatch, *, fail_construction=False, fail_first_update=False):
    created = []

    def factory():
        if fail_construction:
            raise RuntimeError("injected viewer construction failure")
        viewer = _ViewerStub(panel._plot_host, fail_update=fail_first_update and not created)
        created.append(viewer)
        return viewer

    monkeypatch.setattr(panel, "_create_plot3d_widget", factory)
    return created


def _pending_map(panel):
    x_coords = np.array([0.0, 1.0])
    y_coords = np.array([0.0, 2.0])
    z_grid = np.array([[0.0, 1.0], [2.0, 3.0]])
    title = "Power Map (Peak: 3.000 mW at X=1.00, Y=2.00 \u00b5m)"
    panel._pending_mapping = (x_coords, y_coords, z_grid, title)
    return x_coords, y_coords, z_grid, title


def _make_window(qtbot, tmp_path, monkeypatch, active_tab=0):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set_active_tab(active_tab)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)
    _install_viewer_stub(window.alignment_tab, monkeypatch)
    return window


def test_window_defers_alignment_viewer_until_selected_and_reuses_it(qtbot, tmp_path, monkeypatch):
    window = _make_window(qtbot, tmp_path, monkeypatch)
    assert window.tab_widget.currentIndex() == 0
    panel = window.alignment_tab
    assert panel._plot3d_widget is None
    window.show()
    qtbot.wait(30)
    assert panel._plot3d_widget is None

    window.tab_widget.setCurrentIndex(1)
    qtbot.wait(30)
    assert panel._plot3d_widget is None

    window.tab_widget.setCurrentIndex(2)
    qtbot.waitUntil(lambda: panel._plot3d_widget is not None)
    viewer = panel._plot3d_widget
    assert viewer.thread() == window.thread()

    window.tab_widget.setCurrentIndex(0)
    window.tab_widget.setCurrentIndex(2)
    qtbot.wait(30)
    assert panel._plot3d_widget is viewer
    window.close()


def test_restored_alignment_tab_creates_viewer_on_first_show(qtbot, tmp_path, monkeypatch):
    window = _make_window(qtbot, tmp_path, monkeypatch, active_tab=2)
    assert window.tab_widget.currentIndex() == 2
    assert window.alignment_tab._plot3d_widget is None
    window.show()
    qtbot.waitUntil(lambda: window.alignment_tab._plot3d_widget is not None)
    assert window.alignment_tab._plot3d_widget.thread() == window.thread()
    window.close()


def test_closing_unvisited_alignment_viewer_needs_no_gl_initialization(qtbot, tmp_path, monkeypatch):
    window = _make_window(qtbot, tmp_path, monkeypatch)
    window.show()
    qtbot.wait(30)
    assert window.alignment_tab._plot3d_widget is None
    window.close()


def test_pending_map_replay_keeps_title_and_updates_colorbar(qtbot, monkeypatch):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    _, _, _, title = _pending_map(panel)
    _install_viewer_stub(panel, monkeypatch)

    viewer = panel.plot3d_widget
    assert viewer.thread() == panel.thread()
    assert viewer.updates
    assert viewer.title_label.text() == title
    assert panel._pending_mapping is None


def test_immediate_map_title_keeps_micrometre_unit(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    _install_viewer_stub(panel, monkeypatch)
    viewer = panel.plot3d_widget

    panel.on_mapping_finished(np.array([0.0, 1.0]), np.array([0.0, 2.0]), np.array([[0.0, 1.0], [2.0, 3.0]]))

    assert viewer.title_label.text() == "Power Map (Peak: 3.000 mW at X=1.00, Y=2.00 \u00b5m)"
    assert panel._pending_mapping is None


def test_unvisited_panel_retains_only_the_latest_copied_map(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    x = np.array([0.0, 1.0])
    y = np.array([0.0, 2.0])
    first_grid = np.ones((2, 2))
    panel.on_mapping_finished(x, y, first_grid)
    first_snapshot = panel._pending_mapping
    second_grid = np.full((2, 2), 4.0)
    panel.on_mapping_finished(x, y, second_grid)

    assert panel._pending_mapping is not first_snapshot
    assert len(panel._pending_mapping) == 4
    assert panel._pending_mapping[2].max() == 4.0
    assert panel._pending_mapping[0] is not x
    assert panel._pending_mapping[2] is not second_grid


def test_viewer_construction_failure_preserves_placeholder_and_retries(qtbot, monkeypatch):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    _pending_map(panel)
    _install_viewer_stub(panel, monkeypatch, fail_construction=True)

    panel.show()
    qtbot.wait(10)
    assert panel._plot3d_widget is None
    assert panel._pending_mapping is not None
    assert panel._plot_placeholder.isVisible()
    assert "viewer unavailable" in panel._plot_placeholder.text()
    assert "retry" in panel._plot_placeholder.text()

    _install_viewer_stub(panel, monkeypatch)
    viewer = panel.plot3d_widget
    assert viewer.title_label.text().endswith("\u00b5m)")
    assert panel._pending_mapping is None


def test_pending_map_replay_failure_rolls_back_and_retries(qtbot, monkeypatch):
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    _pending_map(panel)
    created = _install_viewer_stub(panel, monkeypatch, fail_first_update=True)

    with pytest.raises(RuntimeError, match="injected map replay failure"):
        _ = panel.plot3d_widget
    assert panel._plot3d_widget is None
    assert panel._pending_mapping is not None
    assert panel._plot_placeholder.parent() is panel._plot_host
    assert created[0].parent() is None

    viewer = panel.plot3d_widget
    assert viewer is created[1]
    assert viewer.updates
    assert panel._pending_mapping is None


def test_immediate_map_render_failure_retains_latest_snapshot_and_recovers(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    created = _install_viewer_stub(panel, monkeypatch)
    viewer = panel.plot3d_widget
    viewer.fail_update = True
    x, y, z, title = _pending_map(panel)
    panel._pending_mapping = None

    panel.on_mapping_finished(x, y, z)

    assert panel._plot3d_widget is None
    assert panel._pending_mapping is not None
    assert panel._pending_mapping[3] == title
    assert "Power map retained" in panel._plot_placeholder.text()
    recovered = panel.plot3d_widget
    assert recovered is created[1]
    assert recovered.title_label.text() == title
    assert panel._pending_mapping is None


def _offscreen_gl_capability_error():
    surface = QOffscreenSurface()
    surface.create()
    context = QOpenGLContext()
    try:
        if not surface.isValid():
            return "Qt could not create an offscreen OpenGL surface"
        if not context.create() or not context.isValid():
            return "Qt could not create a valid OpenGL context"
        if not context.makeCurrent(surface):
            return "Qt could not make the OpenGL context current"

        from OpenGL import GL
        from OpenGL.error import GLError

        try:
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glFinish()
        except GLError as exc:
            return f"OpenGL context rejected a basic rendering operation: {exc}"
        return None
    finally:
        context.doneCurrent()
        surface.destroy()


def test_real_gl_render_requires_a_usable_context(qtbot):
    capability_error = _offscreen_gl_capability_error()
    if capability_error:
        pytest.skip(capability_error)

    from ui.plot_3d_widget import Plot3DWidget

    viewer = Plot3DWidget()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(100)
    assert viewer.view.isValid()
    viewer.update_plot(np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.array([[0.0, 1.0], [1.0, 3.0]]))
    qtbot.wait(100)
    assert viewer.view.isValid()


def test_real_viewer_restored_map_title_survives_failure_then_retry(qtbot, tmp_path, monkeypatch):
    capability_error = _offscreen_gl_capability_error()
    if capability_error:
        pytest.skip(capability_error)

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    settings = AppSettings(QSettings(str(tmp_path / "real-viewer.ini"), QSettings.Format.IniFormat))
    settings.set_active_tab(0)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)
    panel = window.alignment_tab
    x_coords, y_coords, z_grid, title = _pending_map(panel)
    panel.on_mapping_finished(x_coords, y_coords, z_grid)
    original_factory = panel._create_plot3d_widget
    attempts = 0

    def fail_once_then_create_real_viewer():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected initial real-viewer failure")
        return original_factory()

    monkeypatch.setattr(panel, "_create_plot3d_widget", fail_once_then_create_real_viewer)
    # Keep this lifecycle regression about widget ownership and map state. Actual
    # GL painting is capability-gated separately because hosted/RDP contexts vary.
    window.setUpdatesEnabled(False)
    window.show()
    window.tab_widget.setCurrentIndex(2)
    qtbot.waitUntil(lambda: "viewer unavailable" in panel._plot_placeholder.text())
    assert panel._pending_mapping is not None

    window.tab_widget.setCurrentIndex(0)
    window.tab_widget.setCurrentIndex(2)
    qtbot.waitUntil(lambda: panel._plot3d_widget is not None)
    qtbot.wait(150)

    assert attempts == 2
    assert panel._plot3d_widget.title_label.text() == title
    assert panel._pending_mapping is None
    assert panel._plot3d_widget.surface_plot is not None
    assert panel._plot3d_widget.grid_item in panel._plot3d_widget.view.items
    assert panel._plot3d_widget.x_axis in panel._plot3d_widget.view.items
    assert panel._plot3d_widget.y_axis in panel._plot3d_widget.view.items
    assert panel._plot3d_widget.z_axis in panel._plot3d_widget.view.items
    assert panel._plot3d_widget.x_label.text == "X (\u00b5m)"
    assert panel._plot3d_widget.y_label.text == "Y (\u00b5m)"
    assert panel._plot3d_widget.z_label.text == "Power (a.u.)"
    assert panel.colorbar_widget.min_label.text() == "0.000 mW"
    assert panel.colorbar_widget.max_label.text() == "3.000 mW"
    window.close()
