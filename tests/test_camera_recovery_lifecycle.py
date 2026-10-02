import threading

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QSignalSpy

from config_model import AppConfig, CameraConfig
from hardware import camera_init_worker
from hardware.camera_recovery_worker import CameraRecoveryWorker
from hardware.simulated_camera import SimulatedCamera
from logic.task_runner import TaskRunner
from ui import main_window as main_window_module
from ui.camera_widgets import AutoOpWorker, CameraPanel


def _qt_thread_stopped(thread):
    if thread is None:
        return True
    try:
        return not thread.isRunning()
    except RuntimeError:
        return True


def _ensure_histogram_worker_stops(window, request, monkeypatch):
    thread = window.histogram_control.power_fetch_thread
    cleanup = window.histogram_control.cleanup_worker_thread

    def cleanup_once():
        if not _qt_thread_stopped(thread):
            cleanup()

    monkeypatch.setattr(window.histogram_control, "cleanup_worker_thread", cleanup_once)

    def cleanup_if_running():
        cleanup_once()

    request.addfinalizer(cleanup_if_running)
    return thread


def test_placeholder_assignment_watchdog_dispatches_recovery_request(qtbot, monkeypatch):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    config = CameraConfig(identifier="probe", enabled=True, name="Probe", backend="simulation")
    panel = CameraPanel(None, "Probe", config)
    qtbot.addWidget(panel)
    panel.set_camera(SimulatedCamera(identifier="probe", width=8, height=8))
    panel.show()
    requests = QSignalSpy(panel.recovery_requested)

    panel.watchdog_timer.timeout.emit()

    assert requests.count() == 1
    assert requests.at(0)[0] == "probe"
    assert panel._recovery_active
    assert not panel.watchdog_timer.isActive()
    assert not panel.controls_container.isEnabled()


def test_physical_camera_retained_by_gui_has_application_thread_affinity(qtbot, monkeypatch):
    app = QCoreApplication.instance()
    observed = {}

    class FakePhysicalCamera(QObject):
        def __init__(self, **kwargs):
            super().__init__()
            self.identifier = kwargs["identifier"]
            self.camera_name = kwargs["camera_name"]
            self.is_mono = True
            observed["initial_thread_is_application"] = self.thread() == app.thread()

        def open(self):
            observed["open_thread_name"] = QThread.currentThread().objectName()
            return True

        def close(self):
            return None

    monkeypatch.setattr(camera_init_worker, "VimbaCam", FakePhysicalCamera)
    config = CameraConfig(identifier="physical-probe", enabled=True, name="Physical probe", backend="vimba")
    worker = camera_init_worker.CameraInitWorker(config.identifier, config)
    results = QSignalSpy(worker.camera_initialized)
    task = TaskRunner(worker)
    task.worker_thread.setObjectName("camera-init-worker-thread")
    task.start()

    qtbot.waitUntil(lambda: results.count() == 1, timeout=2000)
    retained_camera = results.at(0)[1]
    assert not observed["initial_thread_is_application"]
    assert observed["open_thread_name"] == "camera-init-worker-thread"
    assert retained_camera.thread() == app.thread()
    qtbot.waitUntil(lambda: _thread_finished(task), timeout=2000)


def test_watchdog_allows_one_attempt_until_a_fresh_frame(qtbot, monkeypatch):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    config = CameraConfig(identifier="latch", enabled=True, name="Latch", backend="simulation")
    panel = CameraPanel(None, "Latch", config)
    qtbot.addWidget(panel)
    panel.set_camera(SimulatedCamera(identifier="latch", width=8, height=8))
    panel.show()
    requests = QSignalSpy(panel.recovery_requested)

    panel.watchdog_timer.timeout.emit()
    panel.recovery_failed("failed")
    panel.watchdog_timer.timeout.emit()
    assert requests.count() == 1
    assert not panel.watchdog_timer.isActive()

    panel.process_new_frame_data(np.zeros((8, 8), dtype=np.uint8))
    assert panel.watchdog_timer.isActive()
    panel.watchdog_timer.timeout.emit()
    assert requests.count() == 2


def test_success_without_frames_does_not_retry_automatically(qtbot, monkeypatch):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    config = CameraConfig(identifier="no-frames", enabled=True, name="No frames", backend="simulation")
    panel = CameraPanel(None, "No frames", config)
    qtbot.addWidget(panel)
    panel.set_camera(SimulatedCamera(identifier="no-frames", width=8, height=8))
    panel.show()
    requests = QSignalSpy(panel.recovery_requested)

    panel.watchdog_timer.timeout.emit()
    panel.recovery_succeeded()
    panel.watchdog_timer.timeout.emit()

    assert requests.count() == 1
    assert panel._recovery_failed
    assert "unavailable" in panel.video_label.text().lower()
    assert not panel.controls_container.isEnabled()


def test_recovery_worker_emits_truthful_result_and_finished_once():
    camera = SimulatedCamera(identifier="worker", width=8, height=8)
    camera.recover_once = lambda: True
    worker = CameraRecoveryWorker(camera, "worker")
    results = QSignalSpy(worker.recovery_finished)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert results.at(0) == ["worker", True, "", None]
    assert finished.count() == 1

    failed_camera = SimulatedCamera(identifier="failed-worker", width=8, height=8)
    failed_camera.recover_once = lambda: False
    failed_worker = CameraRecoveryWorker(failed_camera, "failed-worker")
    failed_results = QSignalSpy(failed_worker.recovery_finished)
    failed_finished = QSignalSpy(failed_worker.finished)
    failed_worker.run()
    assert failed_results.count() == 1
    assert failed_results.at(0)[1] is False
    assert "failed" in failed_results.at(0)[2].lower()
    assert failed_finished.count() == 1

    exception_camera = SimulatedCamera(identifier="exception-worker", width=8, height=8)

    def raise_recovery_error():
        raise RuntimeError("injected recovery exception")

    exception_camera.recover_once = raise_recovery_error
    exception_worker = CameraRecoveryWorker(exception_camera, "exception-worker")
    exception_results = QSignalSpy(exception_worker.recovery_finished)
    exception_finished = QSignalSpy(exception_worker.finished)
    exception_worker.run()
    assert exception_results.count() == 1
    assert exception_results.at(0)[1] is False
    assert "injected recovery exception" in exception_results.at(0)[2]
    assert exception_finished.count() == 1


def test_recovery_worker_emits_and_clears_task_runner_reference():
    camera = SimulatedCamera(identifier="task-worker", width=8, height=8)
    camera.recover_once = lambda: True
    worker = CameraRecoveryWorker(camera, "task-worker")
    task = TaskRunner(worker, auto_start_run=False)
    worker.task = task
    results = QSignalSpy(worker.recovery_finished)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert results.at(0) == ["task-worker", True, "", task]
    assert worker.task is None
    assert finished.count() == 1


def test_panel_serializes_auto_exposure_and_gain_and_suppresses_watchdog(qtbot, monkeypatch):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    config = CameraConfig(identifier="auto", enabled=True, name="Auto", backend="simulation")
    panel = CameraPanel(None, "Auto", config)
    qtbot.addWidget(panel)
    panel.set_camera(SimulatedCamera(identifier="auto", width=8, height=8))
    panel.show()
    started = []
    monkeypatch.setattr(panel._thread_pool, "start", lambda worker: started.append(worker))
    panel.watchdog_timer.start()

    panel.recovery_started()
    panel._start_auto_op("auto_exposure")
    assert started == []
    panel.recovery_failed("recovery active guard probe")
    panel.process_new_frame_data(np.zeros((8, 8), dtype=np.uint8))

    panel._start_auto_op("auto_exposure")
    panel._start_auto_op("auto_gain")

    assert len(started) == 1
    assert panel.auto_operation_active
    assert not panel.watchdog_timer.isActive()
    assert not panel.exposure_btn.isEnabled()
    assert not panel.gain_btn.isEnabled()

    panel.handle_auto_finished("auto_exposure")
    assert not panel.auto_operation_active
    assert panel.watchdog_timer.isActive()


def test_auto_operation_error_releases_panel_ownership(qtbot, monkeypatch):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    monkeypatch.setattr("ui.camera_widgets.QTimer.singleShot", lambda *_args: None)
    config = CameraConfig(identifier="auto-error", enabled=True, name="Auto error", backend="simulation")
    camera = SimulatedCamera(identifier="auto-error", width=8, height=8)

    def fail_auto_exposure():
        raise RuntimeError("injected auto-operation error")

    camera.set_auto_exposure_once = fail_auto_exposure
    panel = CameraPanel(None, "Auto error", config)
    qtbot.addWidget(panel)
    panel.set_camera(camera)
    panel.show()
    panel._auto_op_active = True
    panel.watchdog_timer.stop()
    completion = QSignalSpy(panel.auto_operation_finished)

    AutoOpWorker(camera, "auto_exposure", panel).run()

    qtbot.waitUntil(lambda: completion.count() == 1, timeout=2000)
    assert not panel.auto_operation_active
    assert panel.exposure_btn.isEnabled()
    assert panel.gain_btn.isEnabled()


def test_mainwindow_owns_one_recovery_per_camera_and_defers_close(qtbot, monkeypatch):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)

    class FakeThreadSignals(QObject):
        finished = Signal()

    class FakeTaskRunner:
        def __init__(self, worker):
            self.worker = worker
            self.worker_thread = FakeThreadSignals()

        def start(self):
            started.append(self)

    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    cameras = []
    for identifier in ("first", "second"):
        camera = SimulatedCamera(identifier=identifier, width=8, height=8)
        panel_config = CameraConfig(identifier=identifier, enabled=True, name=identifier, backend="simulation")
        panel = window._create_camera_panel(None, panel_config)
        panel.set_camera(camera)
        window.camera_panels[identifier] = panel
        cameras.append(camera)
    window.cameras.extend(cameras)

    started = []
    monkeypatch.setattr(main_window_module, "TaskRunner", FakeTaskRunner)
    window._request_camera_recovery("first")
    original = window._camera_recovery_tasks["first"]
    window._request_camera_recovery("first")
    window._request_camera_recovery("second")
    assert len(started) == 2
    assert len(window._camera_recovery_tasks) == 2
    window._on_camera_recovery_result("first", True, "", original)
    assert window._camera_recovery_tasks["first"] is original

    replacement = FakeTaskRunner(CameraRecoveryWorker(cameras[0], "first"))
    window._camera_recovery_tasks["first"] = replacement
    window._on_camera_recovery_thread_finished("first", original)
    assert window._camera_recovery_tasks["first"] is replacement

    scheduled = []
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda _delay, callback: scheduled.append(callback))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window._pending_camera_lifecycle_close

    window._on_camera_recovery_thread_finished("first", replacement)
    assert "first" not in window._camera_recovery_tasks
    assert not scheduled
    last_task = window._camera_recovery_tasks["second"]
    window._on_camera_recovery_thread_finished("second", last_task)
    assert not window._camera_recovery_tasks
    assert len(scheduled) == 1


def test_mainwindow_defers_shutdown_for_active_auto_operation(qtbot, monkeypatch, request):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    config = CameraConfig(identifier="auto-close", enabled=True, name="Auto", backend="simulation")
    panel = window._create_camera_panel(None, config)
    panel.set_camera(SimulatedCamera(identifier="auto-close", width=8, height=8))
    window.camera_panels[config.identifier] = panel
    window.cameras.append(panel.camera)
    window._connect_camera_signals(panel.camera, panel)
    panel._auto_op_active = True
    cleanup_events = []
    monkeypatch.setattr(window, "_cleanup_cameras", lambda: cleanup_events.append("camera"))
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: cleanup_events.append("vimba"))
    scheduled = []
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda _delay, callback: scheduled.append(callback))

    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window._pending_camera_lifecycle_close

    panel.handle_auto_finished("auto_exposure")
    assert len(scheduled) == 1
    assert cleanup_events == []
    scheduled[0]()
    assert cleanup_events == ["camera", "vimba"]
    assert _qt_thread_stopped(histogram_thread)


def test_shutdown_waits_for_recovery_thread_finished_before_camera_and_vimba_cleanup(qtbot, monkeypatch, request):
    monkeypatch.setattr(CameraPanel, "_start_conversion_worker", lambda _panel: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": "simulation"}))
    qtbot.addWidget(window)
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    config = CameraConfig(identifier="gated-recovery", enabled=True, name="Gated", backend="simulation")
    camera = SimulatedCamera(identifier=config.identifier, width=8, height=8)
    entered = threading.Event()
    release = threading.Event()

    def gated_recovery():
        entered.set()
        return release.wait(timeout=10)

    camera.recover_once = gated_recovery
    panel = window._create_camera_panel(None, config)
    panel.set_camera(camera)
    window.camera_panels[config.identifier] = panel
    window.cameras.append(camera)
    window._connect_camera_signals(camera, panel)
    cleanup_events = []
    scheduled = []
    monkeypatch.setattr(window, "_cleanup_cameras", lambda: cleanup_events.append("camera"))
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: cleanup_events.append("vimba"))
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda _delay, callback: scheduled.append(callback))

    window._request_camera_recovery(config.identifier)
    qtbot.waitUntil(entered.is_set, timeout=5000)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert cleanup_events == []

    release.set()
    qtbot.waitUntil(lambda: config.identifier not in window._camera_recovery_tasks, timeout=5000)
    assert cleanup_events == []
    assert len(scheduled) == 1

    scheduled[0]()
    assert cleanup_events == ["camera", "vimba"]
    assert _qt_thread_stopped(histogram_thread)


def _thread_finished(task):
    try:
        return not task.worker_thread.isRunning()
    except RuntimeError:
        return True
