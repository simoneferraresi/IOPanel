"""Native ownership boundaries, including teardown after the last assertion."""

from threading import Event, get_ident

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, Qt, QThread, QTimer, Slot

from config_model import AppConfig, CameraConfig
from hardware import camera_init_worker
from logic.task_runner import BaseWorker, TaskRunner
from ui.camera_widgets import CameraPanel
from ui.main_window import MainWindow


class FinishingWorker(BaseWorker):
    @Slot()
    def run(self):
        self.finished.emit()


@pytest.mark.parametrize("outcome", ["failed", "cancel-before-open", "cancel-after-open", "exception"])
def test_unadopted_camera_native_destruction_returns_to_gui(qtbot, monkeypatch, outcome):
    gui_thread = get_ident()
    destroyed_on = []

    class FailedCamera(QObject):
        def __init__(self, **kwargs):
            super().__init__()
            self.destroyed.connect(lambda: destroyed_on.append(get_ident()), Qt.ConnectionType.DirectConnection)
            if outcome == "cancel-before-open":
                worker.cancel()

        def open(self):
            if outcome == "exception":
                raise RuntimeError("simulated open failure")
            if outcome == "cancel-after-open":
                worker.cancel()
                return True
            return False

        def close(self):
            pass

    monkeypatch.setattr(camera_init_worker, "VimbaCam", FailedCamera)
    config = CameraConfig(identifier="failed-camera", name="Failed camera", backend="vimba")
    worker = camera_init_worker.CameraInitWorker(config.identifier, config)
    task = TaskRunner(worker)
    task.start()
    qtbot.waitUntil(lambda: bool(destroyed_on))
    assert destroyed_on == [gui_thread]


def test_task_worker_native_destruction_runs_on_owning_gui_thread(qtbot, qapp):
    worker = FinishingWorker()
    destroyed_on = []
    worker.destroyed.connect(
        lambda: destroyed_on.append(QThread.currentThread() == qapp.thread()), Qt.ConnectionType.DirectConnection
    )
    task = TaskRunner(worker)
    task.start()
    qtbot.waitUntil(lambda: bool(destroyed_on))
    assert destroyed_on == [True]


def test_task_runner_has_native_owner_until_deferred_thread_destruction(qapp):
    task = TaskRunner(FinishingWorker())
    assert task.parent() is not None
    assert task.worker_thread.parent() is task
    assert task.worker.parent() is task
    assert task.worker.thread() == qapp.thread()
    task.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_stopped_worker_does_not_replay_queued_hardware_commands_on_gui(qtbot, qapp):
    entered = Event()
    release = Event()
    calls = []

    class GatedWorker(BaseWorker):
        @Slot()
        def run(self):
            entered.set()
            try:
                assert release.wait(3)
            finally:
                self.finished.emit()

        @Slot()
        def command(self):
            calls.append(QThread.currentThread() == qapp.thread())

    worker = GatedWorker()
    task = TaskRunner(worker)
    destroyed = []
    worker.destroyed.connect(lambda: destroyed.append(True))
    task.start()
    try:
        assert entered.wait(2)
        assert QMetaObject.invokeMethod(worker, "command", Qt.ConnectionType.QueuedConnection)
    finally:
        release.set()
    qtbot.waitUntil(lambda: bool(destroyed))
    assert calls == []


def test_feedback_callback_is_cancelled_by_native_panel_destruction(qtbot, monkeypatch):
    panel = CameraPanel(None, "Feedback", CameraConfig(identifier="feedback", name="Feedback"))
    original = QTimer.singleShot
    # Advance only the feedback deadline, retaining Qt's real context and
    # queued callback behavior; no wall-clock sleep or fake callable delivery.
    monkeypatch.setattr(QTimer, "singleShot", lambda delay, *args: original(0, *args))
    panel.handle_auto_result("auto_exposure", 100)
    panel.close()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(panel)
    QCoreApplication.processEvents()


def test_retired_worker_timers_cannot_restart_hardware_work_on_gui(qtbot, qapp):
    calls = []

    class TimerWorker(FinishingWorker):
        def __init__(self):
            super().__init__()
            self.timer = QTimer(self)
            self.timer.timeout.connect(lambda: calls.append(True))

        @Slot()
        def run(self):
            self.timer.start(1)
            self.finished.emit()

    worker = TimerWorker()
    task = TaskRunner(worker)
    retired = []
    task.worker_thread.finished.connect(
        lambda: retired.append((worker.timer.isActive(), worker.timer.thread() == qapp.thread())),
        Qt.ConnectionType.QueuedConnection,
    )
    task.start()
    qtbot.waitUntil(lambda: bool(retired))
    assert retired == [(False, True)]
    assert calls == []


def _config():
    return AppConfig(
        instruments={"ct400_backend": "simulation"},
        cameras={
            key: CameraConfig(identifier=key, enabled=True, name=key, backend="simulation")
            for key in ("first", "second")
        },
    )


def test_close_before_startup_timer_cannot_initialize_hardware(qtbot, monkeypatch):
    starts = []
    monkeypatch.setattr(MainWindow, "_init_ct400_lazy", lambda self: starts.append("ct400"))
    monkeypatch.setattr(MainWindow, "_init_cameras_lazy", lambda self: starts.append("camera"))
    window = MainWindow(_config())
    qtbot.addWidget(window)
    assert window.close()
    window._begin_lazy_init()
    assert starts == []


def test_all_camera_placeholders_exist_before_first_worker_starts(qtbot, monkeypatch):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda self: None)
    window = MainWindow(_config())
    qtbot.addWidget(window)
    panels_at_start = []
    monkeypatch.setattr(TaskRunner, "start", lambda task: panels_at_start.append(set(window.camera_panels)))
    window._init_cameras_lazy()
    assert panels_at_start == [{"first", "second"}, {"first", "second"}]
    # These workers deliberately never started: release the test's bookkeeping.
    window._init_tasks.clear()
    window.close()
