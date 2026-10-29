"""One-shot, worker-thread Vimba X startup.

Only system startup runs here. The caller must keep the returned context alive
until all camera producers and workers have retired, and then call __exit__ once.
Native VmbPy startup may not be interruptible; close must defer until QThread
has emitted finished rather than killing or timing out this worker.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, override

from PySide6.QtCore import Signal, Slot

from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.VimbaSystemInit")


@dataclass(frozen=True)
class VimbaSystemInitOutcome:
    instance: Any | None
    error: str | None = None


class VimbaSystemInitWorker(BaseWorker):
    """Enter the VmbSystem context on a background QThread exactly once."""

    initialization_completed = Signal(object)

    def __init__(self, system_factory: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._system_factory = system_factory

    @override
    @Slot()
    def run(self) -> None:
        try:
            logger.info("Attempting to start VmbSystem in initialization worker...")
            instance = self._system_factory()
            if instance is None:
                raise RuntimeError("Vimba SDK did not return a system instance")
            instance.__enter__()
            logger.info("VmbSystem entered successfully.")
            self.initialization_completed.emit(VimbaSystemInitOutcome(instance))
        except Exception as exc:
            logger.exception("Failed to enter VmbSystem")
            self.initialization_completed.emit(
                VimbaSystemInitOutcome(None, f"{type(exc).__name__}: {exc}")
            )
        finally:
            self.finished.emit()
