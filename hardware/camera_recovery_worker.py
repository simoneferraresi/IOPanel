"""One-shot camera recovery executed outside the GUI thread."""

import logging
from typing import override

from PySide6.QtCore import Signal, Slot

from hardware.camera import VimbaCam
from logic.task_runner import BaseWorker, TaskRunner

logger = logging.getLogger("LabApp.CameraRecovery")


class CameraRecoveryWorker(BaseWorker):
    """Runs one blocking close/reopen attempt for a single camera."""

    recovery_finished = Signal(str, bool, str, object)

    def __init__(self, camera: VimbaCam, identifier: str, parent=None):
        super().__init__(parent)
        self.camera = camera
        self.identifier = identifier
        self.task: TaskRunner | None = None

    @override
    @Slot()
    def run(self) -> None:
        success = False
        message = ""
        try:
            success = self.camera.recover_once()
            if not success:
                message = f"Failed to reconnect '{self.camera.camera_name}'."
        except Exception as exc:
            logger.exception("Recovery attempt failed for %s", self.identifier)
            message = f"Recovery failed: {exc}"
        task = self.task
        self.recovery_finished.emit(self.identifier, success, message, task)
        self.task = None
        self.finished.emit()
