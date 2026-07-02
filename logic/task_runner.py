import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot

logger = logging.getLogger("LabApp.TaskRunner")


class BaseWorker(QObject):
    """
    Standard interface for all background workers.
    Ensures consistent signal naming for the TaskRunner to hook into.
    """

    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    @Slot()
    def run(self):
        """
        The main entry point for the worker's logic.
        Subclasses should override this if they are 'one-shot' tasks.
        """
        pass


class TaskRunner(QObject):
    """
    Manages the lifecycle of a QThread and a Worker.
    Handles the boilerplate of moveToThread, starting, and cleaning up.
    """

    # Signal to re-emit errors from the worker to the main thread convenience
    error_occurred = Signal(str)

    def __init__(self, worker: BaseWorker, auto_start_run: bool = True):
        """
        Args:
            worker: An instance of a class inheriting from BaseWorker.
            auto_start_run: If True, thread.started is connected to worker.run.
                            Set False for 'Service' workers (like Alignment) that wait for signals.
        """
        super().__init__()
        self.worker = worker
        self.thread = QThread()

        # 1. Move the worker to the new thread
        self.worker.moveToThread(self.thread)

        # 2. Connect Lifecycle Signals
        # When the thread starts, run the worker logic (if auto_start is True)
        if auto_start_run:
            self.thread.started.connect(self.worker.run)

        # When the worker says it's finished, quit the thread loop
        self.worker.finished.connect(self.thread.quit)

        # When the thread loop stops, delete the worker and the thread object
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)

        # 3. Error Forwarding (Optional, but useful)
        self.worker.error.connect(self.error_occurred)

    def start(self):
        """Starts the background thread."""
        logger.debug(f"Starting thread for worker: {self.worker.__class__.__name__}")
        self.thread.start()

    def stop(self):
        """
        Forcefully asks the thread to stop.
        Note: The worker usually needs its own 'stop()' method to break loops safely.
        """
        if self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()
