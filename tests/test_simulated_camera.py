import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy

from config_model import CameraConfig
from hardware import camera
from hardware.camera_init_worker import CameraInitWorker
from hardware.simulated_camera import SimulatedCamera
from logic.task_runner import TaskRunner
from ui.camera_widgets import CameraPanel


def test_camera_config_keeps_physical_backend_as_default():
    config = CameraConfig(identifier="DEV_1", name="Top")
    assert config.backend == "vimba"
    assert config.simulation_width == 64
    assert config.simulation_height == 48


def test_camera_init_worker_explicitly_opens_simulator_without_vimba(qtbot, monkeypatch):
    monkeypatch.setattr(camera, "VIMBA_AVAILABLE", False)
    config = CameraConfig(identifier="sim-top", name="Top", backend="simulation")
    worker = CameraInitWorker(config.identifier, config)
    initialized = QSignalSpy(worker.camera_initialized)
    runner = TaskRunner(worker)
    runner.start()
    qtbot.waitUntil(lambda: initialized.count() == 1, timeout=1500)

    instance = initialized.at(0)[1]
    assert isinstance(instance, SimulatedCamera)
    assert instance.camera_name == "Top [SIMULATED]"
    assert instance.thread() == QApplication.instance().thread()
    assert instance.open()
    frames = QSignalSpy(instance.new_frame)
    qtbot.waitUntil(lambda: frames.count() >= 1, timeout=1500)
    assert frames.at(0)[0].shape == (48, 64)
    frame_thread = instance._frame_thread
    instance.close()
    assert frame_thread is not None and not frame_thread.is_alive()


def test_simulated_frames_reach_existing_panel_conversion_path(qtbot):
    camera_instance = SimulatedCamera("sim-panel", width=8, height=6, frame_interval=0.01)
    config = CameraConfig(identifier="sim-panel", name="Test", backend="simulation")
    panel = CameraPanel(None, "Test [SIMULATED]", config)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_camera(camera_instance)
    camera_instance.new_frame.connect(panel.process_new_frame_data)
    raw_frames = QSignalSpy(camera_instance.new_frame)
    converted_frames = QSignalSpy(panel.conversion_worker.image_ready)

    assert camera_instance.open()
    qtbot.waitUntil(lambda: raw_frames.count() >= 2, timeout=1500)
    qtbot.waitUntil(lambda: converted_frames.count() >= 2, timeout=1500)
    first_raw = raw_frames.at(0)[0]
    second_raw = raw_frames.at(1)[0]
    first_image = converted_frames.at(0)[0]
    assert first_raw.shape == (6, 8)
    assert first_raw.dtype == np.uint8
    assert [int(first_raw[y, x]) for y, x in ((0, 0), (0, 7), (5, 0), (5, 7))] == [24, 88, 152, 216]
    assert [int(second_raw[y, x]) for y, x in ((0, 0), (0, 7), (5, 0), (5, 7))] == [28, 92, 156, 220]
    assert (first_image.width(), first_image.height()) == (8, 6)
    assert first_image.pixelColor(0, 0).red() == 24
    assert first_image.pixelColor(7, 5).red() == 216

    qtbot.waitUntil(lambda: panel._latest_pixmap is not None)
    frame_thread = camera_instance._frame_thread
    camera_instance.close()
    panel.close()
    assert not camera_instance.is_streaming
    assert frame_thread is not None and not frame_thread.is_alive()
    assert not panel.conversion_thread.isRunning()


def test_simulator_error_can_be_recovered_and_closed(qtbot):
    instance = SimulatedCamera("sim-recover", frame_interval=0.01, fail_after_frames=1)
    errors = QSignalSpy(instance.error)
    assert instance.open()
    qtbot.waitUntil(lambda: errors.count() >= 1, timeout=1500)
    assert errors.at(0)[0] == "Simulated camera acquisition failure"
    assert not instance.is_streaming

    instance.fail_after_frames = None
    frames = QSignalSpy(instance.new_frame)
    assert instance.open()
    qtbot.waitUntil(lambda: frames.count() >= 1, timeout=1500)
    frame_thread = instance._frame_thread
    instance.close()
    assert frame_thread is not None and not frame_thread.is_alive()
    assert not instance.is_streaming
