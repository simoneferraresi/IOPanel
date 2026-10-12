"""Navigation order and menu conventions must not activate physical devices."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

from app_settings import AppSettings
from config_model import AppConfig
from ui.main_window import MainWindow


def _make_window(qtbot, monkeypatch, settings):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    window = MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}), settings=settings)
    qtbot.addWidget(window)
    return window


def test_fresh_window_uses_monitor_scan_alignment_order(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "new.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    assert [window.tab_widget.tabText(i) for i in range(window.tab_widget.count())] == [
        "Power Monitor",
        "Wavelength Scan",
        "Auto Alignment",
    ]
    assert window.tab_widget.currentIndex() == 0
    assert window.tab_widget.widget(0) is window.second_tab
    assert window.tab_widget.widget(1) is window.first_tab
    assert window.tab_widget.widget(2) is window.alignment_tab
    window.close()


@pytest.mark.parametrize(("legacy_index", "expected_index"), [(0, 1), (1, 0), (2, 2)])
def test_restored_legacy_tab_selects_same_logical_page(qtbot, monkeypatch, tmp_path, legacy_index, expected_index):
    backend = QSettings(str(tmp_path / "previous.ini"), QSettings.Format.IniFormat)
    backend.setValue("MainWindow/active_tab", legacy_index)
    settings = AppSettings(backend)
    window = _make_window(qtbot, monkeypatch, settings)
    assert window.tab_widget.currentIndex() == expected_index
    window.close()
    settings.sync()
    reloaded = AppSettings(QSettings(str(tmp_path / "previous.ini"), QSettings.Format.IniFormat))
    assert reloaded.active_tab(3) == expected_index
    assert reloaded._settings.value("MainWindow/active_tab_id") == AppSettings.ACTIVE_TAB_IDS[expected_index]


def test_menu_order_and_action_placement(qtbot, monkeypatch, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "menus.ini"), QSettings.Format.IniFormat))
    window = _make_window(qtbot, monkeypatch, settings)
    menus = {action.text().replace("&", ""): action.menu() for action in window.menuBar().actions()}
    assert list(menus) == ["File", "View", "Instruments", "Cameras", "Help"]

    def labels(name):
        return [action.text() for action in menus[name].actions() if not action.isSeparator()]

    assert labels("File") == ["Open Scan...", "Add Scan to Plot...", "E&xit"]
    assert labels("View") == ["Application Icon…", "Log Console"]
    assert "CT400 ETA Calibration…" in labels("Instruments")
    assert "CT400 ETA Calibration…" not in labels("File")
    assert labels("Cameras") == ["Discover Cameras..."]
    assert labels("Help") == ["&About"]
    assert not menus["Cameras"].actions()[0].isSeparator()
    assert not menus["Cameras"].actions()[-1].isSeparator()
    calibration = next(
        action for action in menus["Instruments"].actions() if action.objectName() == "ct400EtaCalibrationAction"
    )
    assert calibration.text() == "CT400 ETA Calibration…"
    assert window.log_console_action.shortcut() == QKeySequence("F12")
    assert window.log_console_action.isCheckable()
    window.close()
