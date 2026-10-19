import logging
from pathlib import Path
from typing import override

from PySide6.QtCore import Signal, Slot
from serial.tools import list_ports

from config_model import InstrumentsConfig
from hardware.piezo import PiezoController, PiezoError
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.PiezoInit")


class PiezoInitWorker(BaseWorker):
    """Find configured serial-port candidates without opening attached devices."""

    # Signal: Emits the two controller objects on success, or (None, None).
    piezos_initialized = Signal(object, object)
    # Signal: Emits an error message string on failure.
    initialization_failed = Signal(str)

    def __init__(self, config: InstrumentsConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._is_running = True

    @override
    @Slot()
    def run(self) -> None:
        """Enumerate configured COM ports; never open devices during discovery."""
        piezo_left = None
        piezo_right = None
        try:
            if not self._is_running:
                return

            logger.info("Worker starting initialization for Piezo controllers...")
            dll_path = Path(self.config.piezo_dll_path)
            if not dll_path.is_file():
                raise PiezoError(f"Piezo DLL not found at specified path: {dll_path}")

            # The legacy find_devices() actively Open/Close-probed every COM port.
            # Startup/Refresh must not talk to unrelated serial instruments.
            found_ports = {port.device.upper() for port in list_ports.comports()}
            logger.info("Piezo candidate serial ports enumerated (no device opens): %s", sorted(found_ports))

            left_port = self.config.piezo_left_serial
            right_port = self.config.piezo_right_serial

            # --- New logic: Create if found, otherwise it stays None ---
            if left_port and left_port.upper() in found_ports:
                piezo_left = PiezoController(dll_path)
                logger.info("Left Piezo configured port %s is present (device not connected or verified).", left_port)
            else:
                logger.info("Left Piezo configured port %s is not available.", left_port or "(unset)")

            if right_port and right_port.upper() in found_ports:
                piezo_right = PiezoController(dll_path)
                logger.info("Right Piezo configured port %s is present (device not connected or verified).", right_port)
            else:
                logger.info("Right Piezo configured port %s is not available.", right_port or "(unset)")

            # Always emit success, even if objects are None
            self.piezos_initialized.emit(piezo_left, piezo_right)

        except (PiezoError, FileNotFoundError) as e:
            self._cleanup_partial_controllers(piezo_left, piezo_right)
            logger.exception("Failed to initialize Piezo library")
            self.initialization_failed.emit(str(e))
        except Exception as e:
            self._cleanup_partial_controllers(piezo_left, piezo_right)
            logger.exception("Unexpected error in piezo init worker")
            self.initialization_failed.emit(f"An unexpected error occurred: {e}")
        finally:
            self.finished.emit()

    @staticmethod
    def _cleanup_partial_controllers(piezo_left, piezo_right):
        for side, piezo in (("left", piezo_left), ("right", piezo_right)):
            if piezo is None:
                continue
            try:
                if piezo.is_connected():
                    piezo.disconnect()
            except Exception:
                logger.exception("Failed to clean up partially initialized %s Piezo controller", side)

    def stop(self):
        self._is_running = False
