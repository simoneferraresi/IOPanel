"""Own Python-created native window graphs for a test or diagnostic scope."""

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QMainWindow, QWidget


def native_window_roots(app):
    return tuple(
        widget
        for widget in app.topLevelWidgets()
        if shiboken6.isValid(widget) and shiboken6.ownedByPython(widget) and widget.parent() is None
    )


def close_native_widget(widget, qtbot):
    # QWidget.close can refuse while a real worker owns resources. pytest-qt
    # 4.5 otherwise posts deleteLater even for that refused close. Wait for the
    # actual accepted close; never destroy a running worker's owning window.
    qtbot.waitUntil(
        lambda: not shiboken6.isValid(widget) or QWidget.close(widget),
        timeout=5000,
    )


def release_native_windows(app, previous, qtbot):
    previous_ids = {id(widget) for widget in previous if shiboken6.isValid(widget)}
    created = [widget for widget in native_window_roots(app) if id(widget) not in previous_ids]
    # Closing the application owner first normally releases its floating menus
    # and dialogs too. Re-check validity before touching the remaining snapshot.
    created.sort(key=lambda widget: isinstance(widget, QMainWindow), reverse=True)
    for widget in created:
        if shiboken6.isValid(widget):
            close_native_widget(widget, qtbot)
            if shiboken6.isValid(widget):
                widget.deleteLater()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
