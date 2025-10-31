import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from config_model import InstrumentsConfig
from hardware.piezo import PiezoController, PiezoError

logger = logging.getLogger("LabApp.PiezoInit")


class PiezoInitWorker(QObject):
    """
    A worker that initializes two Piezo controllers in a separate thread.
    """

    # Signal: Emits the two controller objects on success, or (None, None).
    piezos_initialized = Signal(object, object)
    # Signal: Emits an error message string on failure.
    initialization_failed = Signal(str)

    def __init__(self, config: InstrumentsConfig, parent=None):
        super().__init__(parent)
        self.config = config
        self._is_running = True

    @Slot()
    def run(self):
        """Finds and connects to the configured piezo controllers."""
        if not self._is_running:
            return

        logger.info("Worker starting initialization for Piezo controllers...")
        piezo_left, piezo_right = None, None
        try:
            dll_path = Path(self.config.piezo_dll_path)
            if not dll_path.is_file():
                raise PiezoError(f"Piezo DLL not found at specified path: {dll_path}")

            found_ports = PiezoController.find_devices(dll_path)
            logger.info(f"Piezo worker discovered devices: {found_ports}")

            left_port = self.config.piezo_left_serial
            right_port = self.config.piezo_right_serial

            # --- New logic: Create if found, otherwise it stays None ---
            if left_port in found_ports:
                piezo_left = PiezoController(dll_path)
                logger.info(f"Left Piezo ({left_port}) object created (disconnected).")
            else:
                logger.warning(f"Left Piezo ({left_port}) not found. Device may be off.")

            if right_port in found_ports:
                piezo_right = PiezoController(dll_path)
                logger.info(f"Right Piezo ({right_port}) object created (disconnected).")
            else:
                logger.warning(f"Right Piezo ({right_port}) not found. Device may be off.")

            # Always emit success, even if objects are None
            self.piezos_initialized.emit(piezo_left, piezo_right)

        except (PiezoError, FileNotFoundError) as e:
            # This now only catches *critical* errors, like the DLL being missing
            logger.error(f"Failed to initialize Piezo library: {e}", exc_info=True)
            self.initialization_failed.emit(str(e))
        except Exception as e:
            logger.exception(f"Unexpected error in piezo init worker: {e}")
            self.initialization_failed.emit(f"An unexpected error occurred: {e}")

        except (PiezoError, FileNotFoundError) as e:
            logger.error(f"Failed to initialize Piezo controllers: {e}", exc_info=True)
            self.initialization_failed.emit(str(e))
            # Ensure partial connections are cleaned up
            if piezo_left and piezo_left.is_connected():
                piezo_left.disconnect()
            if piezo_right and piezo_right.is_connected():
                piezo_right.disconnect()
        except Exception as e:
            logger.exception(f"Unexpected error in piezo init worker: {e}")
            self.initialization_failed.emit(f"An unexpected error occurred: {e}")

    def stop(self):
        self._is_running = False
