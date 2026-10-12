from PySide6.QtCore import QSettings

from app_settings import AppSettings
from config_model import AppConfig
from ui.main_window import MainWindow


def test_window_defers_alignment_viewer_until_selected_and_reuses_it(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set_active_tab(0)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)

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
    assert viewer.thread() is window.thread()

    window.tab_widget.setCurrentIndex(0)
    window.tab_widget.setCurrentIndex(2)
    qtbot.wait(30)
    assert panel._plot3d_widget is viewer
    window.close()


def test_restored_alignment_tab_creates_viewer_on_first_show(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set_active_tab(2)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)

    assert window.tab_widget.currentIndex() == 2
    assert window.alignment_tab._plot3d_widget is None
    window.show()
    qtbot.waitUntil(lambda: window.alignment_tab._plot3d_widget is not None)
    window.close()


def test_closing_unvisited_alignment_viewer_needs_no_gl_initialization(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set_active_tab(0)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(30)
    assert window.alignment_tab._plot3d_widget is None
    window.close()


def test_lazy_viewer_replays_pending_map_and_updates_colorbar(qtbot):
    import numpy as np

    from ui.alignment_panel import AlignmentPanel

    panel = AlignmentPanel(None, None, None)
    qtbot.addWidget(panel)
    panel._pending_mapping = (
        np.array([0.0, 1.0]),
        np.array([0.0, 1.0]),
        np.array([[0.0, 1.0], [1.0, 3.0]]),
        "Power Map (Peak: 3.000 mW)",
    )

    viewer = panel.plot3d_widget
    assert viewer.thread() is panel.thread()
    assert viewer.surface_plot is not None
    assert viewer.title_label.text() == "Power Map (Peak: 3.000 mW)"
    assert panel.colorbar_widget.max_label.text() == "3.000 mW"
    assert panel.colorbar_widget.min_label.text() == "0.000 mW"
    assert panel._pending_mapping is None

    viewer.update_plot(np.array([0.0, 1.0]), np.array([0.0, 1.0]), np.ones((2, 2)))
    assert not panel.colorbar_widget.gradient_widget.isVisible()
    panel.close()
