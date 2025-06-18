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

            # Find all available ports first. This is a blocking call.
            found_ports = PiezoController.find_devices(dll_path)
            if not found_ports:
                raise PiezoError("No Piezo controllers were detected on any COM ports.")

            logger.info(f"Discovered Piezo devices on ports: {found_ports}")

            # Get desired ports from config and validate
            left_port = self.config.piezo_left_serial
            right_port = self.config.piezo_right_serial

            if left_port not in found_ports:
                raise PiezoError(
                    f"Configured left piezo port '{left_port}' not found in detected devices: {found_ports}"
                )
            if right_port not in found_ports:
                raise PiezoError(
                    f"Configured right piezo port '{right_port}' not found in detected devices: {found_ports}"
                )

            # Create and connect to the controllers
            piezo_left = PiezoController(dll_path)
            piezo_left.connect(left_port)
            logger.info(f"Successfully connected to Left Piezo on {left_port}")

            piezo_right = PiezoController(dll_path)
            piezo_right.connect(right_port)
            logger.info(f"Successfully connected to Right Piezo on {right_port}")

            self.piezos_initialized.emit(piezo_left, piezo_right)

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
