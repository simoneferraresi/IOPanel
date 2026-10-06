"""Load bundled Geist fonts and provide small, consistent font helpers."""

from __future__ import annotations

import logging

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QGroupBox

from resources import resources_rc  # noqa: F401  # Register the embedded font data.

logger = logging.getLogger("LabApp.Typography")

GEIST_SANS_RESOURCE = ":/fonts/Geist-Variable.ttf"
GEIST_MONO_RESOURCE = ":/fonts/GeistMono-Variable.ttf"
APPLICATION_MIN_POINT_SIZE = 10.0
PLOT_TITLE_POINT_SIZE = 12
GROUP_BOX_TITLE_STYLE = "QGroupBox { font-weight: bold; }"

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
    """Register bundled families and keep the Geist Sans baseline at least 10 pt."""
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
        current_point_size = application_font.pointSizeF()
        if current_point_size <= 0 or current_point_size < APPLICATION_MIN_POINT_SIZE:
            application_font.setPointSizeF(APPLICATION_MIN_POINT_SIZE)
        app.setFont(application_font)
    return sans_family, _FONT_FAMILIES["mono"]


def style_group_box_title(group_box: QGroupBox) -> None:
    """Render the group caption bold without propagating bold to child widgets."""
    group_box.setStyleSheet(f"{group_box.styleSheet()}\n{GROUP_BOX_TITLE_STYLE}".strip())


def pyqtgraph_title_style(color: str = "black") -> dict[str, object]:
    """Return the shared styling for all plot titles."""
    style: dict[str, object] = {"color": color, "size": f"{PLOT_TITLE_POINT_SIZE}pt", "bold": True}
    if _FONT_FAMILIES["sans"]:
        style["family"] = _FONT_FAMILIES["sans"]
    return style


def plot_title_font() -> QFont:
    """Return the canonical Geist Sans font used by non-PyQtGraph plot titles."""
    font = make_font("sans", PLOT_TITLE_POINT_SIZE, QFont.Weight.Bold)
    return font


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
