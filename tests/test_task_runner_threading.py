import threading

from PySide6.QtCore import QCoreApplication, QMetaObject, Qt, QThread, Signal, Slot
from PySide6.QtTest import QSignalSpy

from logic.task_runner import BaseWorker, TaskRunner


class ThreadProbeWorker(BaseWorker):
    entered = Signal(object, object, int, str)
    service_stopped = Signal(object, object, int)

    def __init__(self, gate=None):
        super().__init__()
        self.gate = gate
        self.run_count = 0

    @Slot()
    def run(self):
        self.run_count += 1
        current = QThread.currentThread()
        affinity = self.thread()
        self.entered.emit(current, affinity, threading.get_ident(), current.objectName())
        if self.gate is not None:
            self.gate.wait(timeout=3)
        self.finished.emit()

    @Slot()
    def stop_service(self):
        self.service_stopped.emit(QThread.currentThread(), self.thread(), threading.get_ident())
        self.finished.emit()


def test_worker_run_executes_in_taskrunner_thread(qtbot):
    app = QCoreApplication.instance()
    gui_thread = app.thread()
    gui_python_ident = threading.get_ident()
    worker = ThreadProbeWorker()
    entered = QSignalSpy(worker.entered)
    task = TaskRunner(worker)
    task.thread.setObjectName("task-runner-execution-probe")
    finished = QSignalSpy(task.thread.finished)

    task.start()
    qtbot.waitUntil(lambda: entered.count() == 1, timeout=2000)
    current_thread, worker_thread, python_ident, thread_name = entered.at(0)

    assert current_thread == task.thread
    assert worker_thread == task.thread
    assert current_thread == worker_thread
    assert current_thread != gui_thread
    assert python_ident != gui_python_ident
    assert thread_name == "task-runner-execution-probe"
    qtbot.waitUntil(lambda: finished.count() == 1, timeout=2000)


def test_worker_finished_precedes_task_thread_finished(qtbot):
    gate = threading.Event()
    worker = ThreadProbeWorker(gate)
    entered = QSignalSpy(worker.entered)
    worker_finished = QSignalSpy(worker.finished)
    task = TaskRunner(worker)
    thread_done = QSignalSpy(task.thread.finished)

    task.start()
    qtbot.waitUntil(lambda: entered.count() == 1, timeout=2000)
    assert worker_finished.count() == 0
    assert thread_done.count() == 0

    gate.set()
    qtbot.waitUntil(lambda: worker_finished.count() == 1, timeout=2000)
    qtbot.waitUntil(lambda: thread_done.count() == 1, timeout=2000)
    assert worker_finished.count() == 1
    assert thread_done.count() == 1


def test_immediate_worker_thread_quits_without_gui_event_processing(qtbot):
    run_returned = threading.Event()

    class ImmediateWorker(BaseWorker):
        @Slot()
        def run(self):
            self.finished.emit()
            run_returned.set()

    worker = ImmediateWorker()
    task = TaskRunner(worker)
    thread_finished = QSignalSpy(task.thread.finished)

    task.start()
    assert run_returned.wait(timeout=2)
    terminated_without_gui_events = task.thread.wait(2000)

    if not terminated_without_gui_events:
        # Let the normal queued quit/cleanup path complete so the failing
        # regression cannot leave a live QThread behind.
        qtbot.waitUntil(lambda: thread_finished.count() == 1, timeout=2000)

    assert terminated_without_gui_events, "QThread.quit depended on GUI event processing"
    assert thread_finished.count() == 1
    QCoreApplication.instance().processEvents()


def test_auto_start_run_false_keeps_worker_as_service(qtbot):
    worker = ThreadProbeWorker()
    started = QSignalSpy(worker.entered)
    service_stopped = QSignalSpy(worker.service_stopped)
    task = TaskRunner(worker, auto_start_run=False)
    thread_started = QSignalSpy(task.thread.started)
    thread_finished = QSignalSpy(task.thread.finished)

    task.start()
    qtbot.waitUntil(lambda: thread_started.count() == 1, timeout=2000)
    assert worker.run_count == 0
    assert started.count() == 0

    # A queued service slot proves the event loop is running without invoking
    # run(); it then requests orderly worker/thread completion itself.
    QMetaObject.invokeMethod(worker, "stop_service", Qt.ConnectionType.QueuedConnection)
    qtbot.waitUntil(lambda: service_stopped.count() == 1, timeout=2000)
    current, affinity, python_ident = service_stopped.at(0)
    assert current == task.thread
    assert affinity == task.thread
    assert python_ident != threading.get_ident()
    qtbot.waitUntil(lambda: thread_finished.count() == 1, timeout=2000)
    assert worker.run_count == 0
