import logging

from PySide6.QtCore import Signal, Slot

from config_model import CameraConfig
from hardware.camera import VimbaCam
from logic.task_runner import BaseWorker

logger = logging.getLogger("LabApp.CameraInit")


class CameraInitWorker(BaseWorker):
    """
    A worker that initializes a single VimbaCam in a separate thread.
    """

    # Signal: Emits the identifier, successfully opened camera object (or None), and original config.
    camera_initialized = Signal(str, object, CameraConfig)

    def __init__(self, identifier: str, cam_config: CameraConfig, parent=None):
        super().__init__(parent)
        self.identifier = identifier
        self.cam_config = cam_config
        self._is_running = True

    @Slot()
    def run(self):
        """Creates and opens a VimbaCam instance."""
        if not self._is_running:
            return
        logger.info(f"Worker starting initialization for: {self.cam_config.name} (ID: {self.identifier})")
        cam_instance = None
        try:
            cam_instance = VimbaCam(
                identifier=self.identifier,  # Use the passed-in identifier
                camera_name=self.cam_config.name,
                flip_horizontal=self.cam_config.flip_horizontal,
            )
            if cam_instance.open():
                logger.info(f"Worker successfully opened camera: {self.cam_config.name}")
                self.camera_initialized.emit(self.identifier, cam_instance, self.cam_config)
            else:
                logger.error(f"Worker failed to open camera: {self.cam_config.name}")
                cam_instance.close()  # Ensure cleanup
                self.camera_initialized.emit(self.identifier, None, self.cam_config)
        except Exception as e:
            logger.exception(f"Exception in camera init worker for {self.cam_config.name}: {e}")
            if cam_instance:
                cam_instance.close()
            self.camera_initialized.emit(self.identifier, None, self.cam_config)

        self.finished.emit()
