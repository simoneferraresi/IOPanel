"""Driver-free deterministic camera for exercising the application's frame path."""

from __future__ import annotations

import logging
import threading

import numpy as np

from hardware.camera import VimbaCam

logger = logging.getLogger("LabApp.SimulatedCamera")


class SimulatedCamera(VimbaCam):
    """A mono8 camera source implementing the signals and methods used by the GUI.

    Each frame is a four-quadrant intensity target plus a small sequence offset.
    It makes orientation and frame updates easy to inspect without modeling a
    physical sensor. ``fail_after_frames`` is intended for deterministic tests.
    """

    def __init__(
        self,
        identifier: str,
        camera_name: str | None = None,
        flip_horizontal: bool = False,
        width: int = 64,
        height: int = 48,
        frame_interval: float = 0.05,
        fail_after_frames: int | None = None,
        parent=None,
    ):
        if width <= 1 or height <= 1:
            raise ValueError("Simulated frame width and height must be greater than one pixel")
        if frame_interval <= 0:
            raise ValueError("frame_interval must be positive")
        if fail_after_frames is not None and fail_after_frames < 1:
            raise ValueError("fail_after_frames must be positive")
        super().__init__(identifier, camera_name, flip_horizontal, parent)
        self.width = width
        self.height = height
        self.frame_interval = frame_interval
        self.fail_after_frames = fail_after_frames
        self.is_mono = True
        self._stop_event = threading.Event()
        self._frame_thread: threading.Thread | None = None
        self._frame_count = 0
        self._is_open = False

    def open(self) -> bool:
        """Start deterministic frame delivery without loading the Vimba SDK."""
        if self.is_streaming:
            return True
        self._stop_event.clear()
        self._frame_count = 0
        self.is_mono = True
        self.is_streaming = True
        self._is_closing = False
        self._is_open = True
        self._frame_thread = threading.Thread(
            target=self._deliver_frames,
            name=f"SimulatedCamera-{self.identifier}",
            daemon=True,
        )
        self._frame_thread.start()
        self.connected.emit()
        return True

    def _make_frame(self, sequence: int) -> np.ndarray:
        middle_y = self.height // 2
        middle_x = self.width // 2
        frame = np.empty((self.height, self.width), dtype=np.uint8)
        frame[:middle_y, :middle_x] = 24
        frame[:middle_y, middle_x:] = 88
        frame[middle_y:, :middle_x] = 152
        frame[middle_y:, middle_x:] = 216
        frame = ((frame.astype(np.uint16) + sequence * 4) % 256).astype(np.uint8)
        if self.flip_horizontal:
            frame = np.fliplr(frame).copy()
        return frame

    def _deliver_frames(self) -> None:
        while not self._stop_event.wait(self.frame_interval):
            frame = self._make_frame(self._frame_count)
            self._frame_count += 1
            self.frame_buffer.add_frame(frame)
            self.new_frame.emit(frame)
            fps = self.frame_monitor.update()
            if fps is not None:
                self.fps_updated.emit(fps)
            if self.fail_after_frames is not None and self._frame_count >= self.fail_after_frames:
                self.is_streaming = False
                self.error.emit("Simulated camera acquisition failure")
                return

    def close(self) -> None:
        """Stop frame delivery and release buffered images; safe to call repeatedly."""
        was_open = self._is_open
        self._is_open = False
        self._stop_event.set()
        thread = self._frame_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.frame_interval * 2))
            if thread.is_alive():
                logger.error("Simulated camera frame thread did not stop within its timeout")
        self._frame_thread = None
        self.is_streaming = False
        self.is_mono = None
        self.frame_buffer.clear()
        if was_open:
            self.disconnected.emit()
