"""Load bundled Geist fonts and provide small, consistent font helpers."""

from __future__ import annotations

import logging

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from resources import resources_rc  # noqa: F401  # Register the embedded font data.

logger = logging.getLogger("LabApp.Typography")

GEIST_SANS_RESOURCE = ":/fonts/Geist-Variable.ttf"
GEIST_MONO_RESOURCE = ":/fonts/GeistMono-Variable.ttf"

_FONT_FAMILIES: dict[str, str | None] = {"sans": None, "mono": None}
_FONT_IDS: list[int] = []


def _load_application_font(resource_path: str) -> str | None:
    resource = QFile(resource_path)
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        logger.warning("Could not open bundled font resource %s; keeping Qt's fallback font.", resource_path)
        return None
    try:
        font_id = QFontDatabase.addApplicationFontFromData(resource.readAll())
    finally:
        resource.close()

    if font_id < 0:
        logger.warning("Could not register bundled font resource %s; keeping Qt's fallback font.", resource_path)
        return None

    _FONT_IDS.append(font_id)
    families = QFontDatabase.applicationFontFamilies(font_id)
    if not families:
        logger.warning("Bundled font resource %s reported no font families.", resource_path)
        return None
    return families[0]


def install_application_fonts(app: QApplication) -> tuple[str | None, str | None]:
    """Register bundled families and set Geist Sans without changing point size."""
    if _FONT_FAMILIES["sans"] is None:
        _FONT_FAMILIES["sans"] = _load_application_font(GEIST_SANS_RESOURCE)
        if _FONT_FAMILIES["sans"] is None:
            logger.warning("Geist Sans is unavailable; keeping Qt's existing application font.")
    if _FONT_FAMILIES["mono"] is None:
        _FONT_FAMILIES["mono"] = _load_application_font(GEIST_MONO_RESOURCE)
        if _FONT_FAMILIES["mono"] is None:
            logger.warning("Geist Mono is unavailable; keeping Qt's existing font for numeric readouts.")

    sans_family = _FONT_FAMILIES["sans"]
    if sans_family:
        application_font = app.font()
        application_font.setFamily(sans_family)
        app.setFont(application_font)
    return sans_family, _FONT_FAMILIES["mono"]


def make_font(role: str, point_size: int, weight: QFont.Weight | int | None = None) -> QFont:
    """Build a sized font, falling back to Qt's application font if unavailable."""
    font = QFont()
    font.setPointSize(point_size)
    family = _FONT_FAMILIES.get(role)
    if family:
        font.setFamily(family)
    if weight is not None:
        font.setWeight(weight)
    return font
