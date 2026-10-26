import os
import subprocess
import sys
from pathlib import Path
from threading import Event

import pytest
import shiboken6
from PySide6.QtCore import QMetaObject, Qt
from PySide6.QtGui import QCloseEvent

from config_model import AppConfig
from hardware.ct400 import CT400Error
from hardware.ct400_types import Enable, LaserInput, PowerData
from hardware.dummy_ct400 import DummyCT400
from tools.qt_stall_stress_supervisor import _terminate_process_tree
from ui.control_panel import HistogramControlPanel


def test_stop_is_visible_during_blocked_read_and_discards_queued_fetch(qtbot, monkeypatch):
    entered, release = Event(), Event()
    device = DummyCT400(scan_duration=0)
    panel = HistogramControlPanel(device, AppConfig())
    qtbot.addWidget(panel)
    panel.wavelength_input.setText("1550")
    panel.laser_power.setText("1")
    calls, delivered, finished = [], [], []

    def read():
        calls.append("read")
        entered.set()
        assert release.wait(5), "test did not release fake native read"
        calls.append("returned")
        return PowerData(1.0, {})

    def laser(port, enabled, *_args):
        if enabled == Enable.DISABLE:
            assert release.is_set()
            calls.append("disable")

    monkeypatch.setattr(device, "get_all_powers", read)
    monkeypatch.setattr(device, "cmd_laser", laser)
    panel.power_data_ready.connect(delivered.append)
    panel.operation_finished.connect(lambda: finished.append(True))
    panel._start_monitoring()
    panel.timer.stop()
    panel._request_power_fetch_from_worker()
    qtbot.waitUntil(entered.is_set)
    QMetaObject.invokeMethod(panel.power_fetch_worker, "fetch_power", Qt.ConnectionType.QueuedConnection)
    try:
        panel._stop_monitoring()
        panel._stop_monitoring()
        assert not panel.power_fetch_worker.is_worker_running()
        assert calls == ["read"]
        assert finished == []
        panel._start_monitoring()
        assert not panel.monitoring
    finally:
        release.set()
        qtbot.waitUntil(lambda: not panel._monitor_stop_pending)
    assert calls == ["read", "returned", "disable"]
    assert delivered == []
    assert finished == [True]
    panel._start_monitoring()
    assert panel.monitoring
    panel._stop_monitoring()
    qtbot.waitUntil(lambda: not panel._monitor_stop_pending)


@pytest.mark.parametrize("active", [False, True])
def test_panel_close_waits_for_native_retirement(qtbot, active):
    panel = HistogramControlPanel(DummyCT400(scan_duration=0), AppConfig())
    qtbot.addWidget(panel)
    panel.wavelength_input.setText("1550")
    panel.laser_power.setText("1")
    if active:
        panel._start_monitoring()
    event = QCloseEvent()
    panel.closeEvent(event)
    assert not event.isAccepted()
    panel.cleanup_worker_thread()
    qtbot.waitUntil(lambda: panel.shutdown_finished)
    assert not shiboken6.isValid(panel.power_fetch_thread) or not panel.power_fetch_thread.isRunning()
    panel.closeEvent(event)
    assert event.isAccepted()


def test_mainwindow_close_retains_device_until_blocked_read_retires(qtbot, monkeypatch):
    from ui import main_window as module

    monkeypatch.setattr(module.MainWindow, "_begin_lazy_init", lambda _self: None)
    window = module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    device = DummyCT400(scan_duration=0)
    window._on_ct400_initialized(device)
    window.is_ct400_connected_state = True
    panel = window.histogram_control
    panel.wavelength_input.setText("1550")
    panel.laser_power.setText("1")
    panel.input_port.setCurrentIndex(panel.input_port.findData(LaserInput.LI_4))
    entered, release = Event(), Event()
    events = []

    def read():
        entered.set()
        assert release.wait(5)
        events.append("read-returned")
        return PowerData(1, {})

    def laser(port, enable, *_args):
        if enable == Enable.DISABLE:
            assert events == ["read-returned"]
            assert port == LaserInput.LI_4
            events.append("disable")

    def close():
        assert panel.shutdown_finished
        events.append("device-close")

    monkeypatch.setattr(device, "get_all_powers", read)
    monkeypatch.setattr(device, "cmd_laser", laser)
    monkeypatch.setattr(device, "close", close)
    panel._start_monitoring()
    panel.timer.stop()
    panel._request_power_fetch_from_worker()
    qtbot.waitUntil(entered.is_set)
    window.show()
    try:
        assert not window.close()
        assert not window.close()
        assert window.isVisible()
        assert not window._shutdown_complete
        assert events == []
        assert panel.power_fetch_thread.isRunning()
    finally:
        release.set()
        qtbot.waitUntil(lambda: window._shutdown_complete)
    assert events == ["read-returned", "disable", "device-close"]
    assert not window.isVisible()


@pytest.mark.parametrize("error", [CT400Error("disconnected"), RuntimeError("read failed")])
def test_read_error_releases_monitor_ownership_once(qtbot, monkeypatch, error):
    from ui.control_panel import QMessageBox

    panel = HistogramControlPanel(DummyCT400(scan_duration=0), AppConfig())
    qtbot.addWidget(panel)
    panel.wavelength_input.setText("1550")
    panel.laser_power.setText("1")
    finished, messages = [], []
    panel.operation_finished.connect(lambda: finished.append(True))
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: messages.append(_args))

    def fail():
        raise error

    monkeypatch.setattr(panel.ct400, "get_all_powers", fail)
    panel._start_monitoring()
    panel.timer.stop()
    panel._request_power_fetch_from_worker()
    qtbot.waitUntil(lambda: finished == [True])
    assert len(messages) == 1
    assert not panel.monitoring
    panel._stop_monitoring()
    assert finished == [True]


def test_monitor_shutdown_process_exits_after_unblocking(tmp_path):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(Path(__file__).resolve()),
        "-q",
        "-k",
        "not process_exits",
        "--basetemp",
        str(tmp_path / "child"),
    ]
    with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        try:
            stdout, stderr = child.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(child)
            child.communicate(timeout=5)
            raise
    result = subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "6 passed" in result.stdout
    assert "QThread: Destroyed" not in result.stderr
    assert "QMutex: destroying locked mutex" not in result.stderr
