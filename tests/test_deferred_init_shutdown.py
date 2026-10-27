from threading import Event

from PySide6.QtGui import QCloseEvent

from config_model import AppConfig, CameraConfig
from hardware.ct400_init_worker import CT400InitOutcome, CT400InitWorker
from hardware.simulated_camera import SimulatedCamera
from logic.task_runner import BaseWorker, TaskRunner
from ui import main_window as main_window_module


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


def _window(qtbot, monkeypatch, config=None):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    config = config or AppConfig(instruments={"ct400_backend": "simulation"})
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    return window


def _task(window):
    task = TaskRunner(BaseWorker())
    window._track_init_task(task)
    return task


def test_close_defers_for_ct400_or_piezo_task_without_stopping_thread(qtbot, monkeypatch, request):
    for role in ("ct400", "piezo"):
        window = _window(qtbot, monkeypatch)
        histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
        task = _task(window)
        setattr(window, f"{role}_task", task)
        # Preserve the exact legacy lookup name so this probe would exercise
        # the former closeEvent force-stop route on the pre-R04c implementation.
        setattr(window, f"{role}_init_thread", task.worker_thread)
        calls = []
        monkeypatch.setattr(task.worker_thread, "isRunning", lambda: True)
        monkeypatch.setattr(task.worker_thread, "quit", lambda calls=calls: calls.append("quit"))
        monkeypatch.setattr(task.worker_thread, "wait", lambda *_args, calls=calls: calls.append("wait") or False)
        monkeypatch.setattr(task.worker_thread, "terminate", lambda calls=calls: calls.append("terminate"))

        event = QCloseEvent()
        window.closeEvent(event)

        assert not event.isAccepted()
        assert window._pending_init_close
        assert calls == []
        window.histogram_control.cleanup_worker_thread()
        qtbot.waitUntil(lambda window=window: window.histogram_control.shutdown_finished)
        assert _qt_thread_stopped(histogram_thread)
        task.worker_thread.finished.emit()
        qtbot.waitUntil(lambda window=window: window._shutdown_complete)


def test_camera_init_defers_cleanup_until_last_thread_finished(qtbot, monkeypatch, request):
    window = _window(qtbot, monkeypatch)
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    tasks = [_task(window) for _ in range(3)]  # CT400 and two camera init tasks.
    camera_cleanups = []
    vimba_cleanups = []
    scheduled = []
    original_cleanup = window._cleanup_cameras

    def record_camera_cleanup():
        camera_cleanups.append("camera")
        original_cleanup()

    monkeypatch.setattr(window, "_cleanup_cameras", record_camera_cleanup)
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: vimba_cleanups.append("vimba"))
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda delay, *args: scheduled.append((delay, args[-1])),
    )

    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert camera_cleanups == []
    assert vimba_cleanups == []

    # worker.finished is not the ownership boundary.
    tasks[0].worker.finished.emit()
    assert len(window._init_tasks) == 3
    assert scheduled == []

    tasks[0].worker_thread.finished.emit()
    assert len(window._init_tasks) == 2
    assert scheduled == []
    tasks[1].worker_thread.finished.emit()
    assert len(window._init_tasks) == 1
    assert scheduled == []
    tasks[2].worker_thread.finished.emit()
    assert window._init_tasks == set()
    assert len(scheduled) == 1
    assert scheduled[0][0] == 0
    window._on_init_task_thread_finished(tasks[2])
    assert len(scheduled) == 1

    # Execute the GUI-loop retry. Cleanup is now permitted and runs once.
    scheduled[0][1]()
    qtbot.waitUntil(lambda window=window: window.histogram_control.shutdown_finished)
    scheduled[-1][1]()
    assert camera_cleanups == ["camera"]
    assert vimba_cleanups == ["vimba"]
    assert not window._pending_init_close
    assert _qt_thread_stopped(histogram_thread)


def test_simulated_camera_result_during_pending_close_is_cleaned(qtbot, monkeypatch, request):
    config = AppConfig(
        instruments={"ct400_backend": "simulation"},
        cameras={
            "late-camera": CameraConfig(
                identifier="late-camera",
                enabled=True,
                name="Late camera",
                backend="simulation",
                simulation_width=8,
                simulation_height=8,
            )
        },
    )
    window = _window(qtbot, monkeypatch, config)
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    task = _task(window)
    camera_config = config.cameras["late-camera"]
    camera = SimulatedCamera(identifier="late-camera", camera_name="Late camera", width=8, height=8)
    panel = window._create_camera_panel(None, camera_config)
    window.camera_panels["late-camera"] = panel
    window.camera_container.layout().addWidget(panel)
    cameras_cleaned = []
    vimba_cleanups = []
    scheduled = []
    original_camera_cleanup = window._cleanup_cameras

    def record_camera_cleanup():
        cameras_cleaned.append(len(window.cameras))
        original_camera_cleanup()

    monkeypatch.setattr(window, "_cleanup_cameras", record_camera_cleanup)
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: vimba_cleanups.append(True))
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda delay, *args: scheduled.append((delay, args[-1])),
    )
    window.show()
    assert not window.close()
    assert window._pending_init_close
    assert camera.open()
    window._on_camera_initialized("late-camera", camera, camera_config)
    assert window.cameras == []
    assert not camera.is_streaming
    assert cameras_cleaned == []
    assert vimba_cleanups == []

    task.worker_thread.finished.emit()
    assert task not in window._init_tasks
    assert len(scheduled) == 1
    assert scheduled[0][1]() is False
    qtbot.waitUntil(lambda window=window: window.histogram_control.shutdown_finished)
    assert scheduled[-1][1]() is True
    assert window.cameras == []
    assert not camera.is_streaming
    assert cameras_cleaned == [0]
    assert vimba_cleanups == [True]
    assert not window._pending_init_close
    assert _qt_thread_stopped(histogram_thread)


def test_refresh_and_direct_init_are_blocked_while_init_close_pending(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window._pending_init_close = True
    starts = []
    monkeypatch.setattr(TaskRunner, "start", lambda _task: starts.append("started"))
    window._on_refresh_instruments_triggered()
    window._init_ct400_lazy()
    window._init_piezos_lazy()
    assert starts == []


def test_close_defers_active_ct400_retry_until_terminal_thread_boundary(qtbot, monkeypatch, request):
    entered = Event()
    release = Event()
    worker_runs = []
    stop_requests = []
    candidates = []

    class FakePhysicalCT400:
        def __init__(self):
            self.handle = 1
            self.close_calls = 0
            self.laser_calls = 0

        def close(self):
            self.close_calls += 1

    class BlockingCT400InitWorker(CT400InitWorker):
        def run(self):
            entered.set()
            release.wait()
            candidate = FakePhysicalCT400()
            candidates.append(candidate)
            outcome = CT400InitOutcome(candidate, "DISCONNECTED", "Fake CT400 initialized.")
            self._terminal_outcome = outcome
            self.initialization_completed.emit(outcome)
            self.finished.emit()
            worker_runs.append("finished")

        def stop(self):
            stop_requests.append("stop")
            super().stop()

    monkeypatch.setattr(main_window_module, "CT400InitWorker", BlockingCT400InitWorker)
    monkeypatch.setattr(main_window_module, "CT400", FakePhysicalCT400)
    window = _window(qtbot, monkeypatch, AppConfig(instruments={"ct400_backend": "physical"}))
    histogram_thread = _ensure_histogram_worker_stops(window, request, monkeypatch)
    task_starts = []
    original_start = TaskRunner.start

    def track_start(task):
        task_starts.append(task)
        original_start(task)

    monkeypatch.setattr(TaskRunner, "start", track_start)
    window._retry_ct400_initialization()
    task = window.ct400_task
    assert task is not None
    assert entered.wait(2)

    assert window.close() is False
    assert window._pending_init_close
    assert task.worker_thread.isRunning()
    waiting_message = window.statusBar().currentMessage()
    window._retry_ct400_initialization()
    assert window.statusBar().currentMessage() == waiting_message
    assert task_starts == [task]
    assert stop_requests == []
    assert worker_runs == []

    release.set()
    qtbot.waitUntil(lambda: _qt_thread_stopped(task.worker_thread), timeout=3000)
    qtbot.waitUntil(lambda: window._camera_cleanup_complete, timeout=3000)

    assert window._active_ct400_init_attempt_id is None
    assert window.ct400_device is None
    assert candidates[0].close_calls == 1
    assert candidates[0].laser_calls == 0
    assert worker_runs == ["finished"]
    assert stop_requests == []
    assert _qt_thread_stopped(histogram_thread)
