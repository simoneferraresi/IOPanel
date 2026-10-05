import logging

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Signal, Slot

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

    def __init__(self, worker: BaseWorker, auto_start_run: bool = True) -> None:
        """
        Args:
            worker: An instance of a class inheriting from BaseWorker.
            auto_start_run: If True, queue worker.run() on the moved worker after
                            starting the thread. Set False for service workers
                            that wait for signals.
        """
        super().__init__()
        self.worker = worker
        self.auto_start_run = auto_start_run
        self.worker_thread: QThread = QThread()

        # 1. Move the worker to the new thread
        self.worker.moveToThread(self.worker_thread)

        # 2. Connect Lifecycle Signals
        # When the worker says it's finished, quit the thread loop
        self.worker.finished.connect(self.worker_thread.quit, Qt.ConnectionType.DirectConnection)

        # When the thread loop stops, delete the worker and the thread object
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        # 3. Error Forwarding (Optional, but useful)
        self.worker.error.connect(self.error_occurred)

    def start(self) -> None:
        """Starts the background thread."""
        logger.debug(f"Starting thread for worker: {self.worker.__class__.__name__}")
        self.worker_thread.start()
        if self.auto_start_run:
            QMetaObject.invokeMethod(self.worker, "run", Qt.ConnectionType.QueuedConnection)

    def stop(self) -> None:
        """
        Forcefully asks the thread to stop.
        Note: The worker usually needs its own 'stop()' method to break loops safely.
        """
        if self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait()
