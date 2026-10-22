"""One owned Qt application and explicit deferred destruction between tests."""

import gc

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QThread, QThreadPool
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from tools.qt_test_lifecycle import close_native_widget, native_window_roots, release_native_windows


def _drain_qt(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime(qapp):
    # Qt helpers must reuse pytest-qt's application. Creating a temporary app
    # before qapp, then leaving cyclic widgets for PySide's atexit visitor,
    # reproduced post-summary native heap failures in the camera characterizer.
    yield
    assert QThreadPool.globalInstance().waitForDone(5000), "Qt pool work survived the test session"
    _drain_qt(qapp)
    gc.collect()
    _drain_qt(qapp)
    active = [thread.objectName() for thread in qapp.findChildren(QThread) if thread.isRunning()]
    assert not active, f"Native QThreads survived the test session: {active}"
    assert not native_window_roots(qapp), "Python-owned native windows survived the test session"


@pytest.fixture(autouse=True)
def qt_test_scope(qapp, qt_application_lifetime, tmp_path, request):
    previous_windows = native_window_roots(qapp)
    # Default AppSettings() must never read/write the operator's registry.
    original_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(tmp_path / "qt-settings"))
    yield
    # pytest-qt closes registered widgets and posts deleteLater before fixture
    # finalization. processEvents alone does not drain DeferredDelete here.
    release_native_windows(qapp, previous_windows, QtBot(request))
    QSettings.setDefaultFormat(original_format)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item):
    # Run before pytest-qt's unconditional close/delete pass. A deferred close
    # can finish by destroying the widget itself, so check validity again after
    # waiting instead of calling close on an already invalid native wrapper.
    app = QApplication.instance()
    if app is not None:
        bot = QtBot(item._request)
        registrations = getattr(item, "qt_widgets", ())
        # Consume these registrations: pytest-qt's pass checks only whether
        # the Python weakref exists, not whether its C++ widget still exists.
        # Retained Python wrappers are expected after our native teardown.
        item.qt_widgets = []
        for reference, before_close in registrations:
            widget = reference()
            if widget is None or not shiboken6.isValid(widget):
                continue
            if before_close is not None:
                before_close(widget)
            if shiboken6.isValid(widget):
                close_native_widget(widget, bot)
            if shiboken6.isValid(widget):
                widget.deleteLater()
        _drain_qt(app)
    yield
