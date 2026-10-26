import logging

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject, Qt, QThread, Signal, Slot
from shiboken6 import isValid

from logic.qt_worker_lifecycle import retire_worker_on_thread_finish

logger = logging.getLogger("LabApp.TaskRunner")


class BaseWorker(QObject):
    """
    Standard interface for all background workers.
    Ensures consistent signal naming for the TaskRunner to hook into.
    """

    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    @Slot()
    def run(self) -> None:
        """
        The main entry point for the worker's logic.
        Subclasses should override this if they are 'one-shot' tasks.
        """


class TaskRunner(QObject):
    """
    Manages the lifecycle of a QThread and a Worker.
    Handles the boilerplate of moveToThread, starting, and cleaning up.
    """

    # Signal to re-emit errors from the worker to the main thread convenience
    error_occurred = Signal(str)
    stopped = Signal()

    def __init__(self, worker: BaseWorker, auto_start_run: bool = True) -> None:
        """
        Args:
            worker: An instance of a class inheriting from BaseWorker.
            auto_start_run: If True, queue worker.run() on the moved worker after
                            starting the thread. Set False for service workers
                            that wait for signals.
        """
        # Native ownership must survive a caller dropping its Python reference
        # in a queued finished callback. A runner retires with its QThread.
        super().__init__(QCoreApplication.instance())
        self.worker = worker
        self.auto_start_run = auto_start_run
        self._stop_requested = False
        self._started = False
        self.worker_thread: QThread = QThread(self)

        # An unstarted task has no worker event loop to drain deleteLater.
        # Keep its worker GUI-owned until execution actually starts.
        self.worker.setParent(self)

        # 2. Connect Lifecycle Signals
        # When the worker says it's finished, quit the thread loop
        self.worker.finished.connect(self.worker_thread.quit, Qt.ConnectionType.DirectConnection)

        # QObject destruction calls virtual disconnectNotify in PySide. Doing
        # that in a native worker thread can hold Qt's connection mutex while
        # waiting for the GIL, as the GUI holds the GIL while connecting objects.
        # Return the stopped worker before requesting GUI deferred destruction.
        retire_worker_on_thread_finish(self.worker_thread, self.worker)
        self.worker_thread.finished.connect(self._on_thread_finished)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.destroyed.connect(self.deleteLater)

        # 3. Error Forwarding (Optional, but useful)
        self.worker.error.connect(self.error_occurred)

    def start(self) -> None:
        """Starts the background thread."""
        if self._stop_requested:
            return
        self._started = True
        logger.debug(f"Starting thread for worker: {self.worker.__class__.__name__}")
        self.worker.setParent(None)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.start()
        if self.auto_start_run:
            QMetaObject.invokeMethod(self.worker, "run", Qt.ConnectionType.QueuedConnection)

    def stop(self) -> None:
        """Request cooperative exit and return; stopped follows native retirement.

        Owners must retain this runner until stopped. quit cannot interrupt a
        synchronous native call; workers may observe the interruption request.
        Service-specific hardware cleanup must be requested before calling stop.
        """
        if self._stop_requested:
            return
        self._stop_requested = True
        if not self._started:
            self.stopped.emit()
            return
        if isValid(self.worker_thread) and self.worker_thread.isRunning():
            self.worker_thread.requestInterruption()
            self.worker_thread.quit()

    @Slot()
    def _on_thread_finished(self) -> None:
        self.stopped.emit()
