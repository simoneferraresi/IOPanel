from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QByteArray, QSettings

from app_settings import AppSettings
from config_model import CameraConfig
from hardware.ct400_types import Detector
from ui import main_window as main_window_module
from ui.camera_widgets import CameraPanel


def make_settings(path: Path) -> AppSettings:
    backend = QSettings(str(path), QSettings.Format.IniFormat)
    backend.clear()
    backend.sync()
    return AppSettings(backend)


def test_mainwindow_restores_and_saves_preferences_without_hardware_operations(tmp_path):
    settings = make_settings(tmp_path / "window.ini")
    settings.set_geometry(QByteArray(b"saved-geometry"))
    settings.set_splitter_sizes([600, 400])
    settings.set_active_tab(2)
    settings.set_scan_detectors([1, 3, 4])
    settings.set_camera_controls_visible("CAM-A", True)
    settings.set_camera_controls_visible("CAM-B", False)
    camera_directory = tmp_path / "camera-a"
    camera_directory.mkdir()
    settings.set_camera_screenshot_directory("CAM-A", camera_directory)

    class Splitter:
        def count(self):
            return 2

        def setSizes(self, sizes):
            self.restored_sizes = sizes

        def sizes(self):
            return self.restored_sizes

    class Tabs:
        def count(self):
            return 3

        def currentIndex(self):
            return self.index

        def setCurrentIndex(self, index):
            self.index = index

    class Checkbox:
        def setChecked(self, checked):
            self.checked = checked

    scan_detectors = (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4)
    checkboxes = {detector: Checkbox() for detector in scan_detectors}
    tabs = Tabs()
    tabs.index = 0
    splitter = Splitter()
    window = SimpleNamespace(
        settings=settings,
        main_splitter=splitter,
        tab_widget=tabs,
        control_panel=SimpleNamespace(
            scan_detector_cbs=checkboxes,
            _selected_scan_detectors=lambda: [Detector.DE_1, Detector.DE_3, Detector.DE_4],
        ),
        control_container=SimpleNamespace(isVisible=lambda: True),
        camera_panels={
            "CAM-A": SimpleNamespace(get_controls_visible=lambda: True),
            "CAM-B": SimpleNamespace(get_controls_visible=lambda: False),
        },
        restored_geometry=None,
        restoreGeometry=lambda geometry: setattr(window, "restored_geometry", geometry),
        isFullScreen=lambda: False,
        saveGeometry=lambda: QByteArray(b"new-geometry"),
        saveState=lambda: QByteArray(b"dock-state"),
        log_console_dock=SimpleNamespace(isVisible=lambda: True),
        log_console=SimpleNamespace(level_filter=SimpleNamespace(currentText=lambda: "WARNING")),
    )

    main_window_module.MainWindow._restore_ui_preferences(window)
    assert window.restored_geometry == QByteArray(b"saved-geometry")
    assert splitter.restored_sizes == [600, 400]
    assert tabs.currentIndex() == 2
    assert [checkboxes[detector].checked for detector in scan_detectors] == [True, False, True, True]

    main_window_module.MainWindow._save_ui_preferences(window)
    assert settings.geometry() == QByteArray(b"new-geometry")
    assert settings.active_tab(3) == 2
    assert settings.splitter_sizes(2) == [600, 400]
    assert settings.scan_detectors() == [1, 3, 4]
    assert settings.camera_controls_visible("CAM-A") is True
    assert settings.camera_controls_visible("CAM-B") is False
    assert settings.log_console_visible() is True
    assert settings.log_console_state() == QByteArray(b"dock-state")
    assert settings.log_console_level() == "WARNING"
    assert settings.camera_screenshot_directory("CAM-A") == camera_directory.resolve()


def test_mainwindow_restore_preferences_falls_back_for_invalid_tab_and_splitter(tmp_path):
    settings = make_settings(tmp_path / "invalid-window.ini")
    settings.set_geometry(QByteArray(b"saved-geometry"))
    settings._settings.setValue("MainWindow/active_tab", 99)
    settings._settings.setValue("MainWindow/splitter_sizes", [700])
    settings._settings.setValue("Scan/detectors", [3, 3, 99])
    splitter = SimpleNamespace(count=lambda: 2, setSizes=lambda sizes: setattr(splitter, "restored_sizes", sizes))
    splitter.restored_sizes = None

    class Tabs:
        index = 0

        def count(self):
            return 3

        def currentIndex(self):
            return self.index

        def setCurrentIndex(self, index):
            self.index = index

    class Checkbox:
        checked = False

        def setChecked(self, value):
            self.checked = value

    checkboxes = {detector: Checkbox() for detector in (Detector.DE_1, Detector.DE_2, Detector.DE_3, Detector.DE_4)}
    tabs = Tabs()
    window = SimpleNamespace(
        settings=settings,
        main_splitter=splitter,
        tab_widget=tabs,
        control_panel=SimpleNamespace(scan_detector_cbs=checkboxes),
        restored_geometry=None,
        restoreGeometry=lambda geometry: setattr(window, "restored_geometry", geometry) or False,
    )

    main_window_module.MainWindow._restore_ui_preferences(window)

    assert window.restored_geometry == QByteArray(b"saved-geometry")
    assert splitter.restored_sizes is None
    assert tabs.currentIndex() == 0
    assert [checkbox.checked for checkbox in checkboxes.values()] == [True, False, True, False]


def test_camera_panel_uses_persisted_screenshot_directory(qtbot, tmp_path):
    settings = make_settings(tmp_path / "camera.ini")
    screenshot_directory = tmp_path / "camera-screenshots"
    screenshot_directory.mkdir()
    settings.set_camera_controls_visible("camera-1", True)
    settings.set_camera_screenshot_directory("camera-1", screenshot_directory)

    panel = CameraPanel(
        None,
        "Camera A",
        CameraConfig(identifier="camera-1", name="Camera A", backend="simulation"),
        settings=settings,
    )
    qtbot.addWidget(panel)

    assert settings.camera_controls_visible("camera-1") is True
    assert panel.get_controls_visible() is False
    assert panel.last_save_dir == screenshot_directory.resolve()


def test_create_camera_panel_applies_saved_visibility_with_lightweight_double(tmp_path, monkeypatch):
    settings = make_settings(tmp_path / "panel-restore.ini")
    settings.set_camera_controls_visible("CAM-A", True)

    class FakeCameraPanel:
        def __init__(self, camera, title, *, config, parent, settings):
            self.camera = camera
            self.title = title
            self.config = config
            self.parent = parent
            self.settings = settings
            self.visibility = None
            self.video_label = SimpleNamespace(setText=lambda _text: None)
            self.maximize_requested = SimpleNamespace(connect=lambda slot: setattr(self, "maximize_slot", slot))

        def set_controls_visibility(self, visible):
            self.visibility = visible

    monkeypatch.setattr(main_window_module, "CameraPanel", FakeCameraPanel)
    owner = SimpleNamespace(
        camera_container=object(),
        settings=settings,
        toggle_cinema_mode=lambda: None,
    )

    configured = main_window_module.MainWindow._create_camera_panel(
        owner, None, CameraConfig(identifier="CAM-A", name="Camera A", backend="simulation")
    )
    defaulted = main_window_module.MainWindow._create_camera_panel(
        owner, None, CameraConfig(identifier="CAM-B", name="Camera B", backend="simulation")
    )

    assert configured.settings is settings
    assert configured.config.identifier == "CAM-A"
    assert configured.visibility is True
    assert defaulted.config.identifier == "CAM-B"
    assert defaulted.visibility is False
