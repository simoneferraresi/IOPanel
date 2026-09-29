import threading

import numpy as np
from PySide6.QtCore import QThread, Qt
from PySide6.QtTest import QSignalSpy

import app
from config_model import AppConfig
from hardware import camera_init_worker, ct400_init_worker
from hardware.dummy_ct400 import DummyCT400
from hardware.simulated_camera import SimulatedCamera
from ui import control_panel as control_panel_module
from ui import main_window as main_window_module


class GatedDummyCT400(DummyCT400):
    """Use an explicit test gate to keep ScanWorker active until assertions finish."""

    def __init__(self, progression_gate, scan_error=None):
        super().__init__(scan_duration=0, scan_error=scan_error, wait_gate=progression_gate)
        self.progression_gate = progression_gate


def _qt_thread_stopped(thread):
    try:
        return not thread.isRunning()
    except RuntimeError:
        return True


def _start_application(qtbot, monkeypatch, tmp_path, scan_error=None):
    dialog_messages = []
    monkeypatch.setattr(
        control_panel_module.QMessageBox,
        "critical",
        lambda *_args: dialog_messages.append(_args[-1]),
    )
    monkeypatch.setattr(
        control_panel_module.QMessageBox,
        "warning",
        lambda *_args: dialog_messages.append(_args[-1]),
    )
    config_path = tmp_path / "concurrent-simulated-acquisition.ini"
    config_path.write_text(
        """[App]
name = Concurrent acquisition integration test

[Instruments]
ct400_backend = simulation

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

    # Explicitly select simulation and fail if production initialization tries
    # to discover/open a physical CT400 driver, even when one is installed.
    def fail_if_physical_ct400_discovery_is_attempted(_worker):
        raise AssertionError("physical CT400 discovery attempted for explicit simulation backend")

    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", fail_if_physical_ct400_discovery_is_attempted)
    # Control scan progression with a thread-safe event; scan setup/data/cleanup
    # still run through the production ScanWorker and DummyCT400 interfaces.
    progression_gate = threading.Event()
    monkeypatch.setattr(
        ct400_init_worker,
        "DummyCT400",
        lambda: GatedDummyCT400(progression_gate, scan_error=scan_error),
    )

    # Guard all physical camera startup. The explicitly simulated backend
    # should not request Vimba initialization, regardless of installed drivers.
    vimba_start_attempts = []
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_start_vimbasystem",
        lambda _window: vimba_start_attempts.append(True),
    )

    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: len(window.cameras) == 1, timeout=4000)
    qtbot.waitUntil(lambda: isinstance(window.ct400_device, DummyCT400), timeout=4000)
    qtbot.waitUntil(lambda: window.control_panel.scan_btn.isEnabled(), timeout=2000)
    camera = window.cameras[0]
    assert isinstance(camera, SimulatedCamera)
    assert not vimba_start_attempts

    panel = window.control_panel
    panel.initial_wl.setText("1550.0")
    panel.final_wl.setText("1550.004")
    panel.resolution.setText("1")
    frame_spy = QSignalSpy(camera.new_frame)
    qtbot.waitUntil(lambda: frame_spy.count() >= 2, timeout=2000)
    qtbot.waitUntil(lambda: window.camera_panels[camera.identifier]._latest_pixmap is not None, timeout=2000)
    return window, progression_gate, frame_spy, dialog_messages


def _start_scan_during_stream(qtbot, window, frame_spy):
    control = window.control_panel
    camera = window.cameras[0]
    camera_panel = window.camera_panels[camera.identifier]
    initial_display_pixel = camera_panel._latest_pixmap.toImage().pixelColor(0, 0).value()
    camera_frame_count = frame_spy.count()
    qtbot.mouseClick(control.scan_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: control.scanning and control.scan_thread is not None and control.scan_thread.isRunning(),
        timeout=2500,
    )
    assert control.scan_btn.property("scanning") is True
    qtbot.waitUntil(
        lambda: (
            frame_spy.count() >= camera_frame_count + 2
            and control.scanning
            and window.ct400_device._is_scanning
            and camera_panel._latest_pixmap.toImage().pixelColor(0, 0).value() != initial_display_pixel
        ),
        timeout=3000,
    )
    return camera_frame_count


def test_mainwindow_scan_updates_real_plot_while_camera_frames_continue(qtbot, monkeypatch, tmp_path):
    window, progression_gate, frame_spy, dialog_messages = _start_application(qtbot, monkeypatch, tmp_path)
    camera = window.cameras[0]
    camera_thread = camera._frame_thread
    camera_panel = window.camera_panels[camera.identifier]
    control = window.control_panel
    dummy = window.ct400_device

    try:
        assert "SIMULATED" in window.ct400_status_label.text()
        assert "[SIMULATED]" in camera_panel.title_label.text()

        _start_scan_during_stream(qtbot, window, frame_spy)
        assert not dialog_messages
        assert dummy._is_scanning
        assert np.isclose(dummy._scan_min_wavelength, 1550.0)
        assert np.isclose(dummy._scan_max_wavelength, 1550.004)
        assert dummy._sampling_resolution_pm == 1

        frames_during_scan = frame_spy.count()
        scan_thread = control.scan_thread
        progression_gate.set()
        qtbot.waitUntil(lambda: not control.scanning, timeout=3000)
        assert control.scan_btn.property("scanning") is False
        qtbot.waitUntil(lambda: _qt_thread_stopped(scan_thread), timeout=1500)

        expected_wavelengths = 1550.0 + np.arange(5) * 0.001
        expected_center = 1550.002
        expected_width = 0.004 / 6
        expected_powers = -10 * np.exp(-((expected_wavelengths - expected_center) ** 2) / (2 * expected_width**2)) - 30
        np.testing.assert_allclose(window.plot_widget.current_wavelengths, expected_wavelengths)
        assert window.plot_widget.current_powers.shape == (5,)
        np.testing.assert_allclose(window.plot_widget.current_powers, expected_powers)
        assert window.plot_widget.current_output_power == -20.0
        assert "SIMULATED CT400 DATA" in window.plot_widget.plot_widget.getPlotItem().titleLabel.text
        plotted_x, plotted_y = window.plot_widget.plot_data_item.getData()
        np.testing.assert_allclose(plotted_x, expected_wavelengths)
        np.testing.assert_allclose(plotted_y, window.plot_widget.current_powers)
        qtbot.waitUntil(lambda: frame_spy.count() > frames_during_scan, timeout=1500)
        assert camera.is_streaming
        assert not dummy._is_scanning

    finally:
        # Do not strand the worker if an assertion fails before the normal
        # progression release; closeEvent then has a bounded shutdown path.
        progression_gate.set()
        window.close()

    qtbot.waitUntil(lambda: not camera_thread.is_alive(), timeout=1500)
    assert not window.cameras


def test_scan_failure_is_reported_while_camera_keeps_streaming(qtbot, monkeypatch, tmp_path):
    window, progression_gate, frame_spy, dialog_messages = _start_application(
        qtbot,
        monkeypatch,
        tmp_path,
        scan_error="controlled DummyCT400 failure",
    )
    camera = window.cameras[0]
    camera_thread = camera._frame_thread
    dummy = window.ct400_device
    control = window.control_panel

    try:
        _start_scan_during_stream(qtbot, window, frame_spy)
        scan_thread = control.scan_thread
        frames_during_scan = frame_spy.count()
        progression_gate.set()
        qtbot.waitUntil(lambda: bool(dialog_messages), timeout=2500)
        qtbot.waitUntil(lambda: not control.scanning, timeout=2500)
        qtbot.waitUntil(lambda: _qt_thread_stopped(scan_thread), timeout=1500)
        qtbot.waitUntil(lambda: control.scan_thread is None, timeout=1500)

        assert "controlled DummyCT400 failure" in dialog_messages[0]
        qtbot.waitUntil(lambda: frame_spy.count() > frames_during_scan, timeout=1500)
        assert camera.is_streaming
        assert not dummy._is_scanning
        assert window.plot_widget.current_wavelengths is None
    finally:
        progression_gate.set()
        window.close()

    qtbot.waitUntil(lambda: not camera_thread.is_alive(), timeout=1500)


def test_cancel_and_close_stop_scan_without_stopping_camera_early(qtbot, monkeypatch, tmp_path):
    window, _progression_gate, frame_spy, dialog_messages = _start_application(qtbot, monkeypatch, tmp_path)
    camera = window.cameras[0]
    camera_thread = camera._frame_thread
    camera_panel = window.camera_panels[camera.identifier]
    dummy = window.ct400_device
    control = window.control_panel

    _start_scan_during_stream(qtbot, window, frame_spy)
    cancelled_scan_thread = control.scan_thread
    frames_before_cancel = frame_spy.count()
    qtbot.mouseClick(control.scan_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: _qt_thread_stopped(cancelled_scan_thread), timeout=2500)
    qtbot.waitUntil(lambda: control.scan_thread is None, timeout=1500)
    assert not dialog_messages
    assert camera.is_streaming
    assert not dummy._is_scanning
    qtbot.waitUntil(lambda: frame_spy.count() > frames_before_cancel, timeout=1500)

    _start_scan_during_stream(qtbot, window, frame_spy)
    active_scan_thread = control.scan_thread
    frames_before_close = frame_spy.count()
    close_during_wait = []
    original_close = dummy.close
    stops_before_close = dummy.stop_scan_calls

    def record_close():
        try:
            close_during_wait.append(active_scan_thread.isRunning())
        except RuntimeError:
            close_during_wait.append(False)
        original_close()

    dummy.close = record_close
    window.is_ct400_connected_state = True
    scan_terminations = []
    original_terminate = QThread.terminate

    def record_scan_termination(thread):
        if thread is active_scan_thread:
            scan_terminations.append(True)
        return original_terminate(thread)

    monkeypatch.setattr(QThread, "terminate", record_scan_termination)
    window.close()
    assert window.isVisible()
    assert dummy.stop_scan_calls == stops_before_close + 1
    assert close_during_wait == []
    assert control.scanning
    _progression_gate.set()

    qtbot.waitUntil(lambda: _qt_thread_stopped(active_scan_thread), timeout=1500)
    qtbot.waitUntil(lambda: not camera_thread.is_alive(), timeout=1500)
    qtbot.waitUntil(lambda: bool(close_during_wait), timeout=1500)
    assert frame_spy.count() >= frames_before_close
    assert close_during_wait == [False]
    assert scan_terminations == []
    assert not dummy._is_scanning
    assert not dummy._laser_enabled
    assert not camera.is_streaming
    assert not window.cameras
    assert _qt_thread_stopped(camera_panel.conversion_thread)
