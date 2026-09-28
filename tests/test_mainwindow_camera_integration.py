import numpy as np
from PySide6.QtTest import QSignalSpy

import app
from config_model import AppConfig
from hardware import camera_init_worker, ct400_init_worker
from hardware.dummy_ct400 import DummyCT400
from hardware.simulated_camera import SimulatedCamera
from ui import main_window as main_window_module


def _runner_stopped(task):
    try:
        return not task.thread.isRunning()
    except RuntimeError:
        return True


def _start_main_window(qtbot, monkeypatch, tmp_path, fail_after_frames=None):
    config_path = tmp_path / "simulated-camera.ini"
    config_path.write_text(
        """[App]
name = IOPanel integration test

[Instruments]
ct400_dll_path = unavailable-in-test-environment.dll

[Camera:Top]
identifier = simulated-top
enabled = true
name = Top camera
backend = simulation
simulation_width = 16
simulation_height = 12
""",
        encoding="utf-8",
    )
    config = AppConfig.from_ini_dict(app.load_raw_config_from_ini(config_path))

    # MainWindow always starts CT400 initialization. Force its existing safe
    # DummyCT400 path so a lab DLL on this machine can never be opened.
    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", lambda _worker: None)

    # A simulation-only config must not start Vimba, even if its SDK is present.
    vimba_start_attempts = []
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_start_vimbasystem",
        lambda _window: vimba_start_attempts.append(True),
    )
    observed_errors = []
    connect_signals = main_window_module.MainWindow._connect_camera_signals

    def observe_camera_signals(window, camera_instance, panel):
        observed_errors.append(QSignalSpy(camera_instance.error))
        connect_signals(window, camera_instance, panel)

    monkeypatch.setattr(main_window_module.MainWindow, "_connect_camera_signals", observe_camera_signals)

    if fail_after_frames is not None:
        class FailureInjectedCamera(SimulatedCamera):
            def __init__(self, *args, **kwargs):
                kwargs["fail_after_frames"] = fail_after_frames
                kwargs["frame_interval"] = 0.25
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(camera_init_worker, "SimulatedCamera", FailureInjectedCamera)

    window = main_window_module.MainWindow(config)
    window._integration_observed_errors = observed_errors
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: len(window.cameras) == 1, timeout=4000)
    qtbot.waitUntil(lambda: isinstance(window.ct400_device, DummyCT400), timeout=4000)
    # Camera initialization is a short-lived TaskRunner. Its QThread may have
    # finished before the GUI processes the queued bookkeeping callback.
    qtbot.waitUntil(
        lambda: all(_runner_stopped(task) for task in window.camera_tasks),
        timeout=4000,
    )

    assert not vimba_start_attempts
    return window


def _close_window_and_check_cleanup(window, qtbot):
    camera_instance = window.cameras[0]
    panel = window.camera_panels[camera_instance.identifier]
    frame_thread = camera_instance._frame_thread
    ct400_thread = window.ct400_init_thread

    window.close()

    assert frame_thread is not None
    qtbot.waitUntil(lambda: not frame_thread.is_alive(), timeout=1500)
    assert not camera_instance.is_streaming
    assert not window.cameras
    if ct400_thread is not None:
        try:
            assert not ct400_thread.isRunning()
        except RuntimeError:  # Qt has deleted the completed thread wrapper.
            pass
    for task in window.camera_tasks:
        assert _runner_stopped(task)
    assert not panel.conversion_thread.isRunning()


def test_mainwindow_loads_simulation_config_and_displays_camera_frames(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path)
    camera_instance = window.cameras[0]
    panel = window.camera_panels[camera_instance.identifier]
    raw_frames = QSignalSpy(camera_instance.new_frame)

    assert isinstance(camera_instance, SimulatedCamera)
    assert "[SIMULATED]" in panel.title_label.text()
    menu_titles = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert menu_titles == ["File", "Instruments", "Cameras", "Help"]
    qtbot.waitUntil(lambda: raw_frames.count() >= 2, timeout=2000)
    first = raw_frames.at(0)[0]
    second = raw_frames.at(1)[0]
    assert first.shape == second.shape == (12, 16)
    assert first.dtype == second.dtype == np.uint8
    assert np.array_equal(second, (first.astype(np.uint16) + 4).astype(np.uint8))

    qtbot.waitUntil(lambda: panel._latest_pixmap is not None, timeout=2000)
    assert not panel._latest_pixmap.isNull()
    assert (panel._latest_pixmap.width(), panel._latest_pixmap.height()) == (16, 12)
    displayed = panel._latest_pixmap.toImage()
    assert displayed.pixelColor(15, 0).red() - displayed.pixelColor(0, 0).red() == 64
    assert panel.video_label.pixmap() is not None
    assert not panel.video_label.pixmap().isNull()

    _close_window_and_check_cleanup(window, qtbot)


def test_mainwindow_displays_camera_error_recovers_and_shuts_down(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, fail_after_frames=3)
    camera_instance = window.cameras[0]
    panel = window.camera_panels[camera_instance.identifier]
    assert camera_instance.fail_after_frames == 3

    error_spy = window._integration_observed_errors[0]
    qtbot.waitUntil(lambda: error_spy.count() >= 1, timeout=2500)
    qtbot.waitUntil(lambda: panel._camera_error_active, timeout=1000)
    assert panel.video_label.text() == "Simulated camera acquisition failure"
    assert not camera_instance.is_streaming

    camera_instance.fail_after_frames = None
    recovered_frames = QSignalSpy(camera_instance.new_frame)
    assert camera_instance.open()
    qtbot.waitUntil(lambda: recovered_frames.count() >= 1, timeout=2000)
    qtbot.waitUntil(lambda: panel._latest_pixmap is not None, timeout=2000)
    assert not panel._latest_pixmap.isNull()

    _close_window_and_check_cleanup(window, qtbot)
