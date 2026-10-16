from pathlib import Path

import pytest
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
    assert settings.application_icon() == "optical_burst"
    assert settings.pm5020_preferences() == {
        "monitor_interval_ms": 1000,
        "interval_unit": "s",
        "history_seconds": 10,
    }


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
    settings.set_application_icon("prism_spectrum")
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
    assert settings.application_icon() == "prism_spectrum"
    assert AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)).application_icon() == (
        "prism_spectrum"
    )


def test_log_level_preferences_migrate_and_reject_unknown_values(tmp_path):
    settings = make_settings(tmp_path / "log-level.ini")
    for legacy, expected in (("DEBUG", "All"), ("INFO", "INFO+"), ("WARNING", "WARNING+"), ("ERROR", "ERROR+")):
        settings._settings.setValue("MainWindow/log_console_level", legacy)
        assert settings.log_console_level() == expected

    settings._settings.setValue("MainWindow/log_console_level", "broken")
    assert settings.log_console_level() == "All"
    settings.set_log_console_level("INFO+")
    assert settings.log_console_level() == "INFO+"


def test_future_schema_is_left_untouched_and_uses_defaults(tmp_path):
    backend = QSettings(str(tmp_path / "future.ini"), QSettings.Format.IniFormat)
    backend.setValue("_meta/schema_version", 2)
    backend.setValue("MainWindow/active_tab", 2)
    backend.setValue("Export/formats/csv", False)
    backend.setValue("Export/power_monitor_formats/mat", False)
    backend.setValue("Appearance/application_icon", "prism_spectrum")
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
    assert settings.application_icon() == "optical_burst"
    assert backend.value("Appearance/application_icon") == "prism_spectrum"


def test_pm5020_preferences_round_trip_and_validate_each_value(tmp_path):
    path = tmp_path / "pm5020.ini"
    settings = make_settings(path)
    assert settings.pm5020_channel_preferences(1) == {
        "enabled": True,
        "wavelength_nm": 1550.0,
        "auto_range": True,
        "power_range_w": 1e-3,
        "range_unit": "mW",
        "display_unit": "mW",
        "input_filter_enabled": False,
    }
    settings.set_pm5020_channel_preferences(
        1,
        {
            "enabled": False,
            "wavelength_nm": 1550.123456789,
            "auto_range": False,
            "power_range_w": 2.5e-6,
            "range_unit": "µW",
            "display_unit": "dBm",
            "input_filter_enabled": True,
        },
    )
    settings.set_pm5020_channel_preferences(2, {"enabled": True, "wavelength_nm": 780.25})
    settings.set_pm5020_preferences(monitor_interval_ms=2500, interval_unit="ms", history_seconds=300)
    settings.sync()

    reloaded = AppSettings(QSettings(str(path), QSettings.Format.IniFormat))
    assert reloaded.pm5020_channel_preferences(1)["wavelength_nm"] == pytest.approx(1550.123456789)
    assert reloaded.pm5020_channel_preferences(1)["power_range_w"] == pytest.approx(2.5e-6)
    assert reloaded.pm5020_channel_preferences(1)["range_unit"] == "µW"
    assert reloaded.pm5020_channel_preferences(1)["display_unit"] == "dBm"
    assert reloaded.pm5020_channel_preferences(1)["input_filter_enabled"] is True
    assert reloaded.pm5020_channel_preferences(1)["enabled"] is False
    assert reloaded.pm5020_channel_preferences(2)["wavelength_nm"] == 780.25
    assert reloaded.pm5020_channel_preferences(2)["enabled"] is True
    assert reloaded.pm5020_preferences() == {
        "monitor_interval_ms": 2500,
        "interval_unit": "ms",
        "history_seconds": 300,
    }


def test_pm5020_corrupt_values_fall_back_individually_and_future_schema_is_read_only(tmp_path):
    settings = make_settings(tmp_path / "pm5020-invalid.ini")
    backend = settings._settings
    backend.setValue("PM5020/Channels/1/enabled", "false")
    backend.setValue("PM5020/Channels/1/wavelength_nm", float("nan"))
    backend.setValue("PM5020/Channels/1/auto_range", False)
    backend.setValue("PM5020/Channels/1/power_range_w", float("inf"))
    backend.setValue("PM5020/Channels/1/range_unit", "mW/s")
    backend.setValue("PM5020/Channels/1/display_unit", "unsupported")
    backend.setValue("PM5020/Channels/1/input_filter_enabled", 1)
    backend.setValue("PM5020/monitor_interval_ms", 200)
    backend.setValue("PM5020/interval_unit", "minutes")
    backend.setValue("PM5020/history_seconds", 45)
    channel = settings.pm5020_channel_preferences(1)
    assert channel["enabled"] is True
    assert channel["wavelength_nm"] == 1550.0
    assert channel["auto_range"] is False
    assert channel["power_range_w"] == 1e-3
    assert channel["range_unit"] == "mW"
    assert channel["display_unit"] == "mW"
    assert channel["input_filter_enabled"] is False
    assert settings.pm5020_preferences() == {
        "monitor_interval_ms": 1000,
        "interval_unit": "s",
        "history_seconds": 10,
    }
    assert settings.pm5020_channel_preferences(2)["wavelength_nm"] == 775.0
    assert AppSettings._finite_number(10**10000, 1.0, 100000.0) is None

    backend.setValue("_meta/schema_version", 3)
    future = AppSettings(backend)
    future.set_pm5020_channel_preferences(1, {"wavelength_nm": 1700.0})
    future.set_pm5020_preferences(monitor_interval_ms=2500, interval_unit="ms", history_seconds=300)
    assert not future.supported
    assert future.pm5020_channel_preferences(1)["wavelength_nm"] == 1550.0
    assert future.pm5020_preferences()["monitor_interval_ms"] == 1000


def test_application_icon_rejects_unknown_values_and_invalid_types(tmp_path):
    settings = make_settings(tmp_path / "application-icon.ini")
    settings._settings.setValue("Appearance/application_icon", "unknown")
    assert settings.application_icon() == "optical_burst"

    settings._settings.setValue("Appearance/application_icon", 42)
    assert settings.application_icon() == "optical_burst"
    settings.set_application_icon("unknown")
    assert settings.application_icon() == "optical_burst"


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


@pytest.mark.parametrize(("legacy_index", "new_index"), [(0, 1), (1, 0), (2, 2)])
def test_legacy_active_tab_indices_preserve_logical_selection(tmp_path, legacy_index, new_index):
    settings = make_settings(tmp_path / "legacy-tab.ini")
    settings._settings.setValue("MainWindow/active_tab", legacy_index)
    assert settings.active_tab(3) == new_index

    # The new semantic key prevents old indices from being remapped repeatedly.
    settings.set_active_tab(new_index)
    settings.sync()
    reloaded = AppSettings(QSettings(str(tmp_path / "legacy-tab.ini"), QSettings.Format.IniFormat))
    assert reloaded.active_tab(3) == new_index
    assert reloaded._settings.value("MainWindow/active_tab_id") == AppSettings.ACTIVE_TAB_IDS[new_index]


def test_invalid_active_tab_identity_falls_back_without_legacy_reinterpretation(tmp_path):
    settings = make_settings(tmp_path / "invalid-tab-id.ini")
    settings._settings.setValue("MainWindow/active_tab", 0)
    settings._settings.setValue("MainWindow/active_tab_id", "unrecognized_page")
    assert settings.active_tab(3, fallback=2) == 2
    assert settings.active_tab(3) == 0


def test_new_tab_setting_prefers_semantic_identity_over_legacy_index(tmp_path):
    settings = make_settings(tmp_path / "semantic-tab.ini")
    settings.set_active_tab(1)
    settings._settings.setValue("MainWindow/active_tab", 0)
    assert settings.active_tab(3) == 1
    assert settings._settings.value("MainWindow/active_tab_id") == "wavelength_scan"
