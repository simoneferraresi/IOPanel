"""One owned Qt application and explicit deferred destruction between tests."""

import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QThread, QThreadPool


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


@pytest.fixture(autouse=True)
def qt_test_scope(qapp, qt_application_lifetime, tmp_path):
    # Default AppSettings() must never read/write the operator's registry.
    original_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
        QSettings.setPath(QSettings.Format.IniFormat, scope, str(tmp_path / "qt-settings"))
    yield
    # pytest-qt closes registered widgets and posts deleteLater before fixture
    # finalization. processEvents alone does not drain DeferredDelete here.
    _drain_qt(qapp)
    QSettings.setDefaultFormat(original_format)
