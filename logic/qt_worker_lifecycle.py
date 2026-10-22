"""Return stopped Python QObjects before native destruction.

PySide's QObject virtual hooks acquire the GIL. Destroying a worker in a
background thread can therefore invert Qt's connection-mutex/GIL lock order
against GUI construction. Move the worker from its own thread at finished,
then let the owner's event loop perform deferred deletion. The owner must
retain the thread and worker through shutdown; this does not cancel work.
"""

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, QThread, QTimer, Slot


class _WorkerRetirement(QObject):
    def __init__(self, thread: QThread, worker: QObject, delete: bool) -> None:
        super().__init__(thread)
        self._worker_thread = thread
        self._worker = worker
        self._delete = delete
        app = QCoreApplication.instance()
        # Borrow the GUI thread wrapper from its durable application owner.
        # Borrowing it through a short-lived worker/retirement QObject caused
        # Shiboken to invalidate observers' wrappers when that object died.
        self._destination = app.thread() if app is not None else QThread.currentThread()
        thread.finished.connect(self._return_worker, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(self._dispose_worker, Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _return_worker(self) -> None:
        # The execution loop has stopped. Discard commands already queued to
        # it: moving them to the GUI must not execute hardware work there.
        for timer in self._worker.findChildren(QTimer):
            timer.stop()
        for obj in [self._worker, *self._worker.findChildren(QObject)]:
            QCoreApplication.removePostedEvents(obj, QEvent.Type.MetaCall)
        self._worker.moveToThread(self._destination)

    @Slot()
    def _dispose_worker(self) -> None:
        # finished precedes native thread-local cleanup. Join only here, after
        # that signal, with the GIL released by QThread.wait(). No active
        # operation is cancelled, timed out, or terminated by this helper.
        self._worker_thread.wait()
        self._worker.setParent(self)
        if self._delete:
            self._worker.deleteLater()


def retire_worker_on_thread_finish(thread: QThread, worker: QObject, *, delete: bool = True) -> None:
    """Connect before starting, from the thread that owns the QThread object.

    finished is emitted in the native worker thread. The direct callback may
    push the worker's affinity; a queued GUI callback cannot pull it afterwards.
    The queued completion joins the native thread and establishes parentage
    before deferred destruction. GUI result callbacks already in the queue run
    before that destruction.
    For workers whose owner reads terminal state at finished, delete=False lets
    that owner request deletion after consuming it.
    """
    _WorkerRetirement(thread, worker, delete)
