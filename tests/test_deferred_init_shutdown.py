from PySide6.QtGui import QCloseEvent

from config_model import AppConfig, CameraConfig
from hardware.simulated_camera import SimulatedCamera
from logic.task_runner import BaseWorker, TaskRunner
from ui import main_window as main_window_module


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


def test_close_defers_for_ct400_or_piezo_task_without_stopping_thread(qtbot, monkeypatch):
    for role in ("ct400", "piezo"):
        window = _window(qtbot, monkeypatch)
        task = _task(window)
        setattr(window, f"{role}_task", task)
        # Preserve the exact legacy lookup name so this probe would exercise
        # the former closeEvent force-stop route on the pre-R04c implementation.
        setattr(window, f"{role}_init_thread", task.worker_thread)
        calls = []
        monkeypatch.setattr(task.worker_thread, "isRunning", lambda: True)
        monkeypatch.setattr(task.worker_thread, "quit", lambda: calls.append("quit"))
        monkeypatch.setattr(task.worker_thread, "wait", lambda *_args: calls.append("wait") or False)
        monkeypatch.setattr(task.worker_thread, "terminate", lambda: calls.append("terminate"))

        event = QCloseEvent()
        window.closeEvent(event)

        assert not event.isAccepted()
        assert window._pending_init_close
        assert calls == []
        window.deleteLater()


def test_camera_init_defers_cleanup_until_last_thread_finished(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    tasks = [_task(window) for _ in range(3)]  # CT400 and two camera init tasks.
    camera_cleanups = []
    vimba_cleanups = []
    scheduled = []
    monkeypatch.setattr(window.histogram_control, "cleanup_worker_thread", lambda: None)
    monkeypatch.setattr(window.alignment_tab, "cleanup", lambda: None)
    monkeypatch.setattr(window, "_cleanup_cameras", lambda: camera_cleanups.append("camera"))
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: vimba_cleanups.append("vimba"))
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
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
    assert camera_cleanups == ["camera"]
    assert vimba_cleanups == ["vimba"]
    assert not window._pending_init_close


def test_simulated_camera_result_during_pending_close_is_cleaned(qtbot, monkeypatch):
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
    monkeypatch.setattr(window.histogram_control, "cleanup_worker_thread", lambda: None)
    monkeypatch.setattr(window.alignment_tab, "cleanup", lambda: None)

    def record_camera_cleanup():
        cameras_cleaned.append(len(window.cameras))
        original_camera_cleanup()

    monkeypatch.setattr(window, "_cleanup_cameras", record_camera_cleanup)
    monkeypatch.setattr(window, "_cleanup_vimbasystem", lambda: vimba_cleanups.append(True))
    monkeypatch.setattr(
        main_window_module.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    window.show()
    assert not window.close()
    assert window._pending_init_close
    assert camera.open()
    window._on_camera_initialized("late-camera", camera, camera_config)
    assert window.cameras == [camera]
    assert camera.is_streaming
    assert cameras_cleaned == []
    assert vimba_cleanups == []

    task.worker_thread.finished.emit()
    assert task not in window._init_tasks
    assert len(scheduled) == 1
    assert scheduled[0][1]() is True
    assert window.cameras == []
    assert not camera.is_streaming
    assert cameras_cleaned == [1]
    assert vimba_cleanups == [True]
    assert not window._pending_init_close


def test_refresh_and_direct_init_are_blocked_while_init_close_pending(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window._pending_init_close = True
    starts = []
    monkeypatch.setattr(TaskRunner, "start", lambda _task: starts.append("started"))
    window._on_refresh_instruments_triggered()
    window._init_ct400_lazy()
    window._init_piezos_lazy()
    assert starts == []
