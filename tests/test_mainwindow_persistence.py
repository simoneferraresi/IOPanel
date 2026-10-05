from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

from app_settings import AppSettings
from config_model import AppConfig, CameraConfig
from hardware.ct400 import CT400
from logic.task_runner import TaskRunner
from ui import main_window as main_window_module
from ui.camera_widgets import CameraPanel
from ui.control_panel import ScanSettings
from ui.plot_widgets import PlotWidget


def make_settings(path: Path) -> AppSettings:
    backend = QSettings(str(path), QSettings.Format.IniFormat)
    backend.clear()
    backend.sync()
    return AppSettings(backend)


def test_mainwindow_restores_preferences_without_hardware_operations(qtbot, tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("settings restoration must not operate hardware")

    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    monkeypatch.setattr(TaskRunner, "start", forbidden)
    monkeypatch.setattr(CT400, "cmd_laser", forbidden)
    monkeypatch.setattr(CT400, "set_detector_array", forbidden)
    monkeypatch.setattr(main_window_module.VimbaCam, "open", forbidden)
    settings = make_settings(tmp_path / "window.ini")
    settings.set_geometry(QByteArray())  # Invalid geometry must preserve configured sizing.
    settings.set_splitter_sizes([600, 400])
    settings.set_active_tab(2)
    settings.set_scan_detectors([1, 3, 4])
    camera_a = tmp_path / "camera-a"
    camera_b = tmp_path / "camera-b"
    camera_a.mkdir()
    camera_b.mkdir()
    settings.set_camera_controls_visible("CAM-A", True)
    settings.set_camera_screenshot_directory("CAM-A", camera_a)
    settings.set_camera_screenshot_directory("CAM-B", camera_b)

    first = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(first)
    first.show()
    qtbot.wait(20)

    assert first.tab_widget.currentIndex() == 2
    checkboxes = first.control_panel.scan_detector_cbs
    assert checkboxes[next(detector for detector in checkboxes if detector.value == 1)].isChecked()
    assert checkboxes[next(detector for detector in checkboxes if detector.value == 3)].isChecked()
    assert checkboxes[next(detector for detector in checkboxes if detector.value == 4)].isChecked()
    assert not checkboxes[next(detector for detector in checkboxes if detector.value == 2)].isChecked()

    panel_a = first._create_camera_panel(
        None, CameraConfig(identifier="CAM-A", name="Camera A", backend="simulation")
    )
    panel_b = first._create_camera_panel(
        None, CameraConfig(identifier="CAM-B", name="Camera B", backend="simulation")
    )
    assert panel_a.get_controls_visible() is True
    assert panel_b.get_controls_visible() is False
    assert panel_a.last_save_dir == camera_a.resolve()
    assert panel_b.last_save_dir == camera_b.resolve()
    first.tab_widget.setCurrentIndex(1)
    first.main_splitter.setSizes([500, 500])
    first.resize(1320, 920)
    first.show()
    first._save_ui_preferences()

    assert settings.geometry() is not None
    assert settings.active_tab(3) == 1
    assert settings.splitter_sizes(2) is not None

    second = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(second)
    second.show()
    qtbot.wait(20)
    assert second.tab_widget.currentIndex() == 1
    assert second.settings.active_tab(second.tab_widget.count()) == 1
    assert second.main_splitter.sizes()[0] > 0
    saved_geometry = settings.geometry()
    assert isinstance(saved_geometry, QByteArray)


def test_invalid_window_tab_and_splitter_preferences_fall_back(qtbot, tmp_path, monkeypatch):
    settings = make_settings(tmp_path / "invalid-window.ini")
    settings._settings.setValue("MainWindow/active_tab", 99)
    settings._settings.setValue("MainWindow/splitter_sizes", [700])
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)

    window = main_window_module.MainWindow(
        AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(20)

    assert window.tab_widget.currentIndex() == 0
    assert len(window.main_splitter.sizes()) == window.main_splitter.count() == 2
    assert all(size > 0 for size in window.main_splitter.sizes())


def test_output_widgets_start_in_independent_persisted_directories(qtbot, tmp_path):
    settings = make_settings(tmp_path / "outputs.ini")
    scan = tmp_path / "scan"
    plot = tmp_path / "plot"
    camera = tmp_path / "camera"
    for directory in (scan, plot, camera):
        directory.mkdir()
    settings.set_scan_export_directory(scan)
    settings.set_plot_image_directory(plot)
    camera_b = tmp_path / "camera-b"
    camera_b.mkdir()
    settings.set_camera_screenshot_directory("camera-1", camera)
    settings.set_camera_screenshot_directory("camera-2", camera_b)
    settings.set_camera_controls_visible("camera-1", True)

    plot_widget = PlotWidget(ScanSettings(), settings=settings)
    qtbot.addWidget(plot_widget)
    camera_panel = CameraPanel(
        None,
        "Camera A",
        CameraConfig(identifier="camera-1", name="Camera A", backend="simulation"),
        settings=settings,
    )
    qtbot.addWidget(camera_panel)

    assert plot_widget.last_scan_save_dir == scan.resolve()
    assert plot_widget.last_plot_image_dir == plot.resolve()
    assert camera_panel.last_save_dir == camera.resolve()
    assert settings.camera_screenshot_directory("camera-2") == camera_b.resolve()
