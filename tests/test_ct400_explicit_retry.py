from config_model import AppConfig
from hardware import ct400_init_worker
from hardware.ct400_init_worker import CT400InitOutcome
from hardware.dummy_ct400 import DummyCT400
from logic.task_runner import TaskRunner
from ui import main_window as main_window_module


def _window(qtbot, monkeypatch, backend="physical"):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _window: None)
    window = main_window_module.MainWindow(AppConfig(instruments={"ct400_backend": backend}))
    qtbot.addWidget(window)
    return window


def test_explicit_retry_starts_only_ct400_and_suppresses_reentry(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    tasks = []
    monkeypatch.setattr(TaskRunner, "start", lambda task: tasks.append(task))
    piezo_calls = []
    camera_calls = []
    monkeypatch.setattr(window, "_init_piezos_lazy", lambda: piezo_calls.append(True))
    monkeypatch.setattr(window, "_init_cameras_lazy", lambda: camera_calls.append(True))

    window.ct400_retry_action.trigger()
    first_task = window.ct400_task
    window.ct400_retry_action.trigger()

    assert len(tasks) == 1
    assert first_task is tasks[0]
    assert piezo_calls == []
    assert camera_calls == []
    assert not window.ct400_retry_action.isEnabled()

    outcome = CT400InitOutcome(DummyCT400(), "UNAVAILABLE", "Mock failure.")
    first_task.worker.initialization_completed.emit(outcome)
    first_task.worker_thread.finished.emit()
    assert window._active_ct400_init_attempt_id is None
    assert window.ct400_status_label.text() == "CT400: Unavailable"


def test_retry_rejected_while_old_ct400_state_is_unresolved(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    started = []
    monkeypatch.setattr(window, "_init_ct400_lazy", lambda: started.append(True))
    window._ct400_cleanup_laser_input = object()

    window._retry_ct400_initialization()

    assert started == []


def test_stale_ct400_attempt_status_and_result_are_ignored(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window._active_ct400_init_attempt_id = 2
    previous_device = DummyCT400()
    window.ct400_device = previous_device
    old_text = window.ct400_status_label.text()

    window._on_ct400_attempt_status_updated(1, "UNAVAILABLE", "stale failure")
    window._on_ct400_init_task_finished(1, object(), CT400InitOutcome(DummyCT400(), "UNAVAILABLE", "stale"))

    assert window.ct400_device is previous_device
    assert window.ct400_status_label.text() == old_text


def test_cancelled_initialization_without_device_releases_retry(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    tasks = []

    def cancel_before_worker_run(task):
        task.worker.stop()
        task.worker.run()
        tasks.append(task)

    monkeypatch.setattr(TaskRunner, "start", cancel_before_worker_run)

    window._retry_ct400_initialization()

    task = tasks[0]
    assert task.worker.terminal_outcome is not None
    assert task.worker.terminal_outcome.cancelled
    assert task.worker.terminal_outcome.device is None
    assert window.ct400_device is None

    task.worker_thread.finished.emit()

    assert window._active_ct400_init_attempt_id is None
    assert window.ct400_status_label.text() == "CT400: Unavailable"
    assert window.ct400_retry_action.isEnabled()


def test_ct400_terminal_outcome_is_transferred_at_thread_boundary(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    tasks = []
    monkeypatch.setattr(TaskRunner, "start", lambda task: tasks.append(task))
    window._retry_ct400_initialization()
    task = tasks[0]
    attempt_id = window._active_ct400_init_attempt_id
    outcome = CT400InitOutcome(DummyCT400(), "UNAVAILABLE", "mock terminal failure")

    task.worker.initialization_completed.emit(outcome)

    assert window.ct400_device is None
    assert window._active_ct400_init_attempt_id == attempt_id

    task.worker_thread.finished.emit()

    assert window.ct400_device is outcome.device
    assert window._active_ct400_init_attempt_id is None
    assert window.ct400_status_label.text() == "CT400: Unavailable"


def test_stale_physical_candidate_is_closed_once_but_current_device_is_retained(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    monkeypatch.setattr(main_window_module, "CT400", FakeCT400)
    window._active_ct400_init_attempt_id = 2
    current = FakeCT400()
    stale = FakeCT400()
    window.ct400_device = current

    window._on_ct400_init_task_finished(1, object(), CT400InitOutcome(current, "DISCONNECTED", "stale"))
    window._on_ct400_init_task_finished(1, object(), CT400InitOutcome(stale, "DISCONNECTED", "stale"))
    window._on_ct400_init_task_finished(1, object(), CT400InitOutcome(stale, "DISCONNECTED", "duplicate"))

    assert current.close_calls == 0
    assert stale.close_calls == 1
    assert window.ct400_device is current


class FakeCT400:
    def __init__(self):
        self.close_calls = 0
        self.handle = None

    def close(self):
        self.close_calls += 1


def test_physical_failure_dummy_stays_unavailable_and_cannot_enable_operations(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)

    window._on_ct400_status_updated("UNAVAILABLE", "DLL architecture mismatch")
    window._on_ct400_initialized(DummyCT400())

    assert window.ct400_status_label.text() == "CT400: Unavailable"
    assert window.control_panel.is_simulated is False
    assert window.histogram_control.is_simulated is False
    assert window.control_panel.is_instrument_connected is False
    assert window.histogram_control.is_instrument_connected is False
    assert window.ct400_retry_action.isEnabled()


def test_failed_initialization_can_be_retried_to_disconnected_ready(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch)
    dll_path = tmp_path / "mock-ct400.dll"
    dll_path.touch()
    attempts = iter((None, dll_path))
    monkeypatch.setattr(ct400_init_worker.CT400InitWorker, "_find_dll", lambda _worker: next(attempts))
    calls = []

    class FakeCT400:
        def __init__(self, _path):
            calls.append("initialize")
            self.handle = 1

        def close(self):
            calls.append("close")

    monkeypatch.setattr(ct400_init_worker, "CT400", FakeCT400)
    monkeypatch.setattr(main_window_module, "CT400", FakeCT400)

    window._retry_ct400_initialization()
    qtbot.waitUntil(
        lambda: (
            window.ct400_task is None
            and isinstance(window.ct400_device, DummyCT400)
            and window.ct400_status_label.text() == "CT400: Unavailable"
        ),
        timeout=3000,
    )
    assert isinstance(window.ct400_device, DummyCT400)
    assert window.ct400_status_label.text() == "CT400: Unavailable"
    assert window.ct400_retry_action.isEnabled()

    window.ct400_retry_action.trigger()
    qtbot.waitUntil(
        lambda: window.ct400_task is None and window.ct400_status_label.text() == "CT400: Ready (Disconnected)",
        timeout=3000,
    )

    assert calls == ["initialize"]
    assert not window.is_ct400_connected_state
    assert not window.control_panel.is_instrument_connected
    assert not window.histogram_control.is_instrument_connected

    window.close()
    assert calls == ["initialize", "close"]
