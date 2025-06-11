import logging

from PySide6.QtCore import QObject, Signal, Slot

from config_model import CameraConfig
from hardware.camera import VimbaCam

logger = logging.getLogger("LabApp.CameraInit")


class CameraInitWorker(QObject):
    """
    A worker that initializes a single VimbaCam in a separate thread.
    """

    # Signal: Emits the successfully opened camera object, or None on failure.
    # Also emits the original config so the main window knows which camera this is.
    camera_initialized = Signal(object, CameraConfig)

    def __init__(self, cam_config: CameraConfig, parent=None):
        super().__init__(parent)
        self.cam_config = cam_config
        self._is_running = True

    @Slot()
    def run(self):
        """Creates and opens a VimbaCam instance."""
        if not self._is_running:
            return

        logger.info(f"Worker starting initialization for: {self.cam_config.name}")
        cam_instance = None
        try:
            cam_instance = VimbaCam(
                identifier=self.cam_config.identifier,
                camera_name=self.cam_config.name,
                flip_horizontal=self.cam_config.flip_horizontal,
            )
            if cam_instance.open():
                logger.info(f"Worker successfully opened camera: {self.cam_config.name}")
                self.camera_initialized.emit(cam_instance, self.cam_config)
            else:
                logger.error(f"Worker failed to open camera: {self.cam_config.name}")
                cam_instance.close()  # Ensure cleanup
                self.camera_initialized.emit(None, self.cam_config)
        except Exception as e:
            logger.exception(f"Exception in camera init worker for {self.cam_config.name}: {e}")
            if cam_instance:
                cam_instance.close()
            self.camera_initialized.emit(None, self.cam_config)
