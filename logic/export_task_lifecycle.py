"""Shared Qt lifecycle bookkeeping for asynchronous CSV/MAT export tasks.

This object is deliberately not a QObject: the owning widget's existing Qt
slots receive queued outcome and QThread.finished signals in the GUI thread.
A native QThread can remain alive after finished, so its TaskRunner stays
strongly referenced until QObject.destroyed arrives.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from PySide6.QtCore import QObject, Qt

from logic.task_runner import BaseWorker, TaskRunner

_Outcome = TypeVar("_Outcome")


class ExportTaskLifecycle(Generic[_Outcome]):
    """Track a single active export and the native lifetime of all owned threads."""

    def __init__(self, owner: QObject) -> None:
        self._owner = owner
        self.active_task: TaskRunner | None = None
        self.owned_tasks: dict[int, TaskRunner] = {}
        self.result: _Outcome | None = None
        self.thread_stopped = False
        self._idle_notified = True

    @property
    def busy(self) -> bool:
        """True until the active job completes AND its QThread is destroyed."""
        return self.active_task is not None or bool(self.owned_tasks)

    def start(
        self,
        worker: BaseWorker,
        *,
        on_outcome: Callable[[_Outcome], None],
        on_thread_finished: Callable[[], None],
        on_destroyed: Callable[[int], None],
    ) -> None:
        if self.busy:
            raise RuntimeError("Cannot start another export while a native export thread is owned.")

        task = TaskRunner(worker)
        # The QWidget owns the native object; this controller retains the
        # Python TaskRunner until Qt emits the QThread.destroyed signal.
        task.worker_thread.setParent(self._owner)
        task_id = id(task)
        self.owned_tasks[task_id] = task
        task.worker_thread.destroyed.connect(lambda _obj=None, key=task_id: on_destroyed(key))
        self.active_task = task
        self.result = None
        self.thread_stopped = False
        self._idle_notified = False
        worker.outcome.connect(on_outcome, Qt.ConnectionType.QueuedConnection)
        task.worker_thread.finished.connect(on_thread_finished, Qt.ConnectionType.QueuedConnection)
        task.start()

    def set_outcome(self, result: _Outcome) -> None:
        self.result = result

    def set_thread_finished(self) -> None:
        self.thread_stopped = True

    def take_ready_result(self) -> _Outcome | None:
        """Return exactly once, only after both queued notifications have arrived."""
        if self.active_task is None or not self.thread_stopped or self.result is None:
            return None
        result = self.result
        self.active_task = None
        self.result = None
        self.thread_stopped = False
        return result

    def release_destroyed(self, task_id: int) -> None:
        """Release Python ownership only when Qt destroys the native thread."""
        self.owned_tasks.pop(task_id, None)

    def take_idle_notification(self) -> bool:
        """Notify at most once, never before native destruction and completion."""
        if self.busy or self._idle_notified:
            return False
        self._idle_notified = True
        return True
