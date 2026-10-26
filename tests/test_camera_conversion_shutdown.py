import os
import subprocess
import sys
from pathlib import Path
from threading import Event

import numpy as np
from PySide6.QtCore import QTimer

from config_model import AppConfig, CameraConfig
from hardware.simulated_camera import SimulatedCamera
from tools.qt_stall_stress_supervisor import _terminate_process_tree
from ui import camera_widgets


def test_close_retains_blocked_converter_until_native_return(qtbot, monkeypatch):
    panel = camera_widgets.CameraPanel(None, "Blocked", CameraConfig(identifier="blocked", name="Blocked"))
    qtbot.addWidget(panel)
    panel.show()
    panel.set_camera(SimulatedCamera("blocked", width=8, height=8))
    worker, thread = panel.conversion_worker, panel.conversion_thread
    worker.is_mono = False
    worker.set_active(True)
    entered, release = Event(), Event()
    native_convert = camera_widgets.cv2.cvtColor

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return native_convert(*args)

    monkeypatch.setattr(camera_widgets.cv2, "cvtColor", blocked)
    worker.submit_frame(np.zeros((8, 8, 3), dtype=np.uint8))
    qtbot.waitUntil(entered.is_set)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    try:
        assert not panel.close()
        assert not panel._conversion_shutdown_complete
        assert panel.conversion_worker is worker
        assert thread.isRunning()
        qtbot.waitUntil(lambda: heartbeat == [True])
        assert panel.isVisible()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not thread.isRunning())
    qtbot.waitUntil(lambda: panel._conversion_shutdown_complete)
    assert panel.conversion_worker is None
    assert panel._latest_qimage is None
    assert panel.close()


def test_mainwindow_retains_panel_and_camera_system_during_conversion(qtbot, monkeypatch):
    from ui import main_window as module

    monkeypatch.setattr(module.MainWindow, "_begin_lazy_init", lambda _self: None)
    window = module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    config = CameraConfig(identifier="blocked", name="Blocked", backend="simulation")
    camera = SimulatedCamera("blocked", width=8, height=8)
    panel = window._create_camera_panel(None, config)
    window.camera_layout.addWidget(panel)
    panel.set_camera(camera)
    window.camera_panels[config.identifier] = panel
    window.cameras.append(camera)
    worker, thread = panel.conversion_worker, panel.conversion_thread
    worker.is_mono = False
    worker.set_active(True)
    entered, release = Event(), Event()
    native_convert = camera_widgets.cv2.cvtColor
    events = []
    original_close = camera.close

    def close_producer():
        events.append("producer-stopped")
        original_close()

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        events.append("conversion-returned")
        return native_convert(*args)

    monkeypatch.setattr(camera, "close", close_producer)
    monkeypatch.setattr(camera_widgets.cv2, "cvtColor", blocked)
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: events.append("system-closed"))
    worker.submit_frame(np.zeros((8, 8, 3), dtype=np.uint8))
    qtbot.waitUntil(entered.is_set)
    window.show()
    try:
        assert not window.close()
        qtbot.waitUntil(lambda: panel._conversion_shutdown_requested)
        assert events == ["producer-stopped"]
        assert panel.conversion_worker is worker
        assert thread.isRunning()
        assert window.cameras == [camera]
        assert not window._camera_cleanup_complete
        assert not window.close()
    finally:
        release.set()
        qtbot.waitUntil(lambda: window._shutdown_complete)
    assert events == ["producer-stopped", "conversion-returned", "system-closed"]
    assert not window.cameras


def test_conversion_shutdown_exits_cleanly_in_fresh_process(tmp_path):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(Path(__file__).resolve()),
        "tests/test_task_runner_threading.py",
        "-q",
        "-k",
        "not fresh_process",
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
    assert "9 passed" in result.stdout
    assert "QThread: Destroyed" not in result.stderr


def test_closed_placeholder_rejects_late_camera_assignment(qtbot):
    panel = camera_widgets.CameraPanel(None, "Late", CameraConfig(identifier="late", name="Late"))
    qtbot.addWidget(panel)
    assert panel.close()
    panel.set_camera(SimulatedCamera("late", width=8, height=8))
    assert panel.camera is None
    assert panel.conversion_thread is None
