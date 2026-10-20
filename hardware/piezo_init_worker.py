import logging
from pathlib import Path
from typing import override

from PySide6.QtCore import Signal, Slot

from config_model import InstrumentsConfig
from hardware.piezo import PiezoController, PiezoError
from hardware.piezo_ports import enumerate_piezo_ports, resolve_piezo_port
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
            ports = enumerate_piezo_ports()
            logger.info("Piezo candidate serial ports enumerated (no device opens): %s", [p.device for p in ports])
            left_port = resolve_piezo_port(
                ports,
                configured_port=self.config.piezo_left_serial,
                usb_serial=self.config.piezo_left_usb_serial,
            )
            right_port = resolve_piezo_port(
                ports,
                configured_port=self.config.piezo_right_serial,
                usb_serial=self.config.piezo_right_usb_serial,
            )

            # Do not permit Left and Right to claim the same physical interface.
            if left_port is not None and left_port.casefold() == (right_port or "").casefold():
                logger.error(
                    "Left and Right piezo configurations resolve to the same port %s; refusing both.", left_port
                )
                left_port = right_port = None

            if left_port is not None:
                piezo_left = PiezoController(dll_path)
                piezo_left.discovered_port = left_port
                logger.info("Left piezo candidate resolved to %s (not connected).", left_port)
            else:
                logger.warning("Left piezo identity/port unavailable or ambiguous; not connecting.")

            if right_port is not None:
                piezo_right = PiezoController(dll_path)
                piezo_right.discovered_port = right_port
                logger.info("Right piezo candidate resolved to %s (not connected).", right_port)
            else:
                logger.warning("Right piezo identity/port unavailable or ambiguous; not connecting.")

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
