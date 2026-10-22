import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from tools.qt_test_lifecycle import native_window_roots, release_native_windows


def test_scope_destroys_unregistered_native_window(qapp, qtbot):
    previous = native_window_roots(qapp)
    widget = QWidget()
    widget.show()
    release_native_windows(qapp, previous, qtbot)
    assert not shiboken6.isValid(widget)


def test_scope_waits_for_accepted_asynchronous_close(qapp, qtbot):
    previous = native_window_roots(qapp)
    states = []

    class DeferredWidget(QWidget):
        ready = False

        def closeEvent(self, event):
            if not self.ready:
                states.append("deferred")
                QTimer.singleShot(0, self, self.finish)
                event.ignore()
            else:
                states.append("accepted")
                event.accept()

        def finish(self):
            assert shiboken6.isValid(self)
            self.ready = True

    widget = DeferredWidget()
    release_native_windows(qapp, previous, qtbot)
    assert states[0] == "deferred"
    assert states[-1] == "accepted"
    assert not shiboken6.isValid(widget)


def test_registered_window_is_destroyed_before_fixture_finalizers(qtbot, request):
    callbacks = []

    class DeferredWidget(QWidget):
        ready = False

        def closeEvent(self, event):
            if not self.ready:
                QTimer.singleShot(0, self, self.finish)
                event.ignore()
            else:
                event.accept()

        def finish(self):
            self.ready = True

    widget = DeferredWidget()
    qtbot.addWidget(widget, before_close_func=lambda target: callbacks.append(target))

    def verify_native_teardown():
        assert callbacks == [widget]
        assert not shiboken6.isValid(widget)

    # Keep the Python wrapper alive past the actual pytest-qt teardown hook.
    request.addfinalizer(verify_native_teardown)


def test_registered_invalid_wrapper_is_safe_during_teardown(qapp, qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.deleteLater()
    release_native_windows(qapp, (), qtbot)
    assert not shiboken6.isValid(widget)
