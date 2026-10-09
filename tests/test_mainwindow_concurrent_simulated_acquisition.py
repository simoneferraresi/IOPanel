import threading
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QSignalSpy

import app
from config_model import AppConfig
from hardware import ct400_init_worker
from hardware.ct400 import CT400
from hardware.ct400_types import Detector, Enable, LaserInput
from hardware.dummy_ct400 import DummyCT400
from hardware.simulated_camera import SimulatedCamera
from ui import alignment_panel as alignment_panel_module
from ui import control_panel as control_panel_module
from ui import main_window as main_window_module


class GatedDummyCT400(DummyCT400):
    """Use an explicit test gate to keep ScanWorker active until assertions finish."""

    def __init__(self, progression_gate, scan_error=None):
        super().__init__(scan_duration=0, scan_error=scan_error, wait_gate=progression_gate)
        self.progression_gate = progression_gate


class FakeAlignmentPiezo:
    VOLTS_PER_NM = 0.001

    def __init__(self, port):
        self.port = port

    def get_voltage(self, _axis):
        return 0.0

    def set_voltage(self, _axis, _value):
        return None

    def move_nm(self, _axis, _distance):
        return None

    def get_min_voltage(self, _axis):
        return -100.0

    def get_max_voltage(self, _axis):
        return 100.0


def _prepare_alignment_panel(window, device=None):
    panel = window.alignment_tab
    panel.set_hardware(
        device or window.ct400_device,
        FakeAlignmentPiezo("fake-left"),
        FakeAlignmentPiezo("fake-right"),
    )
    return panel


def _arm_blocking_alignment(panel, release, started, events, fail_read=False):
    worker = panel.alignment_worker

    def prepare(settings):
        events.append(("laser-enable", settings.input_port))
        started.set()
        if not release.wait(5):
            raise TimeoutError("test did not release alignment worker")

    def read_power(_samples):
        events.append(("get-all-powers",))
        if fail_read:
            raise RuntimeError("fake alignment read failure")
        return -20.0

    def shutdown(settings):
        events.append(("laser-disable", settings.input_port))

    worker._prepare_laser = prepare
    worker._read_power = read_power
    worker._shutdown_laser = shutdown

    def record_worker_finished():
        # Observe the cleanup guarantee independently of MainWindow's receiver
        # on the same signal. Qt receiver delivery order is not a contract.
        laser_disable_attempted = any(event[0] == "laser-disable" for event in events)
        events.append(("cleanup-complete", laser_disable_attempted))

    worker.operation_finished.connect(record_worker_finished, Qt.ConnectionType.DirectConnection)
    panel.operation_finished.connect(
        lambda: events.append(("ownership-idle", window_state(panel))), Qt.ConnectionType.DirectConnection
    )
    return worker


def window_state(panel):
    window = panel.window()
    return window._ct400_operation_state.name


def _qt_thread_stopped(thread):
    try:
        return not thread.isRunning()
    except RuntimeError:
        return True


def _fake_physical_ct400(events):
    device = CT400.__new__(CT400)
    device.handle = 73
    device.dll = SimpleNamespace(
        CT400_Close=lambda handle: events.append(("close", handle)) or 0,
    )
    device.cmd_laser = lambda **kwargs: events.append(("cmd_laser", kwargs))
    return device


def _fake_blocking_connection_ct400(events, release, operation_started, native_operation_active, failure):
    device = CT400.__new__(CT400)
    device.handle = 73
    laser_call_count = 0

    def block_native_call(name, kwargs):
        nonlocal laser_call_count
        if name == "laser":
            laser_call_count += 1
        call_number = laser_call_count
        events.append((f"{name}_started", call_number, kwargs))
        native_operation_active.set()
        operation_started.set()
        try:
            if not release.wait(5):
                raise TimeoutError("test did not release fake CT400 operation")
            if (failure == "connect" and name == "connect") or (
                failure == "disconnect" and name == "laser" and call_number == 1
            ):
                raise RuntimeError(f"fake {name} operation failed")
            events.append((f"{name}_completed", call_number, kwargs))
        finally:
            native_operation_active.clear()

    device.set_laser = lambda *args, **kwargs: block_native_call("connect", kwargs)
    device.cmd_laser = lambda *args, **kwargs: block_native_call("laser", kwargs)
    device.dll = SimpleNamespace(
        CT400_Close=lambda handle: events.append(("close", handle, native_operation_active.is_set())) or 0,
    )
    return device


def _laser_call_input(call):
    args, kwargs = call
    return kwargs.get("laser_input", args[0] if args else None)


def _laser_call_enable(call):
    args, kwargs = call
    return kwargs.get("enable", args[1] if len(args) > 1 else None)


def _start_application(qtbot, monkeypatch, tmp_path, scan_error=None, *, include_camera=True):
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
    camera_config = (
        """[Camera:Top]
identifier = simulated-top
enabled = true
name = Top camera
backend = simulation
simulation_width = 16
simulation_height = 12
"""
        if include_camera
        else ""
    )
    config_path.write_text(
        """[App]
name = Concurrent acquisition integration test

[Instruments]
ct400_backend = simulation
"""
        + camera_config,
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
    qtbot.waitUntil(lambda: isinstance(window.ct400_device, DummyCT400), timeout=4000)
    qtbot.waitUntil(lambda: window.control_panel.scan_btn.isEnabled(), timeout=2000)
    assert not vimba_start_attempts

    panel = window.control_panel
    panel.initial_wl.setText("1550.0")
    panel.final_wl.setText("1550.004")
    panel.resolution.setText("1")
    frame_spy = None
    if include_camera:
        qtbot.waitUntil(lambda: len(window.cameras) == 1, timeout=4000)
        camera = window.cameras[0]
        assert isinstance(camera, SimulatedCamera)
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


def test_scan_owns_ct400_and_blocks_monitor_and_detector_writes(qtbot, monkeypatch, tmp_path):
    window, gate, frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path)
    scan = window.control_panel
    monitor = window.histogram_control
    device = window.ct400_device
    detector_writes = []
    device.set_detector_array = lambda *args: detector_writes.append(args)

    try:
        # Make the action available in this explicit DummyCT400 test so the
        # coordinator's SCANNING gate itself is exercised.
        window.ct400_connect_action.setEnabled(True)
        _start_scan_during_stream(qtbot, window, frame_spy)
        assert window._ct400_operation_state.name == "SCANNING"
        file_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "&File")
        open_scan_action = next(action for action in file_menu.actions() if action.objectName() == "openScanAction")
        assert not open_scan_action.isEnabled()
        assert not window.plot_widget.load_btn.isEnabled()
        dialog_attempts = []
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getOpenFileName",
            lambda *_args, **_kwargs: dialog_attempts.append(True) or ("unused.csv", ""),
        )
        window._open_scan_file()
        assert dialog_attempts == []
        assert not monitor.monitor_btn.isEnabled()
        assert not window.ct400_connect_action.isEnabled()
        assert scan.scan_btn.isEnabled()
        assert scan.scan_btn.text() == "Stop Scan"

        # Programmatic checkbox changes still emit the callback; its ownership
        # guard must prevent an independent native detector write.
        monitor.detector_cbs[0].setChecked(not monitor.detector_cbs[0].isChecked())
        # Scan setup explicitly applies the GUI's DE1-only selection once;
        # monitor ownership prevents any additional detector-array write.
        assert detector_writes == [(Enable.DISABLE, Enable.DISABLE, Enable.DISABLE, Enable.DISABLE)]

        scan_thread = scan.scan_thread
        gate.set()
        qtbot.waitUntil(lambda: not scan.scanning, timeout=2500)
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=1000)
        qtbot.waitUntil(lambda: _qt_thread_stopped(scan_thread), timeout=1500)
        assert open_scan_action.isEnabled()
        assert window.plot_widget.load_btn.isEnabled()
        assert monitor.monitor_btn.isEnabled()
    finally:
        gate.set()
        if window.isVisible():
            window.close()


def test_monitor_owns_ct400_until_selected_input_cleanup_finishes(qtbot, monkeypatch, tmp_path):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    monitor = window.histogram_control
    scan = window.control_panel
    device = window.ct400_device
    monitor.on_instrument_connected(True)
    index = monitor.input_port.findData(LaserInput.LI_4)
    monitor.input_port.setCurrentIndex(index)
    disable_states = []
    original_cmd = device.cmd_laser

    def record_laser(*args, **kwargs):
        if _laser_call_enable((args, kwargs)) is Enable.DISABLE:
            disable_states.append(window._ct400_operation_state.name)
        return original_cmd(*args, **kwargs)

    device.cmd_laser = record_laser
    try:
        window.ct400_connect_action.setEnabled(True)
        monitor._start_monitoring()
        assert window._ct400_operation_state.name == "MONITORING"
        assert not scan.scan_btn.isEnabled()
        assert not window.ct400_connect_action.isEnabled()
        assert monitor.monitor_btn.isEnabled()
        monitor._stop_monitoring()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=1500)
        assert disable_states == ["MONITORING"]
        assert _laser_call_input(device.cmd_laser_calls[-1]) == LaserInput.LI_4
        assert _laser_call_enable(device.cmd_laser_calls[-1]) == Enable.DISABLE
        assert scan.scan_btn.isEnabled()

        original_set_detectors = device.set_detector_array
        device.set_detector_array = lambda *_args: (_ for _ in ()).throw(RuntimeError("fake setup failure"))
        monitor._start_monitoring()
        assert window._ct400_operation_state.name == "IDLE"
        assert not monitor.monitoring
        assert scan.scan_btn.isEnabled()
        device.set_detector_array = original_set_detectors
    finally:
        monitor.cleanup_worker_thread()
        if window.isVisible():
            window.close()


def test_scan_ownership_blocks_alignment_start(qtbot, monkeypatch, tmp_path):
    window, gate, frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path)
    alignment = _prepare_alignment_panel(window)
    starts = QSignalSpy(alignment.operation_started)

    try:
        _start_scan_during_stream(qtbot, window, frame_spy)
        assert window._ct400_operation_state.name == "SCANNING"
        assert not alignment.align_button.isEnabled()
        assert not alignment.spiral_align_button.isEnabled()
        assert not alignment.map_button.isEnabled()
        assert window.control_panel.scan_btn.isEnabled()
        alignment.toggle_alignment(True)
        alignment.toggle_spiral_alignment()
        alignment.toggle_mapping(True)
        assert starts.count() == 0

        gate.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=3000)
    finally:
        gate.set()
        if window.isVisible():
            window.close()


def test_monitoring_ownership_blocks_alignment_and_keeps_monitor_stop(qtbot, monkeypatch, tmp_path):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    alignment = _prepare_alignment_panel(window)
    monitor = window.histogram_control
    starts = QSignalSpy(alignment.operation_started)

    try:
        monitor._start_monitoring()
        assert window._ct400_operation_state.name == "MONITORING"
        assert monitor.monitor_btn.isEnabled()
        assert "Stop" in monitor.monitor_btn.text()
        assert not alignment.align_button.isEnabled()
        alignment.toggle_alignment(True)
        alignment.toggle_spiral_alignment()
        alignment.toggle_mapping(True)
        assert starts.count() == 0

        monitor._stop_monitoring()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=2000)
    finally:
        monitor._stop_monitoring()
        if window.isVisible():
            window.close()


@pytest.mark.parametrize("mode", ["fine", "spiral", "mapping"])
def test_alignment_modes_own_ct400_until_post_cleanup(qtbot, monkeypatch, tmp_path, mode):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    alignment = _prepare_alignment_panel(window)
    release = threading.Event()
    started = threading.Event()
    events = []
    _arm_blocking_alignment(alignment, release, started, events)
    monitor = window.histogram_control
    detector_writes = []
    monitor.ct400.set_detector_array = lambda *args: detector_writes.append(args)

    try:
        window.ct400_connect_action.setEnabled(True)
        if mode == "fine":
            qtbot.mouseClick(alignment.align_button, Qt.MouseButton.LeftButton)
        elif mode == "spiral":
            qtbot.mouseClick(alignment.spiral_align_button, Qt.MouseButton.LeftButton)
        else:
            qtbot.mouseClick(alignment.map_button, Qt.MouseButton.LeftButton)

        qtbot.waitUntil(started.is_set, timeout=1500)
        assert window._ct400_operation_state.name == "ALIGNMENT"
        assert not window.control_panel.scan_btn.isEnabled()
        assert not monitor.monitor_btn.isEnabled()
        assert not window.ct400_connect_action.isEnabled()
        assert alignment.stop_operation_button.isEnabled()
        assert not alignment.align_button.isEnabled()
        assert not alignment.spiral_align_button.isEnabled()
        assert not alignment.map_button.isEnabled()

        monitor.detector_cbs[0].setChecked(not monitor.detector_cbs[0].isChecked())
        assert detector_writes == []

        alignment.request_stop()
        assert window._ct400_operation_state.name == "ALIGNMENT"
        assert not any(event[0] == "laser-disable" for event in events)
        release.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=3000)
        event_names = [event[0] for event in events]
        assert event_names.index("laser-enable") < event_names.index("laser-disable")
        cleanup_events = [event for event in events if event[0] == "cleanup-complete"]
        assert cleanup_events == [("cleanup-complete", True)]
        assert event_names.index("laser-disable") < event_names.index("ownership-idle")
        ownership_events = [event for event in events if event[0] == "ownership-idle"]
        assert ownership_events == [("ownership-idle", "IDLE")]
    finally:
        release.set()
        if window.isVisible():
            window.close()


def test_single_fine_alignment_click_starts_one_owned_operation(qtbot, monkeypatch, tmp_path):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    alignment = _prepare_alignment_panel(window)
    release = threading.Event()
    started = threading.Event()
    events = []
    _arm_blocking_alignment(alignment, release, started, events)
    operation_spy = QSignalSpy(alignment.operation_started)
    worker_request_spy = QSignalSpy(alignment.start_alignment_requested)

    try:
        qtbot.mouseClick(alignment.align_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(started.is_set, timeout=1500)

        assert operation_spy.count() == 1
        assert worker_request_spy.count() == 1
        assert [event for event in events if event[0] == "laser-enable"] == [
            ("laser-enable", alignment.input_port_combo.currentData())
        ]
        assert window._ct400_operation_state.name == "ALIGNMENT"
        assert alignment._active_mode == "fine alignment"
        assert alignment.align_button.isChecked()
        assert alignment.stop_operation_button.isEnabled()

        alignment.request_stop()
        assert window._ct400_operation_state.name == "ALIGNMENT"
        release.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=3000)
        assert not alignment.align_button.isChecked()
        assert not alignment.stop_operation_button.isEnabled()
    finally:
        release.set()
        if window.isVisible():
            window.close()


def test_alignment_error_disables_selected_input_before_releasing_owner(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(alignment_panel_module.QMessageBox, "critical", lambda *_args: None)
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    alignment = _prepare_alignment_panel(window)
    release = threading.Event()
    started = threading.Event()
    events = []
    _arm_blocking_alignment(alignment, release, started, events, fail_read=True)

    try:
        qtbot.mouseClick(alignment.align_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(started.is_set, timeout=1500)
        release.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=3000)
        event_names = [event[0] for event in events]
        assert "get-all-powers" in event_names
        assert event_names.index("get-all-powers") < event_names.index("laser-disable")
        cleanup_events = [event for event in events if event[0] == "cleanup-complete"]
        assert cleanup_events == [("cleanup-complete", True)]
        assert event_names.index("laser-disable") < event_names.index("ownership-idle")
    finally:
        release.set()
        if window.isVisible():
            window.close()


def test_close_during_alignment_waits_for_cleanup_before_ct400_close(qtbot, monkeypatch, tmp_path):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    events = []
    device = _fake_physical_ct400(events)
    window.ct400_device = device
    alignment = _prepare_alignment_panel(window, device)
    window._handle_ct400_connection_success("Connected", LaserInput.LI_3)
    release = threading.Event()
    started = threading.Event()
    _arm_blocking_alignment(alignment, release, started, events)
    alignment.input_port_combo.setCurrentIndex(alignment.input_port_combo.findData(3))
    terminations = []
    original_terminate = QThread.terminate

    def record_terminate(thread):
        if thread is alignment.worker_thread:
            terminations.append(True)
        return original_terminate(thread)

    monkeypatch.setattr(QThread, "terminate", record_terminate)

    try:
        qtbot.mouseClick(alignment.align_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(started.is_set, timeout=1500)
        worker_thread = alignment.worker_thread
        assert worker_thread is not None
        thread_finished_spy = QSignalSpy(worker_thread.finished)
        window.close()
        assert window.isVisible()
        assert window._pending_ct400_operation_close
        assert window._ct400_operation_state.name == "ALIGNMENT"
        assert not any(event[0] == "close" for event in events)
        alignment.request_stop()
        release.set()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=3500)

        names = [event[0] for event in events]
        cleanup_events = [event for event in events if event[0] == "cleanup-complete"]
        assert cleanup_events == [("cleanup-complete", True)]
        assert names.index("laser-disable") < names.index("ownership-idle")
        assert names.index("ownership-idle") < names.index("close")
        assert ("laser-disable", 3) in events
        assert events[-1] == ("close", 73)
        assert terminations == []
        qtbot.waitUntil(lambda: thread_finished_spy.count() == 1, timeout=1500)
        assert thread_finished_spy.count() == 1
        assert alignment.worker_thread is None
        assert alignment.alignment_worker is None
    finally:
        release.set()
        if window.isVisible():
            window.close()


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

        control.motor_speed.setText("7")
        control.laser_power.setText("3")
        control.power_unit.setCurrentText("dBm")
        set_scan_calls = []
        original_set_scan = dummy.set_scan

        def record_set_scan(*args):
            set_scan_calls.append(args)
            original_set_scan(*args)

        dummy.set_scan = record_set_scan
        _start_scan_during_stream(qtbot, window, frame_spy)
        # Edits made while the acquisition is in progress belong to the next request.
        control.resolution.setText("999")
        control.motor_speed.setText("99")
        control.laser_power.setText("42")
        control.power_unit.setCurrentText("mW")
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

        measurement = window.plot_widget.current_measurement
        assert measurement is not None
        assert measurement.settings.requested_start_wavelength_nm == 1550.0
        assert measurement.settings.requested_end_wavelength_nm == 1550.004
        assert measurement.settings.requested_resolution_pm == 1
        assert measurement.settings.requested_speed_nm_s == "7"
        assert measurement.settings.entered_laser_power == "3"
        assert measurement.settings.entered_laser_power_unit == "dBm"
        assert measurement.settings.laser_power_mw == pytest.approx(10**0.3)
        assert set_scan_calls[0][0] == pytest.approx(10**0.3)
        assert measurement.detectors == (Detector.DE_1,)
        assert measurement.detector_unit is None
        assert measurement.simulated is True

        expected_wavelengths = 1550.0 + np.arange(5) * 0.001
        expected_center = 1550.002
        expected_width = 0.004 / 6
        expected_powers = -10 * np.exp(-((expected_wavelengths - expected_center) ** 2) / (2 * expected_width**2)) - 30
        np.testing.assert_allclose(window.plot_widget.current_wavelengths, expected_wavelengths)
        assert window.plot_widget.current_powers.shape == (5,)
        np.testing.assert_allclose(window.plot_widget.current_powers, expected_powers)
        assert window.plot_widget.current_output_power == -20.0
        assert "SIMULATED CT400 DATA" in window.plot_widget.plot_widget.getPlotItem().titleLabel.text
        plotted_x, plotted_y = window.plot_widget.detector_plot_items[Detector.DE_1].getData()
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
    physical_events = []
    stops_before_close = dummy.stop_scan_calls

    physical_device = _fake_physical_ct400(physical_events)

    def record_physical_close(handle):
        try:
            close_during_wait.append(active_scan_thread.isRunning())
        except RuntimeError:
            close_during_wait.append(False)
        physical_events.append(("close", handle))
        return 0

    physical_device.dll.CT400_Close = record_physical_close
    window.ct400_device = physical_device

    def record_configured_disable(**kwargs):
        try:
            physical_events.append(("disable_during_scan", active_scan_thread.isRunning(), kwargs))
        except RuntimeError:
            physical_events.append(("disable_during_scan", False, kwargs))

    physical_device.cmd_laser = record_configured_disable
    window.is_ct400_connected_state = True
    window._ct400_cleanup_laser_input = LaserInput(window.config.scan_defaults.input_port)
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
    assert physical_events[0][0] == "disable_during_scan"
    assert physical_events[0][1] is False
    assert physical_events[1] == ("close", 73)
    assert scan_terminations == []
    assert not dummy._is_scanning
    assert not dummy._laser_enabled
    assert not camera.is_streaming
    assert not window.cameras
    assert _qt_thread_stopped(camera_panel.conversion_thread)


@pytest.mark.parametrize("connected", [True, False])
def test_shutdown_releases_initialized_ct400_and_only_disables_when_connected(qtbot, monkeypatch, tmp_path, connected):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    events = []
    device = _fake_physical_ct400(events)
    window.ct400_device = device
    window.config.scan_defaults.input_port = 3
    window.config.scan_defaults.safe_parking_wavelength = 1532.5
    window.config.scan_defaults.laser_power = 0.75
    window.is_ct400_connected_state = connected
    if connected:
        # A successful reconnect must undo any previously confirmed
        # disconnected state and restore configured-input cleanup at exit.
        window._handle_ct400_disconnection_success("CT400 Disconnected")
        assert not window._ct400_connection_configured
        window._handle_ct400_connection_success("CT400 Connected")
        assert window._ct400_connection_configured
    else:
        # Model the successful GUI Disconnect operation, which already sent
        # the configured-input disable before shutdown begins.
        window._ct400_connection_configured = True
        device.cmd_laser(
            laser_input=LaserInput.LI_3,
            enable=Enable.DISABLE,
            wavelength=1532.5,
            power=0.75,
        )
        window._handle_ct400_disconnection_success("CT400 Disconnected")

    try:
        window.close()
        if connected:
            assert events == [
                (
                    "cmd_laser",
                    {
                        "laser_input": LaserInput.LI_3,
                        "enable": Enable.DISABLE,
                        "wavelength": 1532.5,
                        "power": 0.75,
                    },
                ),
                ("close", 73),
            ]
        else:
            assert events == [
                (
                    "cmd_laser",
                    {
                        "laser_input": LaserInput.LI_3,
                        "enable": Enable.DISABLE,
                        "wavelength": 1532.5,
                        "power": 0.75,
                    },
                ),
                ("close", 73),
            ]
        assert device.handle is None
    finally:
        if window.isVisible():
            window.close()


def test_shutdown_without_ct400_is_harmless(qtbot, monkeypatch, tmp_path):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    window.ct400_device = None

    window.close()

    assert not window.isVisible()


def test_failed_disconnect_is_not_treated_as_confirmed_and_shutdown_retries_disable(qtbot, monkeypatch, tmp_path):
    warnings = []
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )
    events = []
    device = _fake_physical_ct400(events)

    def failed_disable(**kwargs):
        events.append(("disable_failed", kwargs))
        raise RuntimeError("native disable returned failure")

    device.cmd_laser = failed_disable
    window.ct400_device = device
    window._ct400_connection_configured = True
    window._ct400_cleanup_laser_input = LaserInput(window.config.scan_defaults.input_port)
    window.is_ct400_connected_state = False
    window._handle_ct400_disconnection_failure("GUI Disconnect failed")
    assert window._ct400_connection_configured

    window.close()

    assert events[0][0] == "disable_failed"
    assert events[1] == ("close", 73)
    assert device.handle is None
    assert len(warnings) == 1
    assert warnings[0][1] == "Laser Disable Not Confirmed"
    assert "CT400 native resource close is a separate operation" in warnings[0][2]


def test_monitor_stop_and_active_cleanup_disable_its_selected_input(qtbot, monkeypatch, tmp_path):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    panel = window.histogram_control
    device = window.ct400_device
    panel.ct400 = device
    panel.is_instrument_connected = True
    index = panel.input_port.findData(LaserInput.LI_4)
    assert index >= 0
    panel.input_port.setCurrentIndex(index)

    try:
        panel._start_monitoring()
        assert _laser_call_input(device.cmd_laser_calls[-1]) == LaserInput.LI_4
        assert _laser_call_enable(device.cmd_laser_calls[-1]) == Enable.ENABLE
        assert window._ct400_operation_state.name == "MONITORING"
        panel._stop_monitoring()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=1500)
        assert _laser_call_input(device.cmd_laser_calls[-1]) == LaserInput.LI_4
        assert _laser_call_enable(device.cmd_laser_calls[-1]) == Enable.DISABLE

        panel._start_monitoring()
        assert window._ct400_operation_state.name == "MONITORING"
        window.close()
        assert window.isVisible()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=2500)
        assert _laser_call_input(device.cmd_laser_calls[-1]) == LaserInput.LI_4
        assert _laser_call_enable(device.cmd_laser_calls[-1]) == Enable.DISABLE
    finally:
        if window.isVisible():
            window.close()


@pytest.mark.parametrize(
    ("connect", "failure"),
    [(True, None), (True, "connect"), (False, None), (False, "disconnect")],
    ids=["connect-success", "connect-failure", "disconnect-success", "disconnect-failure"],
)
def test_close_waits_for_active_ct400_connection_operation(qtbot, monkeypatch, tmp_path, connect, failure):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    assert window.cameras == []
    assert window.camera_panels == {}
    events = []
    release = threading.Event()
    operation_started = threading.Event()
    native_operation_active = threading.Event()
    device = _fake_blocking_connection_ct400(
        events,
        release,
        operation_started,
        native_operation_active,
        failure,
    )
    window.ct400_device = device
    alignment = _prepare_alignment_panel(window, device)
    alignment_starts = QSignalSpy(alignment.operation_started)
    window.config.scan_defaults.input_port = 3
    window.config.scan_defaults.safe_parking_wavelength = 1532.5
    window.config.scan_defaults.laser_power = 0.75

    if not connect:
        window._handle_ct400_connection_success("CT400 Connected")

    try:
        window._handle_ct400_connect_action_triggered(connect)
        qtbot.waitUntil(operation_started.is_set, timeout=1500)
        assert window._ct400_operation_state.name == ("CONNECTING" if connect else "DISCONNECTING")
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        assert not alignment.align_button.isEnabled()
        assert not alignment.spiral_align_button.isEnabled()
        assert not alignment.map_button.isEnabled()
        alignment.toggle_spiral_alignment()
        assert alignment_starts.count() == 0
        assert not alignment.alignment_worker._is_running
        assert native_operation_active.is_set()

        window.close()

        assert window.isVisible()
        assert window._pending_ct400_operation_close
        assert "Waiting for CT400 connection operation" in window.statusBar().currentMessage()
        assert device.handle == 73
        assert not any(event[0] == "close" for event in events)
        # The only laser command during the blocked disconnect is its own
        # worker call; shutdown has not overlapped it with another command.
        assert sum(event[0] == "laser_started" for event in events) == (0 if connect else 1)

        release.set()
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=4000)

        assert window._ct400_operation_state.name == "IDLE"
        assert not window._pending_ct400_operation_close
        assert events[-1] == ("close", 73, False)
        assert sum(event[0] == "close" for event in events) == 1

        laser_starts = [event for event in events if event[0] == "laser_started"]
        if connect and failure is None:
            assert window._ct400_connection_configured
            assert len(laser_starts) == 1
            assert laser_starts[0][2] == {
                "laser_input": LaserInput.LI_3,
                "enable": Enable.DISABLE,
                "wavelength": 1532.5,
                "power": 0.75,
            }
        elif connect and failure == "connect":
            assert not window._ct400_connection_configured
            assert laser_starts == []
        elif not connect and failure is None:
            assert not window._ct400_connection_configured
            assert len(laser_starts) == 1
        else:
            assert window._ct400_connection_configured
            assert len(laser_starts) == 2
            assert all(event[2]["enable"] == Enable.DISABLE for event in laser_starts)
    finally:
        release.set()
        if window.isVisible():
            window.close()


@pytest.mark.parametrize(
    ("connect", "failure", "configured", "controls_enabled"),
    [
        (True, None, True, True),
        (True, "connect", False, False),
        (False, None, False, False),
        (False, "disconnect", True, False),
    ],
    ids=["connect-success", "connect-failure", "disconnect-success", "disconnect-failure"],
)
def test_ct400_operation_completion_restores_controls_from_confirmed_state(
    qtbot, monkeypatch, tmp_path, connect, failure, configured, controls_enabled
):
    window, _gate, _frame_spy, _dialogs = _start_application(qtbot, monkeypatch, tmp_path, include_camera=False)
    events = []
    release = threading.Event()
    operation_started = threading.Event()
    native_operation_active = threading.Event()
    device = _fake_blocking_connection_ct400(events, release, operation_started, native_operation_active, failure)
    window.ct400_device = device
    window.control_panel.set_instrument(device)
    window.histogram_control.set_instrument(device)
    if not connect:
        window._handle_ct400_connection_success("CT400 Connected")
    else:
        window.control_panel.on_instrument_connected(False)
        window.histogram_control.on_instrument_connected(False)

    try:
        window._handle_ct400_connect_action_triggered(connect)
        qtbot.waitUntil(operation_started.is_set, timeout=1500)
        assert window._ct400_operation_state.name == ("CONNECTING" if connect else "DISCONNECTING")
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        release.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state.name == "IDLE", timeout=2500)

        assert window._ct400_connection_configured is configured
        assert window.control_panel.scan_btn.isEnabled() is controls_enabled
        assert window.histogram_control.monitor_btn.isEnabled() is controls_enabled
        assert window.ct400_connect_action.isEnabled()
    finally:
        release.set()
        if window.isVisible():
            window.close()
