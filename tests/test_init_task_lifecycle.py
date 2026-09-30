from PySide6.QtTest import QSignalSpy

from config_model import AppConfig, CameraConfig
from hardware import ct400_init_worker, piezo_init_worker
from hardware.dummy_ct400 import DummyCT400
from logic.task_runner import BaseWorker, TaskRunner
from ui import main_window as main_window_module


def _config(backend="physical"):
    return AppConfig(instruments={"ct400_backend": backend})


def test_ct400_missing_dll_emits_dummy_and_finished_once(qtbot, monkeypatch):
    worker = ct400_init_worker.CT400InitWorker(_config())
    monkeypatch.setattr(worker, "_find_dll", lambda: None)
    results = QSignalSpy(worker.ct400_initialized)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert isinstance(results.at(0)[0], DummyCT400)
    assert finished.count() == 1


def test_ct400_missing_dll_stops_taskrunner_thread(qtbot, monkeypatch):
    worker = ct400_init_worker.CT400InitWorker(_config())
    monkeypatch.setattr(worker, "_find_dll", lambda: None)
    results = QSignalSpy(worker.ct400_initialized)
    task = TaskRunner(worker)
    task.start()

    qtbot.waitUntil(lambda: results.count() == 1, timeout=2000)
    qtbot.waitUntil(lambda: _thread_finished(task), timeout=2000)


def _thread_finished(task):
    try:
        return not task.thread.isRunning()
    except RuntimeError:
        return True


def test_ct400_simulation_emits_finished_once():
    worker = ct400_init_worker.CT400InitWorker(_config("simulation"))
    results = QSignalSpy(worker.ct400_initialized)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert isinstance(results.at(0)[0], DummyCT400)
    assert finished.count() == 1


def test_ct400_successful_fake_constructor_emits_result_and_finished(monkeypatch, tmp_path):
    dll_path = tmp_path / "fake.dll"
    dll_path.touch()
    expected_device = object()
    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", lambda _worker: dll_path)
    monkeypatch.setattr(ct400_init_worker, "CT400", lambda _path: expected_device)
    worker = ct400_init_worker.CT400InitWorker(_config())
    results = QSignalSpy(worker.ct400_initialized)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert results.at(0)[0] is expected_device
    assert finished.count() == 1


def test_ct400_handled_constructor_failure_emits_dummy_and_finished(qtbot, monkeypatch, tmp_path):
    dll_path = tmp_path / "fake.dll"
    dll_path.touch()
    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", lambda _worker: dll_path)

    def fail_constructor(_path):
        raise ct400_init_worker.CT400InitializationError("fake initialization failure")

    monkeypatch.setattr(ct400_init_worker, "CT400", fail_constructor)
    worker = ct400_init_worker.CT400InitWorker(_config())
    results = QSignalSpy(worker.ct400_initialized)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert isinstance(results.at(0)[0], DummyCT400)
    assert finished.count() == 1


def test_ct400_unexpected_constructor_failure_emits_dummy_and_finished(qtbot, monkeypatch, tmp_path):
    dll_path = tmp_path / "fake.dll"
    dll_path.touch()
    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", lambda _worker: dll_path)
    monkeypatch.setattr(ct400_init_worker, "CT400", lambda _path: (_ for _ in ()).throw(RuntimeError("fake")))
    worker = ct400_init_worker.CT400InitWorker(_config())
    results = QSignalSpy(worker.ct400_initialized)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert isinstance(results.at(0)[0], DummyCT400)
    assert finished.count() == 1


def test_ct400_stopped_before_run_emits_finished_without_initializing(monkeypatch):
    worker = ct400_init_worker.CT400InitWorker(_config())
    monkeypatch.setattr(worker, "_find_dll", lambda: (_ for _ in ()).throw(AssertionError("discovery called")))
    results = QSignalSpy(worker.ct400_initialized)
    finished = QSignalSpy(worker.finished)

    worker.stop()
    worker.run()

    assert results.count() == 0
    assert finished.count() == 1


def test_piezo_normal_discovery_emits_result_and_finished_once(monkeypatch, tmp_path):
    dll_path = tmp_path / "fake.dll"
    dll_path.touch()
    config = _config().instruments.model_copy(update={"piezo_dll_path": str(dll_path), "piezo_left_serial": "L"})
    monkeypatch.setattr(
        piezo_init_worker,
        "PiezoController",
        type(
            "FakePiezo", (), {"find_devices": staticmethod(lambda _path: ["L"]), "__init__": lambda self, _path: None}
        ),
    )
    worker = piezo_init_worker.PiezoInitWorker(config)
    results = QSignalSpy(worker.piezos_initialized)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert results.count() == 1
    assert results.at(0)[0] is not None
    assert results.at(0)[1] is None
    assert finished.count() == 1


def test_piezo_unexpected_discovery_failure_emits_failure_and_finished_once(monkeypatch, tmp_path):
    dll_path = tmp_path / "fake.dll"
    dll_path.touch()
    config = _config().instruments.model_copy(update={"piezo_dll_path": str(dll_path)})
    monkeypatch.setattr(
        piezo_init_worker.PiezoController,
        "find_devices",
        lambda _path: (_ for _ in ()).throw(RuntimeError("fake")),
    )
    worker = piezo_init_worker.PiezoInitWorker(config)
    failures = QSignalSpy(worker.initialization_failed)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert failures.count() == 1
    assert finished.count() == 1


def test_piezo_missing_library_emits_failure_and_finished_once():
    worker = piezo_init_worker.PiezoInitWorker(_config().instruments)
    failures = QSignalSpy(worker.initialization_failed)
    finished = QSignalSpy(worker.finished)

    worker.run()

    assert failures.count() == 1
    assert finished.count() == 1


def test_piezo_stopped_before_run_emits_finished_without_discovery(monkeypatch):
    monkeypatch.setattr(
        piezo_init_worker.PiezoController,
        "find_devices",
        lambda _path: (_ for _ in ()).throw(AssertionError("discovery called")),
    )
    worker = piezo_init_worker.PiezoInitWorker(_config().instruments)
    results = QSignalSpy(worker.piezos_initialized)
    failures = QSignalSpy(worker.initialization_failed)
    finished = QSignalSpy(worker.finished)

    worker.stop()
    worker.run()

    assert results.count() == 0
    assert failures.count() == 0
    assert finished.count() == 1


def _make_main_window(qtbot, monkeypatch):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(_config("simulation"))
    qtbot.addWidget(window)
    return window


def test_mainwindow_clears_finished_ct400_task_and_refresh_restarts(qtbot, monkeypatch):
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *args, **kwargs: None)
    window = _make_main_window(qtbot, monkeypatch)
    window._init_ct400_lazy()
    first_task = window.ct400_task
    assert first_task in window._init_tasks
    thread_destroyed = QSignalSpy(first_task.thread.destroyed)
    qtbot.waitUntil(lambda: window.ct400_task is None, timeout=3000)
    assert first_task not in window._init_tasks

    assert first_task is not None
    qtbot.waitUntil(lambda: thread_destroyed.count() == 1, timeout=3000)
    assert window.ct400_init_thread is None

    window._on_refresh_instruments_triggered()
    replacement_task = window.ct400_task
    assert replacement_task is not None
    assert replacement_task is not first_task
    qtbot.waitUntil(lambda: window.ct400_task is None, timeout=3000)
    assert replacement_task not in window._init_tasks
    assert window.ct400_init_thread is None


def test_mainwindow_clears_finished_piezo_task_and_refresh_restarts(qtbot, monkeypatch):
    monkeypatch.setattr(main_window_module.QMessageBox, "critical", lambda *args, **kwargs: None)
    window = _make_main_window(qtbot, monkeypatch)
    window._init_piezos_lazy()
    first_task = window.piezo_task
    assert first_task in window._init_tasks
    thread_destroyed = QSignalSpy(first_task.thread.destroyed)
    qtbot.waitUntil(lambda: window.piezo_task is None, timeout=3000)
    assert first_task not in window._init_tasks

    assert first_task is not None
    qtbot.waitUntil(lambda: thread_destroyed.count() == 1, timeout=3000)
    assert window.piezo_init_thread is None

    window._on_refresh_instruments_triggered()
    replacement_task = window.piezo_task
    assert replacement_task is not None
    assert replacement_task is not first_task
    qtbot.waitUntil(lambda: window.piezo_task is None, timeout=3000)
    assert replacement_task not in window._init_tasks
    assert window.piezo_init_thread is None


def test_old_init_task_completion_cannot_clear_newer_task(qtbot, monkeypatch):
    window = _make_main_window(qtbot, monkeypatch)
    old_task = object()
    current_task = TaskRunner(BaseWorker())
    ct400_thread = current_task.thread
    piezo_thread = current_task.thread
    window.ct400_task = current_task
    window.ct400_init_thread = ct400_thread
    window.piezo_task = current_task
    window.piezo_init_thread = piezo_thread
    window._init_tasks.add(current_task)

    window._on_ct400_init_task_finished(old_task)
    window._on_piezo_init_task_finished(old_task)
    window._on_init_task_thread_finished(old_task)

    assert window.ct400_task is current_task
    assert window.ct400_init_thread is ct400_thread
    assert window.piezo_task is current_task
    assert window.piezo_init_thread is piezo_thread
    assert current_task in window._init_tasks


def test_worker_finished_does_not_release_task_before_thread_finished(qtbot, monkeypatch):
    window = _make_main_window(qtbot, monkeypatch)
    task = TaskRunner(BaseWorker())
    window._track_init_task(task)
    finished_spy = QSignalSpy(task.worker.finished)
    task.worker.finished.emit()

    assert finished_spy.count() == 1
    assert task in window._init_tasks

    task.thread.finished.emit()
    assert task not in window._init_tasks


def test_registry_removes_only_the_thread_that_finished(qtbot, monkeypatch):
    window = _make_main_window(qtbot, monkeypatch)
    first = TaskRunner(BaseWorker())
    second = TaskRunner(BaseWorker())
    window._track_init_task(first)
    window._track_init_task(second)

    first.thread.finished.emit()
    assert first not in window._init_tasks
    assert second in window._init_tasks

    second.thread.finished.emit()
    assert not window._init_tasks


def test_hardware_initialization_tasks_are_registered_before_start(qtbot, monkeypatch):
    config = _config("simulation").model_copy(
        update={
            "cameras": {
                identifier: CameraConfig(
                    identifier=identifier,
                    enabled=True,
                    name=identifier,
                    backend="simulation",
                    simulation_width=8,
                    simulation_height=8,
                )
                for identifier in ("camera-one", "camera-two")
            }
        }
    )
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)

    started_tasks = []

    def assert_registered_before_start(task):
        assert task in window._init_tasks
        started_tasks.append(task)

    monkeypatch.setattr(TaskRunner, "start", assert_registered_before_start)

    window._init_ct400_lazy()
    window._init_piezos_lazy()
    window._init_cameras_lazy()

    assert len(started_tasks) == 4
    assert window.ct400_task in started_tasks
    assert window.piezo_task in started_tasks
    assert all(task in started_tasks for task in window._init_tasks)
    camera_tasks = [task for task in started_tasks if task not in {window.ct400_task, window.piezo_task}]
    assert len(camera_tasks) == 2
    assert all(task.worker.__class__.__name__ == "CameraInitWorker" for task in camera_tasks)

    for task in list(window._init_tasks):
        task.thread.finished.emit()
    assert not window._init_tasks
