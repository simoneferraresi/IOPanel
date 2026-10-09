from threading import Event, Thread
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QSettings, Qt

import ui.control_panel as control_panel_module
import ui.main_window as main_window_module
from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400 import CT400
from hardware.ct400_types import Enable, LaserInput, ScanWaitResult
from hardware.dummy_ct400 import DummyCT400
from ui.control_panel import (
    CT400ConnectionSignals,
    CT400ConnectionWorker,
    CT400ControlPanel,
    HistogramControlPanel,
    ScanSettings,
)
from ui.main_window import CT400OperationState, MainWindow


def _config():
    return AppConfig.from_ini_dict({"Instruments": {"ct400_backend": "simulation"}})


def test_initialized_physical_ct400_does_not_enable_scan_or_monitor(qtbot):
    device = CT400.__new__(CT400)
    device.handle = 1
    device.dll = SimpleNamespace()

    scan = CT400ControlPanel(ScanSettings(), None, _config())
    monitor = HistogramControlPanel(None, _config())
    qtbot.addWidget(scan)
    qtbot.addWidget(monitor)

    # An initialized handle is available, but no operator Connect succeeded.
    scan.set_instrument(device)
    monitor.set_instrument(device)

    assert not scan.scan_btn.isEnabled()
    assert not monitor.monitor_btn.isEnabled()


def test_dummy_ct400_is_explicitly_operable_without_physical_connect(qtbot):
    device = DummyCT400()
    scan = CT400ControlPanel(ScanSettings(), None, _config())
    monitor = HistogramControlPanel(None, _config())
    qtbot.addWidget(scan)
    qtbot.addWidget(monitor)

    scan.set_instrument(device)
    monitor.set_instrument(device)

    assert scan.scan_btn.isEnabled()
    assert monitor.monitor_btn.isEnabled()


def _physical_device():
    device = CT400.__new__(CT400)
    device.handle = 1
    device.dll = SimpleNamespace(CT400_Close=lambda _handle: 0)
    device.laser_calls = []
    device.cmd_laser = lambda **kwargs: device.laser_calls.append(kwargs)
    return device


def _window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindow, "_begin_lazy_init", lambda _window: None)
    config = _config()
    config.scan_defaults.input_port = 3
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    window = MainWindow(config, settings=settings)
    window.show()
    return window


def test_physical_actions_follow_confirmed_input_and_connection_transitions(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    device = _physical_device()
    try:
        window._on_ct400_initialized(device)
        assert window.ct400_status_label.text() == "CT400: Ready (Disconnected)"
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()

        window.control_panel.input_port.setCurrentIndex(window.control_panel.input_port.findData(LaserInput.LI_3))
        window.histogram_control.input_port.setCurrentIndex(
            window.histogram_control.input_port.findData(LaserInput.LI_3)
        )

        window._set_ct400_operation_state(CT400OperationState.CONNECTING)
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        window._set_ct400_operation_state(CT400OperationState.IDLE)

        window._handle_ct400_connection_success("Connected", LaserInput.LI_3)
        assert window.ct400_status_label.text() == "CT400: Connected · LI_3"
        scan = window.control_panel
        monitor = window.histogram_control
        assert scan.scan_btn.isEnabled()
        assert monitor.monitor_btn.isEnabled()

        scan.input_port.setCurrentIndex(scan.input_port.findData(LaserInput.LI_2))
        monitor.input_port.setCurrentIndex(monitor.input_port.findData(LaserInput.LI_2))
        assert not scan.scan_btn.isEnabled()
        assert not monitor.monitor_btn.isEnabled()

        scan.input_port.setCurrentIndex(scan.input_port.findData(LaserInput.LI_3))
        monitor.input_port.setCurrentIndex(monitor.input_port.findData(LaserInput.LI_3))
        assert scan.scan_btn.isEnabled()
        assert monitor.monitor_btn.isEnabled()

        window._set_ct400_operation_state(CT400OperationState.DISCONNECTING)
        assert not scan.scan_btn.isEnabled()
        assert not monitor.monitor_btn.isEnabled()
        window._handle_ct400_disconnection_success("Disconnected")
        window._handle_ct400_connection_operation_finished()
        assert not scan.scan_btn.isEnabled()
        assert not monitor.monitor_btn.isEnabled()

        # Repeated transitions keep the authorization tied to a successful
        # connection and the same selected input.
        window._handle_ct400_connection_success("Reconnected", LaserInput.LI_3)
        assert scan.scan_btn.isEnabled()
        window._handle_ct400_connection_failure("Disconnect failed")
        assert not scan.scan_btn.isEnabled()
        assert not monitor.monitor_btn.isEnabled()
    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


def test_replacement_resets_connection_and_ignores_stale_success(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    old_device = _physical_device()
    new_device = _physical_device()
    try:
        window._on_ct400_initialized(old_device)
        old_token = window._ct400_connection_operation_token
        window._handle_ct400_connection_success("Connected", LaserInput.LI_3)
        assert window.control_panel.scan_btn.isEnabled()

        window._on_ct400_initialized(new_device)
        assert not window.control_panel.scan_btn.isEnabled()
        window._handle_ct400_connection_success("Late old success", LaserInput.LI_3, old_token, old_device)
        assert not window.control_panel.scan_btn.isEnabled()
        assert window._ct400_connected_laser_input is None
        assert window.ct400_device is old_device
        assert window._pending_ct400_device_replacement is new_device
        assert window._ct400_cleanup_laser_input is LaserInput.LI_3

        window._set_ct400_operation_state(CT400OperationState.CONNECTING)
        window._handle_ct400_connection_operation_finished(old_token, old_device)
        assert window._ct400_operation_state is CT400OperationState.CONNECTING
        window._set_ct400_operation_state(CT400OperationState.IDLE)
    finally:
        window.close()


def test_disconnect_worker_uses_the_input_captured_by_connect():
    config = _config()
    config.scan_defaults.input_port = 1
    calls = []
    device = SimpleNamespace(cmd_laser=lambda **kwargs: calls.append(kwargs))
    worker = CT400ConnectionWorker(device, config, connect=False, laser_input=LaserInput.LI_4)

    worker.run()

    assert calls == [
        {
            "laser_input": LaserInput.LI_4,
            "enable": Enable.DISABLE,
            "wavelength": config.scan_defaults.safe_parking_wavelength,
            "power": config.scan_defaults.laser_power,
        }
    ]


def _queued_connection_signals(window):
    signals = CT400ConnectionSignals(window)
    connection = Qt.ConnectionType.QueuedConnection
    signals.connection_succeeded.connect(window._handle_ct400_connection_success, connection)
    signals.connection_failed.connect(window._handle_ct400_connection_failure, connection)
    signals.disconnection_succeeded.connect(window._handle_ct400_disconnection_success, connection)
    signals.disconnection_failed.connect(window._handle_ct400_disconnection_failure, connection)
    signals.finished.connect(window._handle_ct400_connection_operation_finished, connection)
    return signals


def test_queued_connection_signals_preserve_payload_and_reject_stale_callbacks(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    device = _physical_device()
    try:
        window._on_ct400_initialized(device)
        token = window._ct400_connection_operation_token
        for panel in (window.control_panel, window.histogram_control):
            panel.input_port.setCurrentIndex(panel.input_port.findData(LaserInput.LI_4))
        signals = _queued_connection_signals(window)

        signals.connection_succeeded.emit("Stale success", LaserInput.LI_4, token - 1, device)
        signals.connection_failed.emit("Stale failure", token - 1, device)
        signals.disconnection_succeeded.emit("Stale disconnect", token - 1, device)
        signals.disconnection_failed.emit("Stale disconnect failure", token - 1, device)
        window._set_ct400_operation_state(CT400OperationState.CONNECTING)
        signals.finished.emit(token - 1, device)
        qtbot.wait(20)

        assert window._ct400_connected_laser_input is None
        assert window._ct400_operation_state is CT400OperationState.CONNECTING
        window._set_ct400_operation_state(CT400OperationState.IDLE)

        window._set_ct400_operation_state(CT400OperationState.CONNECTING)
        signals.connection_succeeded.emit("Connected on LI_4", LaserInput.LI_4, token, device)
        qtbot.waitUntil(lambda: window._ct400_connected_laser_input is LaserInput.LI_4)
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert window._ct400_connection_configured
        assert not window.control_panel.scan_btn.isEnabled()
        signals.finished.emit(token, device)
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        assert window.control_panel.scan_btn.isEnabled()
        assert window.histogram_control.monitor_btn.isEnabled()

        # A current-token callback from a replaced device must also be stale.
        replacement = _physical_device()
        window._on_ct400_initialized(replacement)
        replacement_token = window._ct400_connection_operation_token
        assert window._ct400_connected_laser_input is None
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert window.ct400_device is device
        assert window._pending_ct400_device_replacement is replacement
        window._set_ct400_operation_state(CT400OperationState.CONNECTING)
        signals.connection_succeeded.emit("Old device success", LaserInput.LI_4, replacement_token, replacement)
        signals.connection_failed.emit("Old device failure", replacement_token, replacement)
        signals.disconnection_succeeded.emit("Old device disconnect", replacement_token, replacement)
        signals.disconnection_failed.emit("Old device disconnect failure", replacement_token, replacement)
        signals.finished.emit(replacement_token, replacement)
        qtbot.wait(20)
        assert window._ct400_connected_laser_input is None
        assert window._ct400_operation_state is CT400OperationState.CONNECTING
        window._set_ct400_operation_state(CT400OperationState.IDLE)
    finally:
        window.close()


def test_queued_disconnect_and_failure_revoke_authorization_but_keep_cleanup_input(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    device = _physical_device()
    try:
        window._on_ct400_initialized(device)
        token = window._ct400_connection_operation_token
        for panel in (window.control_panel, window.histogram_control):
            panel.input_port.setCurrentIndex(panel.input_port.findData(LaserInput.LI_4))
        signals = _queued_connection_signals(window)

        signals.connection_failed.emit("Connect failed", token, device)
        qtbot.waitUntil(lambda: window._ct400_visual_state.name == "ERROR")
        assert window._ct400_connected_laser_input is None
        assert window._ct400_cleanup_laser_input is None
        assert not window._ct400_connection_configured
        assert not window.control_panel.scan_btn.isEnabled()

        token += 1
        window._ct400_connection_operation_token = token
        signals.connection_succeeded.emit("Connected on LI_4", LaserInput.LI_4, token, device)
        qtbot.waitUntil(lambda: window._ct400_connected_laser_input is LaserInput.LI_4)
        signals.disconnection_failed.emit("Disconnect failed", token, device)
        qtbot.waitUntil(lambda: window._ct400_connected_laser_input is None)
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert window._ct400_connection_configured
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        assert window._ct400_visual_state.name == "DISCONNECT_UNCONFIRMED"
        assert window.ct400_connect_action.isChecked()
        assert "Retry" in window.ct400_connect_action.text()

        # A shutdown cleanup retry must retain the LI_4 input that Connect
        # actually configured even though the current UI authorization is gone.
        assert window._disable_configured_ct400_input_for_shutdown()
        assert device.laser_calls[-1]["laser_input"] is LaserInput.LI_4
        assert device.laser_calls[-1]["enable"] is Enable.DISABLE
        window._ct400_connection_configured = False

        next_token = token + 1
        window._ct400_connection_operation_token = next_token
        signals.connection_succeeded.emit("Reconnected on LI_4", LaserInput.LI_4, next_token, device)
        qtbot.waitUntil(lambda: window._ct400_connected_laser_input is LaserInput.LI_4)
        signals.disconnection_succeeded.emit("Disconnected", next_token, device)
        qtbot.waitUntil(lambda: not window._ct400_connection_configured)
        assert window._ct400_connected_laser_input is None
        assert window._ct400_cleanup_laser_input is None
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()

    finally:
        window._ct400_error_reset_timer.stop()
        window.close()


@pytest.mark.parametrize("retry_succeeds", [True, False])
def test_gui_disconnect_retry_stays_on_captured_input_after_failure(qtbot, monkeypatch, tmp_path, retry_succeeds):
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(main_window_module, "QMessageBox", SimpleNamespace(warning=lambda *_args: None))
    device = _physical_device()
    calls = []
    device.set_laser = lambda **kwargs: calls.append(("connect", kwargs["laser_input"]))
    disconnect_count = 0

    def cmd_laser(**kwargs):
        nonlocal disconnect_count
        disconnect_count += 1
        calls.append(("disconnect", kwargs["laser_input"]))
        if disconnect_count == 1 or not retry_succeeds:
            raise RuntimeError("mock disconnect not confirmed")

    device.cmd_laser = cmd_laser
    threads = []

    class FakePool:
        def start(self, worker):
            thread = Thread(target=worker.run)
            threads.append(thread)
            thread.start()

    monkeypatch.setattr(main_window_module, "QThreadPool", SimpleNamespace(globalInstance=lambda: FakePool()))
    try:
        window._on_ct400_initialized(device)
        # Explicit Connect configures LI_4. The default changes only afterward.
        window.config.scan_defaults.input_port = 4
        for panel in (window.control_panel, window.histogram_control):
            panel.input_port.setCurrentIndex(panel.input_port.findData(LaserInput.LI_4))
        window.ct400_connect_action.trigger()
        qtbot.waitUntil(lambda: window._ct400_connected_laser_input is LaserInput.LI_4)
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        threads[-1].join()
        assert window.control_panel.scan_btn.isEnabled()
        assert window.histogram_control.monitor_btn.isEnabled()

        window.config.scan_defaults.input_port = 1
        window.ct400_connect_action.trigger()
        qtbot.waitUntil(lambda: window._ct400_visual_state.name == "DISCONNECT_UNCONFIRMED")
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        threads[-1].join()
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert window._ct400_connected_laser_input is None
        assert window.ct400_connect_action.isChecked()
        assert "Retry" in window.ct400_connect_action.text()
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()

        window.ct400_connect_action.trigger()
        qtbot.waitUntil(lambda: disconnect_count == 2)
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        threads[-1].join()
        assert [input_port for _, input_port in calls] == [
            LaserInput.LI_4,
            LaserInput.LI_4,
            LaserInput.LI_4,
        ]
        assert all(input_port is not LaserInput.LI_1 for _, input_port in calls)
        if retry_succeeds:
            assert not window._ct400_connection_configured
            assert window._ct400_cleanup_laser_input is None
            assert window._ct400_visual_state.name == "DISCONNECTED"
        else:
            assert window._ct400_connection_configured
            assert window._ct400_cleanup_laser_input is LaserInput.LI_4
            assert window._ct400_visual_state.name == "DISCONNECT_UNCONFIRMED"
            assert not window.control_panel.scan_btn.isEnabled()
            assert not window.histogram_control.monitor_btn.isEnabled()
    finally:
        for thread in threads:
            thread.join()
        window._ct400_error_reset_timer.stop()
        window.close()


def test_replacement_waits_for_confirmed_disconnect_before_closing_old_device(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    old_device = _physical_device()
    replacement = _physical_device()
    closed = []
    old_device.close = lambda: closed.append("close")
    old_device.set_laser = lambda **_kwargs: None
    old_device.cmd_laser = lambda **_kwargs: None
    try:
        window._on_ct400_initialized(old_device)
        window._handle_ct400_connection_success("Connected", LaserInput.LI_4)
        window._on_ct400_initialized(replacement)
        assert window.ct400_device is old_device
        assert window._pending_ct400_device_replacement is replacement
        assert closed == []
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert not window.control_panel.scan_btn.isEnabled()

        token = window._ct400_connection_operation_token + 1
        window._ct400_connection_operation_token = token
        window._set_ct400_operation_state(CT400OperationState.DISCONNECTING)
        window._handle_ct400_disconnection_failure("not confirmed", token, old_device)
        window._handle_ct400_connection_operation_finished(token, old_device)
        assert window.ct400_device is old_device
        assert window._pending_ct400_device_replacement is replacement
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert closed == []

        token = window._ct400_connection_operation_token + 1
        window._ct400_connection_operation_token = token
        window._set_ct400_operation_state(CT400OperationState.DISCONNECTING)
        window._handle_ct400_disconnection_success("confirmed", token, old_device)
        window._handle_ct400_connection_operation_finished(token, old_device)
        assert window.ct400_device is replacement
        assert window._ct400_cleanup_laser_input is None
        assert closed == ["close"]
    finally:
        window.close()


def test_unknown_configured_input_blocks_reconnect_and_shutdown_guess(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(main_window_module, "QMessageBox", SimpleNamespace(warning=lambda *_args: None))
    device = _physical_device()
    try:
        window._on_ct400_initialized(device)
        window._ct400_connection_configured = True
        window._handle_ct400_disconnection_failure("disconnect state unknown")

        assert window._ct400_visual_state.name == "DISCONNECT_UNCONFIRMED"
        assert not window.ct400_connect_action.isEnabled()
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        window._handle_ct400_connect_action_triggered(True)
        assert device.laser_calls == []
        assert not window._disable_configured_ct400_input_for_shutdown()
        assert device.laser_calls == []
    finally:
        window.close()


@pytest.mark.parametrize(
    ("result_code", "request_stop", "expected_stop_calls"),
    [(0, False, 0), (1, True, 1), (100, False, 0)],
    ids=["success", "documented-cancellation", "documented-warning"],
)
def test_normal_physical_scan_outcomes_preserve_authorization(
    qtbot, monkeypatch, tmp_path, result_code, request_stop, expected_stop_calls
):
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(control_panel_module.QMessageBox, "critical", lambda *_args: None)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *_args: None)
    device = _physical_device()
    events = []
    start_entered, wait_entered, release_wait = Event(), Event(), Event()
    if not request_stop:
        release_wait.set()

    device.cmd_laser = lambda **kwargs: events.append(("laser", kwargs["laser_input"], kwargs["enable"]))
    device.set_scan = lambda *_args, **_kwargs: None
    device.set_sampling_res = lambda *_args, **_kwargs: None
    device.set_detector_array = lambda *_args, **_kwargs: None
    device.start_scan = lambda: (start_entered.set(), None)[1]

    def scan_wait_end():
        wait_entered.set()
        if not release_wait.wait(5):
            raise TimeoutError("test did not release scan wait")
        return ScanWaitResult(result_code, "documented result" if result_code else "")

    device.scan_wait_end = scan_wait_end
    device.stop_scan = lambda: events.append(("stop",))
    device.get_data_points = lambda _detectors: (np.array([1500.0, 1501.0]), np.array([[1.0, 2.0]]))
    device.get_all_powers = lambda: SimpleNamespace(pout=-20.0)

    window._on_ct400_initialized(device)
    for panel in (window.control_panel, window.histogram_control):
        panel.input_port.setCurrentIndex(panel.input_port.findData(LaserInput.LI_3))
    window._handle_ct400_connection_success("Connected", LaserInput.LI_3)
    try:
        window.control_panel.scan_btn.click()
        qtbot.waitUntil(start_entered.is_set)
        qtbot.waitUntil(wait_entered.is_set)
        if request_stop:
            window.control_panel.scan_btn.click()
            assert events.count(("stop",)) == 1
            release_wait.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        qtbot.waitUntil(lambda: window.control_panel.scan_thread is None)

        assert events.count(("stop",)) == expected_stop_calls
        assert not window._ct400_scan_state_uncertain
        assert window._ct400_connected_laser_input is LaserInput.LI_3
        assert window._ct400_cleanup_laser_input is LaserInput.LI_3
        assert window._ct400_connection_configured
        assert window.control_panel.scan_btn.isEnabled()
        assert window.histogram_control.monitor_btn.isEnabled()
    finally:
        release_wait.set()
        window.close()


@pytest.mark.parametrize(
    ("cleanup_fails", "recovery_disconnect_succeeds", "operator_confirms_safe_state"),
    [(False, True, True), (False, False, False), (True, True, True), (False, True, False)],
    ids=[
        "start-failure-recovery-confirmed",
        "start-failure-disconnect-fails",
        "cleanup-failure-recovery-confirmed",
        "start-failure-recovery-not-confirmed",
    ],
)
def test_scanstart_exception_revokes_authorization_and_requires_explicit_recovery(
    qtbot, monkeypatch, tmp_path, caplog, cleanup_fails, recovery_disconnect_succeeds, operator_confirms_safe_state
):
    caplog.set_level("INFO", logger="LabApp.control_panel")
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(control_panel_module.QMessageBox, "critical", lambda *_args: None)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *_args: None)
    device = _physical_device()
    events = []
    start_entered, release_start = Event(), Event()
    disconnect_count = 0
    set_laser_calls = []

    def cmd_laser(**kwargs):
        nonlocal disconnect_count
        disconnect_count += 1
        events.append(("laser", kwargs["laser_input"], kwargs["enable"]))
        if cleanup_fails and disconnect_count == 2:
            raise RuntimeError("mock final cleanup failure")
        if disconnect_count >= 3 and not recovery_disconnect_succeeds:
            raise RuntimeError("mock recovery disconnect failure")

    def start_scan():
        start_entered.set()
        if not release_start.wait(5):
            raise TimeoutError("test did not release ScanStart")
        raise OSError(0xEEDFADE, "mock ScanStart failure")

    device.cmd_laser = cmd_laser
    device.set_laser = lambda **kwargs: set_laser_calls.append(kwargs)
    device.set_scan = lambda *_args, **_kwargs: None
    device.set_sampling_res = lambda *_args, **_kwargs: None
    device.set_detector_array = lambda *_args, **_kwargs: None
    device.start_scan = start_scan
    device.stop_scan = lambda: events.append(("stop",))
    threads = []

    class FakePool:
        def start(self, worker):
            thread = Thread(target=worker.run)
            threads.append(thread)
            thread.start()

    monkeypatch.setattr(main_window_module, "QThreadPool", SimpleNamespace(globalInstance=lambda: FakePool()))
    window._on_ct400_initialized(device)
    for panel in (window.control_panel, window.histogram_control):
        panel.input_port.setCurrentIndex(panel.input_port.findData(LaserInput.LI_4))
    window._handle_ct400_connection_success("Connected", LaserInput.LI_4)
    try:
        window.control_panel.scan_btn.click()
        qtbot.waitUntil(start_entered.is_set)
        assert window._ct400_operation_state is CT400OperationState.SCANNING
        assert window.control_panel.scan_worker is not None
        assert not window.control_panel.scan_worker._started

        # GUI Stop during blocked ScanStart records cancellation but must not
        # issue native ScanStop until ScanStart returns successfully.
        window.control_panel.scan_btn.click()
        assert not any(event[0] == "stop" for event in events)
        assert "CT400_ScanStart entry" in caplog.text
        assert "Cancellation requested while CT400_ScanStart is in progress" in caplog.text
        release_start.set()
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        qtbot.waitUntil(lambda: window.control_panel.scan_thread is None)
        assert "CT400_ScanStart exit" in caplog.text
        assert "Unexpected exception at stage start_scan_seconds (OSError)" in caplog.text
        assert not any(event[0] == "stop" for event in events)
        assert [event[1] for event in events if event[0] == "laser"] == [
            LaserInput.LI_4,
            LaserInput.LI_4,
        ]
        assert window._ct400_scan_state_uncertain
        assert window._ct400_visual_state.name == "SCAN_STATE_UNCERTAIN"
        assert window._ct400_connected_laser_input is None
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert window._ct400_connection_configured
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        assert window.ct400_connect_action.isEnabled()
        assert "Recovery" in window.ct400_connect_action.text()

        replacement = _physical_device()
        window._on_ct400_initialized(replacement)
        assert window.ct400_device is device
        assert window._pending_ct400_device_replacement is replacement
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4

        # An explicit Connect request cannot silently reauthorize or reconfigure.
        window._handle_ct400_connect_action_triggered(True)
        assert window._ct400_scan_state_uncertain
        assert set_laser_calls == []
        assert disconnect_count == 2

        # Recovery is an operator click; it always targets the input captured by Connect.
        window.ct400_connect_action.trigger()
        qtbot.waitUntil(lambda: disconnect_count == 3)
        qtbot.waitUntil(lambda: window._ct400_operation_state is CT400OperationState.IDLE)
        threads[-1].join()
        assert events[-1][0] == "laser"
        assert events[-1][1] is LaserInput.LI_4
        assert events[-1][2] is Enable.DISABLE
        assert set_laser_calls == []
        if recovery_disconnect_succeeds:
            assert window._ct400_scan_state_uncertain
            assert window._ct400_recovery_disconnect_completed
            assert window._ct400_visual_state.name == "RECOVERY_CONFIRMATION_REQUIRED"
            assert "does not independently confirm" in window.statusBar().currentMessage()
            if operator_confirms_safe_state:
                # This separate operator click asserts the approved lab safe-state
                # check; the CmdLaser return alone leaves recovery unresolved.
                monkeypatch.setattr(
                    main_window_module.QMessageBox,
                    "question",
                    lambda *_args: main_window_module.QMessageBox.StandardButton.Yes,
                )
                window.ct400_connect_action.trigger()
        recovery_resolved = recovery_disconnect_succeeds and operator_confirms_safe_state
        if recovery_resolved:
            assert window.ct400_device is replacement
            assert not window._ct400_scan_state_uncertain
            assert not window._ct400_recovery_disconnect_completed
            assert window._ct400_connected_laser_input is None
            assert not window._ct400_connection_configured
            assert not window.control_panel.scan_btn.isEnabled()
            assert not window.histogram_control.monitor_btn.isEnabled()
        else:
            assert window.ct400_device is device
            assert window._pending_ct400_device_replacement is replacement
            assert window._ct400_scan_state_uncertain
            assert window._ct400_recovery_disconnect_completed is recovery_disconnect_succeeds
            assert window._ct400_cleanup_laser_input is LaserInput.LI_4
            assert not window.control_panel.scan_btn.isEnabled()
            assert not window.histogram_control.monitor_btn.isEnabled()
    finally:
        release_start.set()
        for thread in threads:
            thread.join()
        window._ct400_error_reset_timer.stop()
        window.close()


def test_scan_failure_with_unknown_captured_input_blocks_recovery_command(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda *_args: None)
    device = _physical_device()
    window._on_ct400_initialized(device)
    window._ct400_connection_configured = True
    issue = control_panel_module.ScanSafetyIssue(
        device=device,
        laser_input=LaserInput.LI_4,
        reason="OSError at start_scan_seconds.",
        failed_stages=("start_scan_seconds",),
        cleanup_failed=False,
    )
    try:
        window._handle_ct400_scan_safety_uncertain(issue)
        assert window._ct400_visual_state.name == "SCAN_STATE_UNCERTAIN"
        assert not window.ct400_connect_action.isEnabled()
        assert not window.control_panel.scan_btn.isEnabled()
        assert not window.histogram_control.monitor_btn.isEnabled()
        window._handle_ct400_connect_action_triggered(False)
        assert device.laser_calls == []
        assert window._ct400_scan_state_uncertain
    finally:
        window.close()


class _ReadyAlignmentPiezo:
    VOLTS_PER_NM = 0.001

    def __init__(self, port):
        self.port = port

    def is_connected(self):
        return True

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


def _install_mock_alignment_hardware(panel, device):
    panel.ct400 = device
    panel.piezo_left = _ReadyAlignmentPiezo("left")
    panel.piezo_right = _ReadyAlignmentPiezo("right")
    panel.set_hardware_ready(True)


@pytest.mark.parametrize("mode", ["fine", "spiral", "mapping"])
def test_alignment_modes_fail_closed_during_scan_recovery_even_if_controls_are_reenabled(
    qtbot, monkeypatch, tmp_path, mode
):
    window = _window(qtbot, monkeypatch, tmp_path)
    device = _physical_device()
    native_commands = []
    device.cmd_laser = lambda **kwargs: native_commands.append(kwargs)
    panel = window.alignment_tab
    window._on_ct400_initialized(device)
    _install_mock_alignment_hardware(panel, device)
    panel.input_port_combo.setCurrentIndex(panel.input_port_combo.findData(4))
    window._handle_ct400_connection_success("Connected", LaserInput.LI_4)
    issue = control_panel_module.ScanSafetyIssue(
        device=device,
        laser_input=LaserInput.LI_4,
        reason="OSError at start_scan_seconds.",
        failed_stages=("start_scan_seconds",),
        cleanup_failed=False,
    )
    window._handle_ct400_scan_safety_uncertain(issue)
    window._set_ct400_operation_state(CT400OperationState.IDLE)
    try:
        assert panel._hardware_ready  # Both mocked piezos and CT400 remain bound and ready.
        assert not panel.align_button.isEnabled()
        assert not panel.spiral_align_button.isEnabled()
        assert not panel.map_button.isEnabled()

        # Simulate stale/forcibly re-enabled widgets: the start-time gate remains authoritative.
        panel.align_group.setEnabled(True)
        panel.map_group.setEnabled(True)
        if mode == "fine":
            panel.toggle_alignment(True)
        elif mode == "spiral":
            panel.toggle_spiral_alignment()
        else:
            panel.toggle_mapping(True)

        assert native_commands == []
        assert panel._active_mode is None
        assert panel.alignment_worker is None
        assert window._ct400_operation_state is CT400OperationState.IDLE
        assert window._ct400_scan_state_uncertain
        panel.operation_started.emit()
        assert window._ct400_operation_state is CT400OperationState.IDLE
    finally:
        window.close()


def test_physical_alignment_requires_explicit_connect_and_matching_input(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    device = _physical_device()
    panel = window.alignment_tab
    window._on_ct400_initialized(device)
    _install_mock_alignment_hardware(panel, device)
    panel.input_port_combo.setCurrentIndex(panel.input_port_combo.findData(4))
    try:
        assert not window._is_alignment_operation_authorized()
        assert not panel.align_button.isEnabled()

        window._handle_ct400_connection_success("Connected", LaserInput.LI_3)
        assert not window._is_alignment_operation_authorized()
        assert not panel.align_button.isEnabled()

        window._handle_ct400_connection_success("Connected", LaserInput.LI_4)
        assert window._is_alignment_operation_authorized()
        assert panel.align_button.isEnabled()

        window._ct400_scan_state_uncertain = True
        panel._update_control_availability()
        assert not window._is_alignment_operation_authorized()
        assert not panel.align_button.isEnabled()
    finally:
        window.close()


@pytest.mark.parametrize(
    "answer",
    [main_window_module.QMessageBox.StandardButton.No, main_window_module.QMessageBox.StandardButton.Cancel],
    ids=["reject", "close-dialog"],
)
def test_safe_state_confirmation_rejection_preserves_uncertainty(qtbot, monkeypatch, tmp_path, answer):
    window = _window(qtbot, monkeypatch, tmp_path)
    device = _physical_device()
    window._on_ct400_initialized(device)
    window._ct400_scan_state_uncertain = True
    window._ct400_recovery_disconnect_completed = True
    window._ct400_connection_configured = True
    window._ct400_cleanup_laser_input = LaserInput.LI_4
    window._ct400_connected_laser_input = None
    prompts = []

    def reject_confirmation(*args):
        prompts.append(args)
        return answer

    monkeypatch.setattr(main_window_module.QMessageBox, "question", reject_confirmation)
    try:
        window.ct400_connect_action.trigger()
        assert len(prompts) == 1
        assert "independently verifying" in prompts[0][2]
        assert window._ct400_scan_state_uncertain
        assert window._ct400_recovery_disconnect_completed
        assert window._ct400_connection_configured
        assert window._ct400_cleanup_laser_input is LaserInput.LI_4
        assert window._ct400_visual_state.name == "RECOVERY_CONFIRMATION_REQUIRED"
        assert not window._is_alignment_operation_authorized()
    finally:
        window.close()
