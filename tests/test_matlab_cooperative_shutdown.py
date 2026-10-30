import os
import subprocess
import sys
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QSignalSpy

from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState
from tools.qt_stall_stress_supervisor import _terminate_process_tree


def test_ready_shutdown_is_off_gui_and_does_not_report_blocked_quit_complete(qtbot):
    gui_ident = get_ident()
    entered, release = Event(), Event()
    quit_threads = []

    class Engine:
        def quit(self):
            quit_threads.append(get_ident())
            entered.set()
            # The old synchronous path must fail quickly without freezing pytest.
            if get_ident() != gui_ident:
                assert release.wait(5)

    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: None, poll_interval_ms=5)
    manager._engine = Engine()
    manager._set_state(MatlabEngineState.READY)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    try:
        manager.shutdown()
        qtbot.waitUntil(entered.is_set)
        assert quit_threads != [gui_ident]
        assert not manager.shutdown_finished
        qtbot.waitUntil(lambda: heartbeat == [True])
        manager.shutdown()
        assert len(quit_threads) == 1
    finally:
        release.set()
    qtbot.waitUntil(lambda: manager.shutdown_finished)


@pytest.mark.parametrize("cancelled,error", [(True, False), (False, False), (False, True)])
def test_pending_startup_has_owned_late_result_and_explicit_completion(qtbot, cancelled, error):
    entered, release = Event(), Event()
    quits = []

    class Engine:
        def quit(self):
            quits.append(get_ident())

    class Future:
        def done(self):
            return False

        def cancel(self):
            entered.set()
            assert release.wait(5)
            return cancelled

        def result(self):
            if error:
                raise RuntimeError("startup failed")
            return Engine()

    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: Future(), poll_interval_ms=5)
    manager.prewarm()
    completed = QSignalSpy(manager.shutdown_complete)
    try:
        manager.shutdown()
        qtbot.waitUntil(entered.is_set)
        assert not manager.shutdown_finished
        assert manager.state is MatlabEngineState.SHUTTING_DOWN
        assert not manager._startup_disposal_thread.daemon
        assert completed.count() == 0
        manager.shutdown()
    finally:
        release.set()
    qtbot.waitUntil(lambda: manager.shutdown_finished)
    assert completed.count() == 1
    assert len(quits) == (0 if cancelled or error else 1)
    assert manager.state is MatlabEngineState.SHUT_DOWN


def test_quit_failure_retains_engine_and_reports_failure_once(qtbot):
    class Engine:
        def quit(self):
            raise RuntimeError("quit rejected")

    engine = Engine()
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: None, poll_interval_ms=5)
    manager._engine = engine
    manager._set_state(MatlabEngineState.READY)
    failures = QSignalSpy(manager.shutdown_failed)
    completed = QSignalSpy(manager.shutdown_complete)
    manager.shutdown()
    qtbot.waitUntil(lambda: failures.count() == 1)
    manager.shutdown()
    assert manager.state is MatlabEngineState.SHUTDOWN_FAILED
    assert not manager.shutdown_finished
    assert completed.count() == 0
    assert manager._failed_disposals[0].engine is engine
    manager.deleteLater()


@pytest.mark.parametrize("main_window", [False, True])
def test_close_during_fig_export_waits_for_worker_before_engine_quit(qtbot, monkeypatch, main_window):
    from config_model import AppConfig
    from ui import main_window as main_module
    from ui import plot_widgets
    from ui.control_panel import ScanSettings

    entered, release = Event(), Event()
    events = []
    dialogs = []
    for method in ("warning", "information", "critical"):
        monkeypatch.setattr(plot_widgets.QMessageBox, method, lambda *args: dialogs.append(args))

    class Engine:
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

        def figure(self, **_kwargs):
            return "figure"

        def savefig(self, *_args, **_kwargs):
            events.append("save-started")
            entered.set()
            assert release.wait(5)
            events.append("save-returned")

        def close(self, *_args, **_kwargs):
            events.append("figure-closed")

        def quit(self):
            assert widget._fig_engine_lease is None
            assert events[-1] == "figure-closed"
            events.append("engine-quit")

    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(double=lambda data: data, engine=SimpleNamespace(start_matlab=lambda **_kwargs: None)),
        raising=False,
    )
    if main_window:
        monkeypatch.setattr(main_module.MainWindow, "_begin_lazy_init", lambda _self: None)
        owner = main_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
        widget = owner.plot_widget
    else:
        owner = widget = plot_widgets.PlotWidget(ScanSettings())
    qtbot.addWidget(owner)
    widget.saved_files_list = []
    widget.error_list = []
    widget.matlab_engine_manager._engine = Engine()
    widget.matlab_engine_manager._set_state(MatlabEngineState.READY)
    payload = plot_widgets.PreparedMatlabFigPayload(
        np.array([1550.0, 1551.0]),
        (plot_widgets.PreparedMatlabFigTrace("Det 1", np.array([-1.0, -2.0]), (0.0, 0.0, 1.0)),),
    )
    widget._queue_matlab_fig((payload, "fake.fig", "Fake", "x", "y"))
    qtbot.waitUntil(entered.is_set)
    thread, worker = widget.matlab_save_thread, widget.matlab_save_worker
    forbidden_calls = []
    monkeypatch.setattr(thread, "terminate", lambda: forbidden_calls.append("terminate"))
    owner.show()
    monitor_entered, monitor_release = Event(), Event()
    camera = None
    try:
        if main_window:
            from config_model import CameraConfig
            from hardware.ct400_types import PowerData
            from hardware.dummy_ct400 import DummyCT400
            from hardware.simulated_camera import SimulatedCamera

            device = DummyCT400(scan_duration=0)
            owner._on_ct400_initialized(device)
            owner.is_ct400_connected_state = True
            monitor = owner.histogram_control
            monitor.wavelength_input.setText("1550")
            monitor.laser_power.setText("1")

            def read():
                monitor_entered.set()
                assert monitor_release.wait(5)
                return PowerData(1, {})

            monkeypatch.setattr(device, "get_all_powers", read)
            monitor._start_monitoring()
            monitor.timer.stop()
            monitor._request_power_fetch_from_worker()
            qtbot.waitUntil(monitor_entered.is_set)
            config = CameraConfig(identifier="overlap", name="Overlap", backend="simulation")
            camera = SimulatedCamera("overlap", width=8, height=8, frame_interval=0.01)
            panel = owner._create_camera_panel(None, config)
            owner.camera_layout.addWidget(panel)
            panel.set_camera(camera)
            owner.camera_panels[config.identifier] = panel
            owner.cameras.append(camera)
            assert camera.open()
        assert not owner.close()
        assert not owner.close()
        widget.matlab_engine_manager.shutdown()
        assert not widget.shutdown_finished
        assert not widget.matlab_engine_manager.shutdown_finished
        assert widget.matlab_save_worker is worker
        assert thread.isRunning()
        assert events == ["save-started"]
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qtbot.waitUntil(lambda: heartbeat == [True])
        if camera is not None:
            qtbot.waitUntil(lambda: camera._frame_count >= 2)
            assert camera.is_streaming
            assert owner.histogram_control.power_fetch_thread.isRunning()
    finally:
        monitor_release.set()
        release.set()
        qtbot.waitUntil(lambda: widget.shutdown_finished)
    assert forbidden_calls == []
    assert dialogs == []
    assert events == ["save-started", "save-returned", "figure-closed", "engine-quit"]
    if main_window:
        qtbot.waitUntil(lambda: owner._shutdown_complete)
        assert not camera.is_streaming
    assert owner.close()


@pytest.mark.parametrize("starting", [False, True])
def test_mainwindow_defers_during_startup_or_blocked_engine_quit(qtbot, monkeypatch, starting):
    from config_model import AppConfig
    from ui import main_window as module

    monkeypatch.setattr(module.MainWindow, "_begin_lazy_init", lambda _self: None)
    window = module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    manager = window.matlab_engine_manager
    assert window._scan_plot_widget is None
    entered, release = Event(), Event()
    calls = []

    class Engine:
        def quit(self):
            if not starting:
                entered.set()
                assert release.wait(5)
            calls.append("quit")

    class Future:
        def cancel(self):
            return False

        def result(self):
            entered.set()
            assert release.wait(5)
            return Engine()

    manager._available = True
    manager._start_engine = lambda **_kwargs: calls.append("start") or Future()
    if starting:
        manager._set_state(MatlabEngineState.IDLE)
        manager.prewarm()
    else:
        manager._engine = Engine()
        manager._set_state(MatlabEngineState.READY)
    window.show()
    try:
        assert not window.close()
        qtbot.waitUntil(entered.is_set)
        assert window.isVisible()
        assert not window._shutdown_complete
        assert not manager.shutdown_finished
        manager.ready.emit()
        manager.startup_failed.emit("late startup signal")
        manager.request_engine()
        assert calls == (["start"] if starting else [])
        assert window._scan_plot_widget is None
    finally:
        release.set()
        qtbot.waitUntil(lambda: window._shutdown_complete)
    assert window._scan_plot_widget is None
    assert calls == (["start", "quit"] if starting else ["quit"])


def test_matlab_shutdown_and_fig_retirement_exit_in_fresh_process(tmp_path):
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(Path(__file__).resolve()),
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
    assert "QMutex: destroying locked mutex" not in result.stderr
