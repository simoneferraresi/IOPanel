import logging
import sys
from pathlib import Path

from PySide6.QtCore import Signal, Slot

# We need to import from the main project, so adjust path if necessary
# This assumes 'app.py' is in the parent directory
from config_model import AppConfig
from hardware.ct400 import CT400, CT400Error, CT400InitializationError
from hardware.dummy_ct400 import DummyCT400
from hardware.interfaces import AbstractCT400
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.CT400Init")


class CT400InitWorker(BaseWorker):
    """
    A worker that initializes the CT400 in a separate thread.
    It attempts to find the DLL and connect to the hardware.
    If it fails, it emits a DummyCT400 instance.
    """

    # Signal: Emits the successfully initialized hardware object (real or dummy)
    ct400_initialized = Signal(object)

    # Signal: Emits a status message for the UI
    status_updated = Signal(str, str)  # Emits (state_name, message) e.g., ("UNAVAILABLE", "DLL not found")

    def __init__(self, config: AppConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._is_running = True

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

    @Slot()
    def run(self):
        """Initializes the CT400 device."""
        if not self._is_running:
            return

        logger.info("CT400InitWorker: Starting initialization...")
        self.status_updated.emit("UNKNOWN", "CT400: Searching for DLL...")

        dll_path_obj = self._find_dll()
        ct400_device: AbstractCT400

        if not dll_path_obj:
            msg = "CT400 DLL not found. Using dummy device."
            logger.warning(msg)
            self.status_updated.emit("UNAVAILABLE", "CT400: DLL not found. Using Dummy.")
            ct400_device = DummyCT400()
            self.ct400_initialized.emit(ct400_device)
            return

        try:
            self.status_updated.emit("UNKNOWN", "CT400: Initializing hardware...")
            # This is the slow, blocking call
            ct400_device = CT400(dll_path_obj)
            logger.info(f"CT400InitWorker: CT400 device object created using DLL: {dll_path_obj}")
            self.status_updated.emit("DISCONNECTED", "CT400: Ready (Disconnected)")
            self.ct400_initialized.emit(ct400_device)

        except (CT400Error, FileNotFoundError, OSError, CT400InitializationError) as e:
            msg = f"CT400 Init Failed: {e}. Using dummy device."
            logger.error(msg, exc_info=True)
            self.status_updated.emit("UNAVAILABLE", "CT400: Init Failed. Using Dummy.")
            ct400_device = DummyCT400()
            self.ct400_initialized.emit(ct400_device)
        except Exception as e:
            msg = f"Unexpected error during CT400 initialization: {e}. Using dummy device."
            logger.critical(msg, exc_info=True)
            self.status_updated.emit("UNAVAILABLE", "CT400: Critical Error. Using Dummy.")
            ct400_device = DummyCT400()
            self.ct400_initialized.emit(ct400_device)

        self.finished.emit()

    def stop(self):
        self._is_running = False
