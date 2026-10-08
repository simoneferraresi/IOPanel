from __future__ import annotations

import pytest
from PySide6.QtCore import QFile, QIODevice, QSettings, QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from app_settings import AppSettings
from config_model import AppConfig
from ui.appearance_dialog import AppearanceDialog
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


def test_appearance_dialog_cards_preview_and_confirm_selection(qapp, qtbot):
    dialog = AppearanceDialog("prism_spectrum")
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Appearance"
    assert dialog.accessibleName() == "Appearance settings"
    heading = dialog.findChild(type(dialog.card_labels["optical_burst"]), "appearanceDialogHeading")
    assert heading is not None
    assert heading.text() == "Choose the application icon"
    assert dialog.findChild(type(heading), "applicationIconSectionTitle") is None
    assert dialog.findChild(type(heading), "appearanceDialogTitle") is None
    assert dialog.findChild(type(heading), "appearanceDialogDescription").text() == (
        "This changes the icon displayed while IOPanel is running."
    )
    assert (
        len([label for label in dialog.findChildren(type(heading)) if label.text() == "Choose the application icon"])
        == 1
    )
    assert dialog.minimumSize().height() >= dialog.layout().sizeHint().height()
    assert dialog.selected_icon_id == "prism_spectrum"
    assert dialog.selection_buttons["prism_spectrum"].isChecked()
    assert not dialog.selection_buttons["optical_burst"].isChecked()
    assert dialog.selection_indicators["prism_spectrum"].isChecked()
    assert not dialog.selection_indicators["optical_burst"].isChecked()
    assert dialog.card_labels["prism_spectrum"].text() == "Prism Spectrum"
    dialog.show()
    qtbot.wait(10)
    card_sizes = {button.size() for button in dialog.selection_buttons.values()}
    assert len(card_sizes) == 1
    card_geometries = {icon_id: button.geometry() for icon_id, button in dialog.selection_buttons.items()}
    for icon_id, display_name in APPLICATION_ICONS:
        button = dialog.selection_buttons[icon_id]
        assert button.isCheckable()
        assert button.accessibleName() == display_name
        assert button.accessibleDescription() == (
            "Currently selected" if icon_id == "prism_spectrum" else "Not selected"
        )
        assert button.toolTip()
        preview = dialog.card_previews[icon_id]
        assert preview.size() == QSize(dialog.PREVIEW_SIZE, dialog.PREVIEW_SIZE)
        assert not preview.pixmap().isNull()
        assert preview.pixmap().toImage().pixelColor(0, 0).alpha() == 0
        label_row = dialog.card_label_rows[icon_id]
        assert label_row is not None
        assert abs(preview.geometry().center().x() - button.rect().center().x()) <= 1
        assert abs(label_row.geometry().center().x() - button.rect().center().x()) <= 1
        label_gap = label_row.geometry().top() - preview.geometry().bottom() - 1
        assert 12 <= label_gap <= 20
        assert "QToolButton:checked" in button.styleSheet()
        assert "keyboardFocus='true'" in dialog.card_label_rows[icon_id].styleSheet()

    dialog.selection_buttons["optical_burst"].click()
    assert dialog.selected_icon_id == "optical_burst"
    assert dialog.selection_buttons["optical_burst"].isChecked()
    assert not dialog.selection_buttons["prism_spectrum"].isChecked()
    assert dialog.card_labels["optical_burst"].text() == "Optical Burst"
    assert dialog.card_labels["prism_spectrum"].text() == "Prism Spectrum"
    assert dialog.selection_indicators["optical_burst"].isChecked()
    assert not dialog.selection_indicators["prism_spectrum"].isChecked()
    assert {icon_id: button.geometry() for icon_id, button in dialog.selection_buttons.items()} == card_geometries
    dialog.apply_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_icon_id == "optical_burst"


def test_appearance_dialog_supports_keyboard_navigation(qapp, qtbot):
    dialog = AppearanceDialog("optical_burst")
    qtbot.addWidget(dialog)
    dialog.show()
    optical = dialog.selection_buttons["optical_burst"]
    prism = dialog.selection_buttons["prism_spectrum"]
    optical.setFocus()
    assert dialog.card_label_rows["optical_burst"].property("keyboardFocus") is True
    qtbot.keyClick(optical, Qt.Key.Key_Tab)
    assert dialog.focusWidget() is prism
    qtbot.waitUntil(lambda: dialog.card_label_rows["optical_burst"].property("keyboardFocus") is False)
    assert dialog.card_label_rows["prism_spectrum"].property("keyboardFocus") is True
    assert prism.focusPolicy() != Qt.FocusPolicy.NoFocus
    qtbot.keyClick(prism, Qt.Key.Key_Space)
    assert prism.isChecked()
    assert dialog.selected_icon_id == "prism_spectrum"


@pytest.mark.parametrize("dismissal", ["cancel", "escape", "close"])
def test_appearance_dialog_dismissal_discards_provisional_choice(qapp, qtbot, dismissal):
    dialog = AppearanceDialog("optical_burst")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.selection_buttons["prism_spectrum"].click()
    assert dialog.selected_icon_id == "prism_spectrum"

    if dismissal == "cancel":
        dialog.cancel_button.click()
    elif dismissal == "escape":
        qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.close()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.selected_icon_id == "prism_spectrum"


def test_view_menu_dialog_applies_only_after_accept_and_restores_current_choice(qapp, qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _self: None)
    path = tmp_path / "appearance.ini"
    backend = QSettings(str(path), QSettings.Format.IniFormat)
    settings = AppSettings(backend)
    settings.set_application_icon("optical_burst")

    config = AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "simulation"}})
    window = MainWindow(config, settings=settings)
    qtbot.addWidget(window)
    app = QApplication.instance()
    assert app is not None

    view_menu = next(action.menu() for action in window.menuBar().actions() if action.text().replace("&", "") == "View")
    assert isinstance(view_menu, QMenu)
    assert [action.text() for action in view_menu.actions()] == ["Application Icon…", "Log Console"]
    assert window.application_icon_action.objectName() == "applicationIconAction"
    assert window.windowIcon().cacheKey() == application_icon("optical_burst").cacheKey()
    assert app.windowIcon().cacheKey() == application_icon("optical_burst").cacheKey()

    opened_dialogs = []

    def cancel_dialog(dialog):
        opened_dialogs.append(dialog)
        assert dialog.selected_icon_id == "optical_burst"
        dialog.selection_buttons["prism_spectrum"].click()
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(AppearanceDialog, "exec", cancel_dialog)
    window.application_icon_action.trigger()
    assert len(opened_dialogs) == 1
    assert settings.application_icon() == "optical_burst"
    assert window._application_icon_id == "optical_burst"
    assert app.windowIcon().cacheKey() == application_icon("optical_burst").cacheKey()

    def apply_dialog(dialog):
        opened_dialogs.append(dialog)
        assert dialog.selected_icon_id == "optical_burst"
        dialog.selection_buttons["prism_spectrum"].click()
        dialog.apply_button.click()
        return dialog.result()

    monkeypatch.setattr(AppearanceDialog, "exec", apply_dialog)
    window.application_icon_action.trigger()
    assert settings.application_icon() == "prism_spectrum"
    assert window._application_icon_id == "prism_spectrum"
    assert window.windowIcon().cacheKey() == application_icon("prism_spectrum").cacheKey()
    assert app.windowIcon().cacheKey() == application_icon("prism_spectrum").cacheKey()

    restored_settings = AppSettings(QSettings(str(path), QSettings.Format.IniFormat))
    restored_window = MainWindow(config, settings=restored_settings)
    qtbot.addWidget(restored_window)
    assert restored_window._application_icon_id == "prism_spectrum"
    assert restored_window.windowIcon().cacheKey() == application_icon("prism_spectrum").cacheKey()
    window.close()
    restored_window.close()
