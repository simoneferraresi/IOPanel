from types import SimpleNamespace

from PySide6.QtCore import QSettings

from app_settings import AppSettings
from config_model import AppConfig
from hardware.ct400 import CT400
from hardware.ct400_types import Enable, LaserInput
from hardware.dummy_ct400 import DummyCT400
from ui.control_panel import CT400ConnectionWorker, CT400ControlPanel, HistogramControlPanel, ScanSettings
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
    device.cmd_laser = lambda **_kwargs: None
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
