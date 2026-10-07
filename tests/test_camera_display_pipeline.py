import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QSignalSpy

from config_model import CameraConfig
from hardware.simulated_camera import SimulatedCamera
from ui.camera_widgets import CameraPanel, ImageConversionWorker


def test_conversion_mailbox_coalesces_to_latest_frame(qtbot, monkeypatch):
    worker = ImageConversionWorker(is_mono=True, camera_name="mailbox")
    worker.set_active(True)
    processed = []
    monkeypatch.setattr(worker, "process_frame", lambda frame: processed.append(int(frame[0, 0])))

    for generation in range(1000):
        worker.submit_frame(np.full((1, 1), generation % 256, dtype=np.uint8))

    qtbot.waitUntil(lambda: len(processed) == 1)
    assert processed == [999 % 256]
    assert worker.submitted_frames == 1000
    assert worker.coalesced_frames == 999
    assert worker.max_pending_frames == 1


def test_color_conversion_preserves_bgr_to_rgb_interpretation():
    worker = ImageConversionWorker(is_mono=False, camera_name="color")
    converted = []
    worker.image_ready.connect(converted.append, Qt.ConnectionType.DirectConnection)
    frame = np.zeros((2, 3, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # BGR blue
    worker.process_frame(frame)

    assert len(converted) == 1
    assert converted[0].format() == QImage.Format.Format_RGB888
    assert converted[0].pixelColor(1, 1).blue() == 255
    assert converted[0].pixelColor(1, 1).red() == 0


def test_converted_image_notifications_coalesce_before_gui_display(qtbot, monkeypatch):
    panel = CameraPanel(None, "Mailbox", CameraConfig(identifier="mailbox", name="Mailbox"))
    qtbot.addWidget(panel)
    panel.show()
    displayed = []
    monkeypatch.setattr(panel, "_display_converted_image", displayed.append)
    notifications = QSignalSpy(panel._display_image_ready)

    for generation in range(100):
        image = QImage(1, 1, QImage.Format.Format_Grayscale8)
        image.fill(generation)
        panel._accept_converted_image(image)

    assert notifications.count() == 1
    assert panel.coalesced_images == 99
    assert panel.max_pending_images == 1
    qtbot.waitUntil(lambda: len(displayed) == 1)
    assert displayed[0].pixelColor(0, 0).red() == 99
    panel.close()


def test_full_resolution_screenshot_pixmap_keeps_orientation_and_color(qtbot):
    panel = CameraPanel(None, "Screenshot", CameraConfig(identifier="shot", name="Shot"))
    qtbot.addWidget(panel)
    image = QImage(1292, 964, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.black)
    image.setPixelColor(0, 0, Qt.GlobalColor.red)
    image.setPixelColor(1291, 963, Qt.GlobalColor.blue)

    panel._display_converted_image(image)

    assert panel._latest_pixmap is not None
    assert (panel._latest_pixmap.width(), panel._latest_pixmap.height()) == (1292, 964)
    assert panel._latest_pixmap.toImage().pixelColor(0, 0).red() > 200
    assert panel._latest_pixmap.toImage().pixelColor(1291, 963).blue() > 200
    panel.close()


def test_roi_screenshot_source_keeps_native_acquired_dimensions(qtbot):
    panel = CameraPanel(None, "ROI Screenshot", CameraConfig(identifier="roi-shot", name="ROI Shot"))
    qtbot.addWidget(panel)
    image = QImage(1292, 480, QImage.Format.Format_Grayscale8)
    image.fill(127)
    panel._display_converted_image(image)
    assert panel._latest_pixmap is not None
    assert (panel._latest_pixmap.width(), panel._latest_pixmap.height()) == (1292, 480)
    panel.close()


def test_overlay_screenshot_button_saves_native_roi_without_gui_overlay(qtbot, monkeypatch, tmp_path):
    panel = CameraPanel(None, "Overlay Screenshot", CameraConfig(identifier="overlay-shot", name="Overlay Shot"))
    qtbot.addWidget(panel)
    panel.resize(800, 500)
    panel.show()
    image = QImage(1292, 240, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.darkBlue)
    panel._display_converted_image(image)
    panel.update_fps(30.4)
    panel._delayed_display_update()
    output = tmp_path / "native-roi.png"
    monkeypatch.setattr(
        "ui.camera_widgets.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "PNG Images (*.png)"),
    )

    panel.screenshot_btn.click()

    saved = QImage(str(output))
    assert (saved.width(), saved.height()) == (1292, 240)
    assert saved.pixelColor(0, 0) == image.pixelColor(0, 0)
    assert saved.pixelColor(20, 20) == image.pixelColor(20, 20)
    assert panel.video_label.pixmap() is not None
    assert panel.video_label.pixmap().width() != saved.width()
    panel.close()


def test_panel_shutdown_drops_pending_frame_and_stops_conversion_thread(qtbot):
    panel = CameraPanel(None, "Shutdown", CameraConfig(identifier="shutdown", name="Shutdown"))
    qtbot.addWidget(panel)
    panel.show()
    panel.set_camera(SimulatedCamera("shutdown"))
    worker = panel.conversion_worker
    thread = panel.conversion_thread
    worker.submit_frame(np.zeros((964, 1292), dtype=np.uint8))

    panel.close()

    assert thread is not None and not thread.isRunning()
    assert worker._latest_frame is None
    assert panel._panel_closing


@pytest.mark.parametrize("fps", [30, 45, 60, 75, 90])
def test_simulated_camera_rates_keep_presentation_mailboxes_bounded(qtbot, fps):
    camera = SimulatedCamera(f"rate-{fps}", width=16, height=12, frame_interval=1 / fps)
    panel = CameraPanel(None, f"Rate {fps}", CameraConfig(identifier=f"rate-{fps}", name=f"Rate {fps}"))
    qtbot.addWidget(panel)
    panel.show()
    panel.set_camera(camera)
    assert camera.open()
    try:
        minimum_frames = max(3, int(fps * 0.1))
        qtbot.waitUntil(lambda: camera._frame_count >= minimum_frames, timeout=2000)
        assert panel.conversion_worker.max_pending_frames <= 1
        assert panel.max_pending_images <= 1
        assert panel._latest_pixmap is not None
    finally:
        camera.close()
        panel.close()
