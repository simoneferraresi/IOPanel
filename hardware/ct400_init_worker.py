import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import override

from PySide6.QtCore import Signal, Slot

# We need to import from the main project, so adjust path if necessary
# This assumes 'app.py' is in the parent directory
from config_model import AppConfig
from hardware.ct400 import CT400, CT400Error
from hardware.dummy_ct400 import DummyCT400
from hardware.interfaces import AbstractCT400
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.CT400Init")


@dataclass(frozen=True, slots=True)
class CT400InitOutcome:
    """One terminal result from a CT400 initialization attempt."""

    device: AbstractCT400 | None
    state_name: str
    message: str
    cancelled: bool = False


class CT400InitWorker(BaseWorker):
    """
    A worker that initializes the CT400 in a separate thread.
    It attempts to find the DLL and connect to the hardware.
    If it fails, it emits a DummyCT400 instance.
    """

    # Emitted exactly once before finished, including cancellation without a device.
    initialization_completed = Signal(object)

    # Signal: Emits a status message for the UI
    status_updated = Signal(str, str)  # Emits (state_name, message) e.g., ("UNAVAILABLE", "DLL not found")

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._is_running = True
        self._terminal_outcome: CT400InitOutcome | None = None

    @property
    def terminal_outcome(self) -> CT400InitOutcome | None:
        """The immutable terminal outcome, available after the worker thread ends."""
        return self._terminal_outcome

    def _find_dll(self) -> Path | None:
        """Finds the CT400 DLL based on config, env vars, and app path."""
        # Determine the application's root directory
        app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()

        # Define potential paths in order of priority
        search_paths = [
            self.config.instruments.ct400_dll_path,  # 1. Path from config.ini
            app_dir / "CT400_lib.dll",  # 2. Application root directory
        ]

        for p in search_paths:
            if not p:  # Skip empty or None paths
                continue
            path_obj = Path(p)
            if path_obj.exists():
                logger.info(f"CT400InitWorker: Found CT400 DLL at: {path_obj}")
                return path_obj

        logger.warning("CT400InitWorker: No CT400 DLL found in search paths.")
        return None

    @override
    @Slot()
    def run(self) -> None:
        """Initializes the CT400 device."""
        outcome: CT400InitOutcome | None = None
        try:
            if not self._is_running:
                outcome = CT400InitOutcome(
                    device=None,
                    state_name="UNAVAILABLE",
                    message="CT400 initialization cancelled before a device was created.",
                    cancelled=True,
                )
            else:
                outcome = self._initialize()
        except Exception as error:
            logger.exception("Unexpected error in CT400 initialization worker.")
            outcome = self._failure_outcome(f"Unexpected CT400 initialization error: {error}.")
        finally:
            if outcome is None:
                outcome = CT400InitOutcome(
                    device=None,
                    state_name="UNAVAILABLE",
                    message="CT400 initialization ended without a terminal result.",
                )
            self._terminal_outcome = outcome
            try:
                self.initialization_completed.emit(outcome)
            finally:
                self.finished.emit()

    def _initialize(self) -> CT400InitOutcome:
        logger.info("CT400InitWorker: Starting initialization...")
        if self.config.instruments.ct400_backend == "simulation":
            self.status_updated.emit("SIMULATED", "CT400: Starting simulated backend.")
            device = DummyCT400()
            self.status_updated.emit("SIMULATED", "CT400: Simulated backend ready.")
            return CT400InitOutcome(device, "SIMULATED", "CT400: Simulated backend ready.")

        self.status_updated.emit("UNKNOWN", "CT400: Searching for DLL...")
        dll_path = self._find_dll()
        if dll_path is None:
            message = "CT400 DLL not found. Physical initialization failed; a dummy device is available."
            logger.warning(message)
            return self._failure_outcome(message)

        try:
            self.status_updated.emit("UNKNOWN", "CT400: Initializing hardware...")
            device = CT400(dll_path)
        except (CT400Error, OSError) as error:
            message = f"CT400 initialization failed: {error}. A dummy device is available."
            logger.exception(message)
            return self._failure_outcome(message)
        except Exception as error:
            message = f"Unexpected CT400 initialization error: {error}. A dummy device is available."
            logger.exception(message)
            return self._failure_outcome(message)

        logger.info("CT400InitWorker: CT400 device object created using DLL: %s", dll_path)
        return CT400InitOutcome(device, "DISCONNECTED", "CT400: Ready (Disconnected)")

    @staticmethod
    def _failure_outcome(message: str) -> CT400InitOutcome:
        try:
            fallback: AbstractCT400 | None = DummyCT400()
        except Exception:
            logger.exception("Could not create the CT400 dummy fallback.")
            fallback = None
            message = f"{message} Dummy fallback creation also failed."
        return CT400InitOutcome(fallback, "UNAVAILABLE", message)

    def stop(self):
        self._is_running = False
