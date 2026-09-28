"""Regression tests for the generated application Qt Style Sheet."""

from __future__ import annotations

import re

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QPushButton, QWidget

from ui.theme import APP_STYLESHEET, _theme, get_app_stylesheet


def test_application_stylesheet_parses_without_qt_warning(qapp) -> None:
    messages: list[str] = []
    previous_handler = qInstallMessageHandler(lambda _type, _context, message: messages.append(message))
    previous_stylesheet = qapp.styleSheet()
    root = QWidget()
    button = QPushButton("Parse stylesheet", root)

    try:
        qapp.setStyleSheet(APP_STYLESHEET)
        root.show()
        root.ensurePolished()
        button.ensurePolished()
        qapp.processEvents()
    finally:
        root.close()
        qapp.setStyleSheet(previous_stylesheet)
        qapp.processEvents()
        qInstallMessageHandler(previous_handler)

    parse_warnings = [message for message in messages if "Could not parse application stylesheet" in message]
    assert not parse_warnings, "\n".join(parse_warnings)


def test_light_and_dark_application_stylesheets_are_generated() -> None:
    try:
        _theme.set_dark_mode(False)
        light_stylesheet = get_app_stylesheet()
        _theme.set_dark_mode(True)
        dark_stylesheet = get_app_stylesheet()
    finally:
        _theme.set_dark_mode(False)

    for stylesheet in (light_stylesheet, dark_stylesheet):
        assert ":root" not in stylesheet
        assert re.search(r"(?m)^\s*--[\w-]+\s*:", stylesheet) is None

    assert "background-color: #ffffff;" in light_stylesheet
    assert "background-color: #1e1e1e;" in dark_stylesheet
