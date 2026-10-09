import numpy as np
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt, QThread
from PySide6.QtTest import QSignalSpy

import app
from app_settings import AppSettings
from config_model import AppConfig
from hardware import camera_init_worker, ct400_init_worker
from hardware.camera_capabilities import ROI
from hardware.dummy_ct400 import DummyCT400
from hardware.simulated_camera import SimulatedCamera
from logic.matlab_engine_manager import MatlabEngineState
from ui import main_window as main_window_module


def _runner_stopped(task):
    try:
        return not task.worker_thread.isRunning()
    except RuntimeError:
        return True


def _start_main_window(qtbot, monkeypatch, tmp_path, fail_after_frames=None, camera_count=1):
    config_path = tmp_path / "simulated-camera.ini"
    config_text = """[App]
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
"""
    if camera_count == 2:
        config_text += """
[Camera:Bottom]
identifier = simulated-bottom
enabled = true
name = Bottom camera
backend = simulation
simulation_width = 16
simulation_height = 12
"""
    config_path.write_text(config_text, encoding="utf-8")
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

    settings = AppSettings(QSettings(str(tmp_path / "simulation-ui.ini"), QSettings.Format.IniFormat))
    window = main_window_module.MainWindow(config, settings=settings)
    window._integration_observed_errors = observed_errors
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: len(window.cameras) == camera_count, timeout=4000)
    qtbot.waitUntil(lambda: isinstance(window.ct400_device, DummyCT400), timeout=4000)
    # Camera initialization is a short-lived TaskRunner. Its QThread may have
    # finished before the GUI processes the queued bookkeeping callback.
    qtbot.waitUntil(
        lambda: all(_runner_stopped(task) for task in window._init_tasks),
        timeout=4000,
    )

    assert not vimba_start_attempts
    return window


def _close_window_and_check_cleanup(window, qtbot):
    cameras = list(window.cameras)
    panels = list(window.camera_panels.values())
    frame_threads = [camera_instance._frame_thread for camera_instance in cameras]
    ct400_task = window.ct400_task

    window.close()

    assert all(frame_thread is not None for frame_thread in frame_threads)
    qtbot.waitUntil(lambda: all(not frame_thread.is_alive() for frame_thread in frame_threads), timeout=1500)
    assert all(not camera_instance.is_streaming for camera_instance in cameras)
    assert not window.cameras
    if ct400_task is not None:
        try:
            assert not ct400_task.worker_thread.isRunning()
        except RuntimeError:  # Qt has deleted the completed thread wrapper.
            pass
    for task in window._init_tasks:
        assert _runner_stopped(task)
    assert all(not panel.conversion_thread.isRunning() for panel in panels)
    window.close()
    assert not window.cameras
    assert all(not frame_thread.is_alive() for frame_thread in frame_threads)


def test_mainwindow_loads_simulation_config_and_displays_camera_frames(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path)
    camera_instance = window.cameras[0]
    panel = window.camera_panels[camera_instance.identifier]
    raw_frames = QSignalSpy(camera_instance.new_frame)

    assert isinstance(camera_instance, SimulatedCamera)
    assert "[SIMULATED]" in panel.title_label.text()
    menu_titles = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert menu_titles == ["File", "Instruments", "Cameras", "Help", "View"]
    assert not window.log_console_dock.isVisible()
    assert window.log_console_dock.isFloating()
    splitter_sizes = window.main_splitter.sizes()
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert window.log_console_dock.isVisible()
    assert window.log_console_action.isChecked()
    assert window.main_splitter.sizes() == splitter_sizes
    window.log_console_dock.setFloating(False)
    assert not window.log_console_dock.isFloating()
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert not window.log_console_dock.isVisible()
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert window.log_console_dock.isVisible()
    window.log_console_dock.setFloating(True)
    assert window.log_console_dock.isFloating()
    window.log_console_dock.close()
    assert not window.log_console_action.isChecked()
    qtbot.keyClick(window, Qt.Key.Key_F12)
    assert window.log_console_dock.isVisible()
    cameras_menu = next(
        action.menu() for action in window.menuBar().actions() if action.text().replace("&", "") == "Cameras"
    )
    camera_actions = [action.text() for action in cameras_menu.actions() if action.isVisible()]
    assert any("Discover Cameras" in label for label in camera_actions)
    assert not any("Controls" in label for label in camera_actions)
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


def test_global_status_tracks_lifecycle_without_startup_or_hardware_commands(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, camera_count=2)
    manager = window.plot_widget.matlab_engine_manager

    def forbidden_startup():
        raise AssertionError("status rendering must not request MATLAB startup")

    monkeypatch.setattr(manager, "request_engine", forbidden_startup)
    window._refresh_global_status()
    assert window.ct400_status_label.text() == "CT400: SIMULATED"
    assert window.cameras_status_label.text() == "Cameras: Simulated"
    assert window.activity_status_label.text() == "Activity: Idle"

    manager._set_state(MatlabEngineState.STARTING)
    assert window.matlab_status_label.text() == "MATLAB: Starting"
    manager._set_state(MatlabEngineState.FAILED)
    assert window.matlab_status_label.text() == "MATLAB: Failed"

    for operation, expected in (
        (main_window_module.CT400OperationState.SCANNING, "Activity: Scanning"),
        (main_window_module.CT400OperationState.MONITORING, "Activity: Monitoring"),
        (main_window_module.CT400OperationState.ALIGNMENT, "Activity: Aligning"),
        (main_window_module.CT400OperationState.IDLE, "Activity: Idle"),
    ):
        window._set_ct400_operation_state(operation)
        assert window.activity_status_label.text() == expected

    panel = next(iter(window.camera_panels.values()))
    panel.recovery_started()
    assert window.cameras_status_label.text() == "Cameras: Mixed"
    panel.recovery_failed("test failure")
    assert window.cameras_status_label.text() == "Cameras: Mixed"
    panel.recovery_succeeded()
    panel.process_new_frame_data()
    assert window.cameras_status_label.text() == "Cameras: Simulated"

    _close_window_and_check_cleanup(window, qtbot)
    assert not window.log_console._timer.isActive()
    assert not window.log_console_dock.isVisible()


def test_camera_gear_controls_are_independent_and_do_not_stop_frames(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, camera_count=2)
    top = window.camera_panels["simulated-top"]
    bottom = window.camera_panels["simulated-bottom"]
    first_camera, second_camera = window.cameras
    assert top.settings_button.isVisible() and bottom.settings_button.isVisible()
    assert top.screenshot_btn.isVisible() and bottom.screenshot_btn.isVisible()
    assert len(top.view_mode_buttons) == len(bottom.view_mode_buttons) == 5
    first_count = first_camera._frame_count
    top.settings_button.click()
    assert top.controls_visible
    assert not bottom.controls_visible
    assert bottom.settings_button.isVisible()
    assert not hasattr(top, "view_mode_combo")
    assert top.view_mode_selector.isVisible()
    qtbot.waitUntil(lambda: first_camera._frame_count > first_count, timeout=1500)
    bottom.settings_button.click()
    assert bottom.controls_visible and top.controls_visible
    second_count = second_camera._frame_count
    top.settings_button.click()
    assert not top.controls_visible and bottom.controls_visible
    qtbot.waitUntil(lambda: second_camera._frame_count > second_count, timeout=1500)
    _close_window_and_check_cleanup(window, qtbot)


def test_mainwindow_closes_each_panel_once_after_stopping_its_camera(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, camera_count=2)
    cameras = {camera.identifier: camera for camera in window.cameras}
    observed = []
    original_close_event = main_window_module.CameraPanel.closeEvent

    def record_close_event(panel, event):
        camera = panel.camera
        producer_thread = camera._frame_thread
        observed.append(
            (
                panel.camera_identifier,
                not camera.is_streaming,
                producer_thread is None or not producer_thread.is_alive(),
                panel._conversion_shutdown_complete,
            )
        )
        original_close_event(panel, event)

    monkeypatch.setattr(main_window_module.CameraPanel, "closeEvent", record_close_event)
    top = window.camera_panels["simulated-top"]
    side = window.camera_panels["simulated-bottom"]
    window.close()
    window.close()

    assert observed == [
        (top.camera_identifier, True, True, False),
        (side.camera_identifier, True, True, False),
    ]
    assert not any(camera.is_streaming for camera in cameras.values())
    assert top._conversion_shutdown_complete and side._conversion_shutdown_complete
    assert not top.conversion_thread.isRunning()
    assert not side.conversion_thread.isRunning()


def test_mainwindow_close_after_fig_thread_deletion_shuts_down_fake_matlab(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path)

    class FakeMatlabEngine:
        quit_calls = 0

        def quit(self):
            self.quit_calls += 1

    engine = FakeMatlabEngine()
    manager = window.plot_widget.matlab_engine_manager
    manager._engine = engine
    manager._set_state(MatlabEngineState.READY)

    thread = QThread(window.plot_widget)
    worker = QObject()
    thread.start()
    thread.quit()
    assert thread.wait(1000)
    thread.deleteLater()
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not shiboken6.isValid(thread)
    assert not shiboken6.isValid(worker)
    window.plot_widget.matlab_save_thread = thread
    window.plot_widget.matlab_save_worker = worker

    window.close()
    window.close()

    assert engine.quit_calls == 1
    assert window.plot_widget.matlab_save_thread is None
    assert window.plot_widget.matlab_save_worker is None


def test_camera_cleanup_attempts_peer_after_one_close_failure(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, camera_count=2)
    first, second = window.cameras
    original_first_close = first.close
    original_second_close = second.close
    attempts = []

    def failing_close():
        attempts.append(first.identifier)
        original_first_close()
        raise RuntimeError("injected close failure after producer stopped")

    def recorded_second_close():
        attempts.append(second.identifier)
        original_second_close()

    monkeypatch.setattr(first, "close", failing_close)
    monkeypatch.setattr(second, "close", recorded_second_close)
    panels = list(window.camera_panels.values())

    window._cleanup_cameras()

    assert attempts == [first.identifier, second.identifier]
    assert all(not camera._frame_thread for camera in (first, second))
    assert all(panel.conversion_thread is not None and not panel.conversion_thread.isRunning() for panel in panels)
    window.close()


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


def test_simulated_acquisition_failure_recovers_once_through_watchdog(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, fail_after_frames=3)
    camera_instance = window.cameras[0]
    panel = window.camera_panels[camera_instance.identifier]
    initial_frames = QSignalSpy(camera_instance.new_frame)
    recovery_requests = QSignalSpy(panel.recovery_requested)

    qtbot.waitUntil(lambda: initial_frames.count() >= 2, timeout=2000)
    qtbot.waitUntil(lambda: not camera_instance.is_streaming, timeout=2000)
    camera_instance.fail_after_frames = None
    resumed_frames = QSignalSpy(camera_instance.new_frame)

    qtbot.waitUntil(lambda: resumed_frames.count() >= 1, timeout=7000)
    qtbot.waitUntil(lambda: camera_instance.identifier not in window._camera_recovery_tasks, timeout=2000)
    assert not panel._recovery_active, (panel._recovery_active, panel._recovery_failed, panel.video_label.text())
    assert recovery_requests.count() == 1
    qtbot.waitUntil(lambda: not panel._camera_error_active, timeout=1000)

    assert camera_instance.is_streaming
    assert not panel._recovery_active
    assert not panel._automatic_recovery_used
    assert camera_instance.identifier not in window._camera_recovery_tasks
    _close_window_and_check_cleanup(window, qtbot)


def test_two_simulated_cameras_survive_resize_cinema_and_control_visibility(qtbot, monkeypatch, tmp_path):
    window = _start_main_window(qtbot, monkeypatch, tmp_path, camera_count=2)
    panels = list(window.camera_panels.values())
    for index, panel in enumerate(panels):
        panel.config.backend = "vimba"
        height = 964 if index == 0 else 480
        panel.camera.get_roi = lambda _height=height: ROI(1292, _height, 0, (964 - _height) // 2)
        panel.camera.get_capabilities = lambda: {
            "features": {
                "width_max": {"value": 1292},
                "height_max": {"value": 964},
                "offset_x": {"minimum": 0, "increment": 1},
                "offset_y": {"minimum": 0, "increment": 1},
            }
        }
        panel.camera.apply_view_mode = lambda *_args, **_kwargs: None
        panel._refresh_view_mode_from_camera()
    qtbot.waitUntil(lambda: all(panel._latest_pixmap is not None for panel in panels), timeout=2000)

    window.resize(1400, 900)
    qtbot.wait(30)
    for panel in panels:
        panel.set_controls_visibility(True)
        qtbot.wait(15)
        assert not hasattr(panel, "view_mode_combo")
        assert panel.view_mode_selector.isVisible()
        assert [panel.view_mode_buttons[h].text() for h in (964, 720, 480, 240, 120)] == [
            "Full",
            "720",
            "480",
            "240",
            "120",
        ]
        assert all(button.isVisible() for button in panel.view_mode_buttons.values())
        assert panel.view_mode_button_group.checkedId() == (964 if panel is panels[0] else 480)
        assert panel.view_mode_apply.width() == 68
        slider_widths = [
            panel.exposure_control.slider.width(),
            panel.gain_control.slider.width(),
            panel.gamma_control.slider.width(),
        ]
        assert max(slider_widths) - min(slider_widths) <= 1
        overlay_rect = panel.overlay_actions.geometry()
        assert overlay_rect.right() >= panel.video_container.width() - 3
        assert overlay_rect.top() <= 3
        assert panel.screenshot_btn.isVisible() and panel.settings_button.isVisible()
        assert panel.controls_container.isVisible()
        if panel is panels[0]:
            panel.grab().save(str(tmp_path / "camera-controls-smoke.png"))
        panel.set_controls_visibility(False)
        assert not panel.controls_container.isVisible()
        pixmap = panel._latest_pixmap
        assert (pixmap.width(), pixmap.height()) == (16, 12)

    was_visible = window.control_container.isVisible()
    window.toggle_cinema_mode()
    assert window.control_container.isVisible() is not was_visible
    for panel in panels:
        panel.set_controls_visibility(True)
        assert panel.view_mode_selector.isVisible()
    window.resize(1000, 700)
    window.toggle_cinema_mode()
    qtbot.wait(30)
    assert window.control_container.isVisible() is was_visible
    for panel in panels:
        overlay_rect = panel.overlay_actions.geometry()
        assert overlay_rect.right() >= panel.video_container.width() - 3
        assert overlay_rect.top() <= 3

    window.showMaximized()
    qtbot.wait(30)
    assert window.isMaximized()
    assert all(panel.video_container.isVisible() for panel in panels)
    assert all(panel.view_mode_selector.isVisible() for panel in panels)

    _close_window_and_check_cleanup(window, qtbot)
