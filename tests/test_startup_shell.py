"""Hardware-free checks for the earliest responsive IOPanel startup shell."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMainWindow
from shiboken6 import isValid

from logic.startup_coordinator import MainWindowStartupCoordinator


def test_startup_shell_pumps_qt_events_while_full_ui_import_is_blocked(qtbot):
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    original_quit = application.quitOnLastWindowClosed()
    started = threading.Event()
    release = threading.Event()
    imported_thread_ids = []
    built = []
    failures = []
    window = None

    def slow_importer():
        imported_thread_ids.append(threading.get_ident())
        started.set()
        assert release.wait(5), "test did not release import worker"
        return QMainWindow

    def on_loaded(window_class):
        nonlocal window
        assert threading.get_ident() != imported_thread_ids[0]
        window = window_class()
        window.show()
        built.append(window)

    startup = MainWindowStartupCoordinator(
        application,
        importer=slow_importer,
        on_loaded=on_loaded,
        on_failure=failures.append,
        logger=logging.getLogger("LabApp.test_startup"),
    )
    try:
        startup.start()
        qtbot.waitUntil(started.is_set, timeout=2000)
        assert startup.shell.isVisible()
        assert not built
        assert imported_thread_ids[0] != threading.get_ident()
        callbacks = []
        QTimer.singleShot(25, lambda: callbacks.append(True))
        qtbot.waitUntil(lambda: bool(callbacks), timeout=1000)
        assert startup.shell.isVisible()
        release.set()
        qtbot.waitUntil(lambda: bool(built), timeout=3000)
        assert not isValid(startup.shell) or not startup.shell.isVisible()
        assert not failures
        assert application.quitOnLastWindowClosed()
    finally:
        release.set()
        if window is not None:
            window.close()
        application.setQuitOnLastWindowClosed(original_quit)


def test_cancel_during_startup_defers_close_until_import_thread_retires(qtbot):
    application = QApplication.instance()
    assert isinstance(application, QApplication)
    original_quit = application.quitOnLastWindowClosed()
    started = threading.Event()
    release = threading.Event()
    built = []

    def importer():
        started.set()
        assert release.wait(5), "test did not release import worker"
        return QMainWindow

    startup = MainWindowStartupCoordinator(
        application,
        importer=importer,
        on_loaded=built.append,
        on_failure=lambda error: built.append(error),
        logger=logging.getLogger("LabApp.test_startup"),
    )
    try:
        startup.start()
        qtbot.waitUntil(started.is_set, timeout=2000)
        assert startup.shell.close()
        assert not built
        assert startup._task is not None
        release.set()
        qtbot.waitUntil(lambda: startup._task is None, timeout=3000)
        assert not built
    finally:
        release.set()
        application.setQuitOnLastWindowClosed(original_quit)
