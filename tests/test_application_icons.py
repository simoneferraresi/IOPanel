from __future__ import annotations

from PySide6.QtCore import QFile, QIODevice, QSettings, QSize
from PySide6.QtWidgets import QApplication

from app_settings import AppSettings
from config_model import AppConfig
from ui.application_icons import APPLICATION_ICON_SIZES, APPLICATION_ICONS, application_icon
from ui.main_window import MainWindow


def test_application_icon_resources_render_with_transparent_corners(qapp, monkeypatch, tmp_path):
    original_cwd = tmp_path / "elsewhere"
    original_cwd.mkdir()
    monkeypatch.chdir(original_cwd)

    assert {icon_id for icon_id, _ in APPLICATION_ICONS} == {"optical_burst", "prism_spectrum"}
    for icon_id, _ in APPLICATION_ICONS:
        icon = application_icon(icon_id)
        assert not icon.isNull()
        available_sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
        assert {(size, size) for size in APPLICATION_ICON_SIZES} <= available_sizes
        for size in APPLICATION_ICON_SIZES:
            pixmap = icon.pixmap(QSize(size, size))
            assert not pixmap.isNull()
            image = pixmap.toImage()
            assert image.pixelColor(0, 0).alpha() == 0


def test_unknown_application_icon_uses_optical_burst_fallback(qapp):
    assert application_icon("unknown") is application_icon("optical_burst")


def test_application_icon_png_resources_are_registered(qapp):
    for icon_id, _ in APPLICATION_ICONS:
        for size in APPLICATION_ICON_SIZES:
            resource = QFile(f":/icons/app/{icon_id}/{size}.png")
            assert resource.open(QIODevice.OpenModeFlag.ReadOnly)
            try:
                assert not resource.readAll().isEmpty()
            finally:
                resource.close()


def test_main_window_icon_selector_switches_and_restores_preference(qapp, qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    path = tmp_path / "appearance.ini"
    backend = QSettings(str(path), QSettings.Format.IniFormat)
    settings = AppSettings(backend)
    settings.set_application_icon("prism_spectrum")

    config = AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "simulation"}})
    window = MainWindow(config, settings=settings)
    qtbot.addWidget(window)
    app = QApplication.instance()
    assert app is not None

    assert window.application_icon_menu.objectName() == "applicationIconMenu"
    assert set(window.application_icon_actions) == {"optical_burst", "prism_spectrum"}
    assert window.application_icon_action_group.isExclusive()
    assert all(
        action.isCheckable() and not action.icon().isNull() for action in window.application_icon_actions.values()
    )
    assert window.application_icon_actions["prism_spectrum"].isChecked()
    assert not window.application_icon_actions["optical_burst"].isChecked()
    assert window.windowIcon().cacheKey() == application_icon("prism_spectrum").cacheKey()
    assert app.windowIcon().cacheKey() == application_icon("prism_spectrum").cacheKey()

    window.application_icon_actions["optical_burst"].trigger()
    assert window.application_icon_actions["optical_burst"].isChecked()
    assert not window.application_icon_actions["prism_spectrum"].isChecked()
    assert window._application_icon_id == "optical_burst"
    assert settings.application_icon() == "optical_burst"
    assert AppSettings(QSettings(str(path), QSettings.Format.IniFormat)).application_icon() == "optical_burst"
    assert app.windowIcon().cacheKey() == application_icon("optical_burst").cacheKey()
    window.application_icon_actions["optical_burst"].trigger()
    assert window.application_icon_actions["optical_burst"].isChecked()
    assert settings.application_icon() == "optical_burst"

    window.application_icon_actions["prism_spectrum"].trigger()
    assert settings.application_icon() == "prism_spectrum"
    window.close()

    restored_settings = AppSettings(QSettings(str(path), QSettings.Format.IniFormat))
    restored_window = MainWindow(config, settings=restored_settings)
    qtbot.addWidget(restored_window)
    assert restored_window.application_icon_actions["prism_spectrum"].isChecked()
    assert restored_window.windowIcon().cacheKey() == application_icon("prism_spectrum").cacheKey()
    restored_window.close()
