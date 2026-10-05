from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings

from app_settings import AppSettings


def make_settings(path: Path) -> AppSettings:
    backend = QSettings(str(path), QSettings.Format.IniFormat)
    backend.clear()
    backend.sync()
    return AppSettings(backend)


def test_missing_schema_initializes_v1_and_defaults(tmp_path):
    settings = make_settings(tmp_path / "settings.ini")

    assert settings.supported
    assert settings._settings.value("_meta/schema_version") == 1
    assert settings.active_tab(3) == 0
    assert settings.scan_detectors() == [1]
    assert settings.scan_export_formats() == (True, True, False)
    assert settings.power_monitor_export_directory() == Path.cwd()
    assert settings.power_monitor_export_formats() == (True, True)


def test_schema_v1_typed_values_round_trip(tmp_path):
    settings = make_settings(tmp_path / "settings.ini")
    settings.set_geometry(QByteArray(b"geometry"))
    settings.set_splitter_sizes([400, 300])
    settings.set_active_tab(2)
    settings.set_camera_controls_visible("CAM-A", True)
    settings.set_scan_detectors([1, 3, 4])
    settings.set_scan_export_formats(csv=False, mat=True, fig=True)
    power_monitor_directory = Path.cwd()
    settings.set_power_monitor_export_directory(power_monitor_directory)
    settings.set_power_monitor_export_formats(csv=False, mat=True)
    settings.sync()

    assert settings.geometry() == QByteArray(b"geometry")
    assert settings.splitter_sizes(2) == [400, 300]
    assert settings.active_tab(3) == 2
    assert settings.camera_controls_visible("CAM-A") is True
    assert settings.camera_controls_visible("CAM-B") is False
    assert settings.scan_detectors() == [1, 3, 4]
    assert settings.scan_export_formats() == (False, True, True)
    assert settings.power_monitor_export_directory() == power_monitor_directory.resolve()
    assert settings.power_monitor_export_formats() == (False, True)


def test_future_schema_is_left_untouched_and_uses_defaults(tmp_path):
    backend = QSettings(str(tmp_path / "future.ini"), QSettings.Format.IniFormat)
    backend.setValue("_meta/schema_version", 2)
    backend.setValue("MainWindow/active_tab", 2)
    backend.setValue("Export/formats/csv", False)
    backend.setValue("Export/power_monitor_formats/mat", False)
    backend.sync()
    settings = AppSettings(backend)

    settings.set_active_tab(1)
    settings.set_scan_export_formats(csv=True, mat=False, fig=True)
    settings.set_power_monitor_export_formats(csv=False, mat=False)
    settings.sync()

    assert not settings.supported
    assert settings.active_tab(3) == 0
    assert backend.value("_meta/schema_version") == 2
    assert backend.value("MainWindow/active_tab") == 2
    assert backend.value("Export/formats/csv") is False
    assert backend.value("Export/power_monitor_formats/mat") is False
    assert settings.scan_export_formats() == (True, True, False)
    assert settings.power_monitor_export_formats() == (True, True)


def test_malformed_export_format_values_fall_back_individually(tmp_path):
    settings = make_settings(tmp_path / "malformed-formats.ini")
    settings._settings.setValue("Export/formats/csv", "bad")
    settings._settings.setValue("Export/formats/mat", False)
    settings._settings.setValue("Export/formats/fig", "yes")

    assert settings.scan_export_formats() == (True, False, False)


def test_malformed_power_monitor_formats_fall_back_independently(tmp_path):
    settings = make_settings(tmp_path / "malformed-power-monitor-formats.ini")
    settings._settings.setValue("Export/power_monitor_formats/csv", "bad")
    settings._settings.setValue("Export/power_monitor_formats/mat", False)
    assert settings.power_monitor_export_formats() == (True, False)


def test_malformed_values_fall_back_independently(tmp_path):
    settings = make_settings(tmp_path / "malformed.ini")
    settings._settings.setValue("MainWindow/active_tab", "bad")
    settings._settings.setValue("MainWindow/splitter_sizes", [500, -1])
    settings._settings.setValue("CameraPanels/CAM-A/controls_visible", "yes")
    settings._settings.setValue("Scan/detectors", [3, 3, 99, "bad"])

    assert settings.active_tab(3, fallback=1) == 1
    assert settings.splitter_sizes(2) is None
    assert settings.camera_controls_visible("CAM-A") is False
    assert settings.scan_detectors() == [1, 3]

    settings._settings.setValue("MainWindow/splitter_sizes", [900_000, 900_000])
    assert settings.splitter_sizes(2) is None


def test_directory_paths_are_validated_and_independent(tmp_path):
    settings = make_settings(tmp_path / "paths.ini")
    scan = tmp_path / "scan"
    power_monitor = tmp_path / "power-monitor"
    plot = tmp_path / "plot"
    camera_a = tmp_path / "camera-a"
    camera_b = tmp_path / "camera-b"
    for directory in (scan, power_monitor, plot, camera_a, camera_b):
        directory.mkdir()

    settings.set_scan_export_directory(scan)
    settings.set_power_monitor_export_directory(power_monitor)
    settings.set_plot_image_directory(plot)
    settings.set_camera_screenshot_directory("A", camera_a)
    settings.set_camera_screenshot_directory("B", camera_b)
    assert settings.scan_export_directory() == scan.resolve()
    assert settings.power_monitor_export_directory() == power_monitor.resolve()
    assert settings.plot_image_directory() == plot.resolve()
    assert settings.camera_screenshot_directory("A") == camera_a.resolve()
    assert settings.camera_screenshot_directory("B") == camera_b.resolve()

    settings._settings.setValue("Paths/plot_image", str(tmp_path / "missing"))
    assert settings.plot_image_directory() == Path.cwd()
    assert settings.scan_export_directory() == scan.resolve()
    assert settings.power_monitor_export_directory() == power_monitor.resolve()
    assert settings.camera_screenshot_directory("A") == camera_a.resolve()


def test_detector_normalization_keeps_mandatory_detector_and_stable_order(tmp_path):
    settings = make_settings(tmp_path / "detectors.ini")
    settings._settings.setValue("Scan/detectors", [3, 3, 99])

    assert settings.scan_detectors() == [1, 3]
    settings.set_scan_detectors([4, 2, 4, 99])
    assert settings.scan_detectors() == [1, 2, 4]


def test_camera_preferences_are_keyed_by_identifier(tmp_path):
    settings = make_settings(tmp_path / "cameras.ini")
    settings.set_camera_controls_visible("serial-A", True)
    settings.set_camera_controls_visible("serial-B", False)

    assert settings.camera_controls_visible("serial-A") is True
    assert settings.camera_controls_visible("serial-B") is False
    assert settings.camera_controls_visible("new-camera") is False
