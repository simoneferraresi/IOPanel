from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtTest import QSignalSpy

from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState
from ui import plot_widgets
from ui.control_panel import ScanSettings
from ui.plot_widgets import PlotWidget


class FakeFuture:
    def __init__(self, *, cancel_result: bool = False) -> None:
        self.complete = False
        self.value = None
        self.error: Exception | None = None
        self.cancel_result = cancel_result
        self.done_calls = 0
        self.result_calls = 0
        self.cancel_calls = 0

    def done(self) -> bool:
        self.done_calls += 1
        return self.complete

    def result(self):
        self.result_calls += 1
        if not self.complete:
            raise AssertionError("result() was called before done()")
        if self.error is not None:
            raise self.error
        return self.value

    def cancel(self) -> bool:
        self.cancel_calls += 1
        return self.cancel_result


class BlockingFuture(FakeFuture):
    def __init__(self) -> None:
        super().__init__(cancel_result=False)
        self.completed = threading.Event()
        self.value = None

    def done(self) -> bool:
        self.done_calls += 1
        return self.completed.is_set()

    def result(self):
        self.result_calls += 1
        assert self.completed.wait(5), "fake startup was not released"
        if self.error is not None:
            raise self.error
        return self.value


class ControlledFuture(FakeFuture):
    def __init__(self, *, cancel_result: bool, block_cancel: bool = False, block_result: bool = False) -> None:
        super().__init__(cancel_result=cancel_result)
        self.block_cancel = block_cancel
        self.block_result = block_result
        self.cancel_started = threading.Event()
        self.release_cancel = threading.Event()
        self.result_started = threading.Event()
        self.release_result = threading.Event()
        if not block_cancel:
            self.release_cancel.set()
        if not block_result:
            self.release_result.set()

    def cancel(self) -> bool:
        self.cancel_calls += 1
        self.cancel_started.set()
        assert self.release_cancel.wait(5), "fake cancellation was not released"
        return self.cancel_result

    def result(self):
        self.result_calls += 1
        self.result_started.set()
        assert self.release_result.wait(5), "fake result was not released"
        if self.error is not None:
            raise self.error
        return self.value


class FakeEngine:
    def __init__(self) -> None:
        self.eval_calls = 0
        self.quit_calls = 0

    def eval(self, *_args, **_kwargs):
        self.eval_calls += 1

    def quit(self):
        self.quit_calls += 1


class FinishedWorker(QObject):
    finished_saving = Signal(str, bool, str)
    engine_unhealthy = Signal(str)

    @Slot()
    def run(self) -> None:
        self.finished_saving.emit("fig", True, "scan.fig")


def test_unavailable_manager_has_no_timers_or_start_attempts(qtbot):
    starts = []
    manager = MatlabEngineManager(available=False, start_engine=lambda **kwargs: starts.append(kwargs))
    manager.schedule_prewarm()
    manager.prewarm()
    manager.request_engine()

    assert manager.state is MatlabEngineState.UNAVAILABLE
    assert manager._prewarm_timer is None
    assert manager._poll_timer is None
    assert starts == []


def test_prewarm_is_deferred_background_and_polled_without_early_result(qtbot):
    future = FakeFuture()
    engine = FakeEngine()
    calls = []
    manager = MatlabEngineManager(
        available=True,
        start_engine=lambda **kwargs: calls.append(kwargs) or future,
        prewarm_delay_ms=15,
        poll_interval_ms=5,
    )
    ready = QSignalSpy(manager.ready)

    manager.schedule_prewarm()
    assert calls == []
    assert manager.state is MatlabEngineState.IDLE
    qtbot.waitUntil(lambda: bool(calls), timeout=1000)
    assert calls == [{"background": True}]
    assert manager.state is MatlabEngineState.STARTING
    future.value = engine
    qtbot.wait(30)
    assert future.result_calls == 0
    assert manager.state is MatlabEngineState.STARTING

    future.complete = True
    qtbot.waitUntil(lambda: manager.state is MatlabEngineState.READY, timeout=1000)
    assert future.result_calls == 1
    assert manager.engine is engine
    assert ready.count() == 1


def test_repeated_prewarm_and_request_share_one_start(qtbot):
    future = FakeFuture()
    engine = FakeEngine()
    calls = []
    manager = MatlabEngineManager(
        available=True,
        start_engine=lambda **kwargs: calls.append(kwargs) or future,
        prewarm_delay_ms=0,
        poll_interval_ms=5,
    )

    manager.prewarm()
    manager.prewarm()
    manager.request_engine()
    assert calls == [{"background": True}]
    future.value = engine
    future.complete = True
    qtbot.waitUntil(lambda: manager.state is MatlabEngineState.READY, timeout=1000)
    manager.prewarm()
    manager.request_engine()
    assert calls == [{"background": True}]


def test_start_failure_is_non_modal_and_only_retries_on_explicit_request(qtbot):
    futures = [FakeFuture(), FakeFuture()]
    futures[0].complete = True
    futures[0].error = RuntimeError("startup failed")
    calls = []

    def start_engine(**kwargs):
        calls.append(kwargs)
        return futures[len(calls) - 1]

    manager = MatlabEngineManager(available=True, start_engine=start_engine, poll_interval_ms=5)
    failures = QSignalSpy(manager.startup_failed)
    manager.prewarm()
    qtbot.waitUntil(lambda: manager.state is MatlabEngineState.FAILED, timeout=1000)
    assert manager.engine is None
    assert failures.count() == 1
    manager.prewarm()
    assert len(calls) == 1

    manager.request_engine()
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=1000)
    assert len(calls) == 2
    assert calls == [{"background": True}, {"background": True}]
    futures[1].complete = True
    futures[1].value = FakeEngine()
    qtbot.waitUntil(lambda: manager.state is MatlabEngineState.READY, timeout=1000)
    manager.shutdown()


@pytest.mark.parametrize("fails", [False, True])
def test_completed_startup_future_is_released_off_gui_thread(qtbot, fails):
    destroyed = threading.Event()
    destruction_threads: list[int] = []
    engine = FakeEngine()

    class FinalizingFuture(FakeFuture):
        def result(self):
            if fails:
                raise RuntimeError("startup failed")
            return engine

        def __del__(self):
            destruction_threads.append(threading.get_ident())
            destroyed.set()

    def start_engine(**_kwargs):
        future = FinalizingFuture()
        future.complete = True
        return future

    gui_thread = threading.get_ident()
    manager = MatlabEngineManager(available=True, start_engine=start_engine, poll_interval_ms=5)
    manager.prewarm()

    expected_state = MatlabEngineState.FAILED if fails else MatlabEngineState.READY
    qtbot.waitUntil(lambda: manager.state is expected_state, timeout=1000)
    qtbot.waitUntil(destroyed.is_set, timeout=1000)
    qtbot.waitUntil(lambda: not manager._disposals, timeout=1000)

    assert destruction_threads
    assert destruction_threads[0] != gui_thread
    assert (manager.engine is None) is fails
    manager.shutdown()
    qtbot.waitUntil(lambda: manager.shutdown_finished, timeout=1000)
    assert engine.quit_calls == (0 if fails else 1)


def test_shutdown_waits_for_completed_future_native_release_before_engine_quit(qtbot):
    release_started = threading.Event()
    release_future = threading.Event()
    engine = FakeEngine()

    class BlockingFinalizerFuture(FakeFuture):
        def result(self):
            return engine

        def __del__(self):
            release_started.set()
            assert release_future.wait(5), "test did not release fake future destructor"

    def start_engine(**_kwargs):
        future = BlockingFinalizerFuture()
        future.complete = True
        return future

    manager = MatlabEngineManager(available=True, start_engine=start_engine, poll_interval_ms=5)
    manager.prewarm()
    qtbot.waitUntil(lambda: manager.state is MatlabEngineState.READY, timeout=1000)
    qtbot.waitUntil(release_started.is_set, timeout=1000)

    manager.shutdown()
    assert engine.quit_calls == 0
    assert not manager.shutdown_finished

    release_future.set()
    qtbot.waitUntil(lambda: manager.shutdown_finished, timeout=1000)
    assert engine.quit_calls == 1


def test_ready_shutdown_quits_exactly_once(qtbot):
    engine = FakeEngine()
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: None)
    manager._engine = engine
    manager._set_state(MatlabEngineState.READY)

    manager.shutdown()
    manager.shutdown()

    qtbot.waitUntil(lambda: manager.shutdown_finished)
    assert engine.quit_calls == 1
    assert manager.engine is None
    assert manager.state is MatlabEngineState.SHUT_DOWN


def test_plotwidget_clears_finished_thread_refs_and_starts_later_fig_thread(qtbot, monkeypatch):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    thread = QThread(widget)
    worker = FinishedWorker()
    widget.matlab_save_thread = thread
    widget.matlab_save_worker = worker
    worker.moveToThread(thread)
    worker.finished_saving.connect(worker.deleteLater)
    worker.finished_saving.connect(thread.quit)
    thread.started.connect(worker.run)
    thread.finished.connect(
        lambda completed_thread=thread, completed_worker=worker: widget._matlab_save_thread_finished.emit(
            completed_thread, completed_worker
        )
    )
    thread.finished.connect(thread.deleteLater)
    thread.start()

    qtbot.waitUntil(lambda: widget.matlab_save_thread is None, timeout=1000)
    assert widget.matlab_save_worker is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not shiboken6.isValid(thread)
    assert not shiboken6.isValid(worker)

    def complete_worker(target, *_args):
        QTimer.singleShot(10, lambda: target.finished_saving.emit("fig", True, "scan.fig"))
        return True

    monkeypatch.setattr(plot_widgets.QMetaObject, "invokeMethod", complete_worker)
    monkeypatch.setattr(plot_widgets.QMessageBox, "information", lambda *_args: None)
    widget.matlab_engine_manager._engine = FakeEngine()
    widget.matlab_engine_manager._set_state(MatlabEngineState.READY)
    widget.pending_saves = 1
    widget.saved_files_list = []
    widget.error_list = []
    widget._completion_reported = False
    widget._pending_matlab_fig = ("payload", "first.fig", "title", "x", "y")
    widget._start_pending_matlab_fig()
    first_fig_thread = widget.matlab_save_thread

    qtbot.waitUntil(lambda: widget.matlab_save_thread is None, timeout=1000)
    assert widget.matlab_save_worker is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not shiboken6.isValid(first_fig_thread)

    widget.pending_saves = 1
    widget.saved_files_list = []
    widget.error_list = []
    widget._completion_reported = False
    widget._pending_matlab_fig = ("payload", "second.fig", "title", "x", "y")
    widget._start_pending_matlab_fig()
    second_fig_thread = widget.matlab_save_thread
    assert second_fig_thread is not first_fig_thread
    qtbot.waitUntil(lambda: widget.matlab_save_thread is None, timeout=1000)
    assert widget.matlab_save_worker is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not shiboken6.isValid(second_fig_thread)


def test_plotwidget_cleanup_tolerates_deleted_qthread_and_always_shuts_manager(qtbot):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    thread = QThread(widget)
    worker = QObject()
    thread.start()
    thread.quit()
    assert thread.wait(1000)
    thread.deleteLater()
    worker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not shiboken6.isValid(thread)
    assert not shiboken6.isValid(worker)
    widget.matlab_save_thread = thread
    widget.matlab_save_worker = worker
    shutdown_calls = []
    original_shutdown = widget.matlab_engine_manager.shutdown

    def shutdown():
        shutdown_calls.append(True)
        original_shutdown()

    widget.matlab_engine_manager.shutdown = shutdown

    widget.cleanup()
    widget.cleanup()

    assert shutdown_calls == [True]
    assert widget.matlab_save_thread is None
    assert widget.matlab_save_worker is None


def test_plotwidget_cleanup_retains_active_thread_until_retirement(qtbot):
    from logic.qt_worker_lifecycle import retire_worker_on_thread_finish

    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    thread = QThread(widget)
    worker = QObject()
    worker.moveToThread(thread)
    retire_worker_on_thread_finish(thread, worker)
    thread.finished.connect(lambda: widget._matlab_save_thread_finished.emit(thread, worker))
    widget.matlab_save_thread = thread
    widget.matlab_save_worker = worker
    thread.start()
    try:
        widget.cleanup()
        assert widget.matlab_save_thread is thread
        assert not widget.shutdown_finished
        assert not widget.close()
    finally:
        thread.quit()
        qtbot.waitUntil(lambda: widget.shutdown_finished)


def test_matlab_status_persists_for_long_startup_states(qtbot, monkeypatch):
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget._status_clear_timer.start(1000)

    widget._on_matlab_engine_state_changed(MatlabEngineState.STARTING)
    assert widget.matlab_status_label.text() == "MATLAB Starting…"
    assert not widget._status_clear_timer.isActive()

    widget.matlab_engine_manager._available = True
    monkeypatch.setattr(widget.matlab_engine_manager, "request_engine", lambda: MatlabEngineState.STARTING)
    widget.pending_saves = 0
    widget.saved_files_list = []
    widget.error_list = []
    widget._queue_matlab_fig(("payload", "scan.fig", "title", "x", "y"))
    assert widget.matlab_status_label.text() == "Waiting for MATLAB…"
    assert not widget._status_clear_timer.isActive()

    widget._set_matlab_status("Restarting MATLAB…", persistent=True)
    assert not widget._status_clear_timer.isActive()

    widget._pending_matlab_fig = None
    widget._on_matlab_engine_ready()
    assert widget.matlab_status_label.text() == "MATLAB Ready"
    assert widget._status_clear_timer.isActive()

    widget._pending_matlab_fig = None
    widget._on_matlab_engine_startup_failed("startup failed")
    assert widget.matlab_status_label.text() == "MATLAB Start Failed"
    assert widget._status_clear_timer.isActive()


def test_startup_shutdown_disposes_late_engine_off_the_qt_thread(qtbot):
    future = BlockingFuture()
    engine = FakeEngine()
    manager = MatlabEngineManager(
        available=True,
        start_engine=lambda **_kwargs: future,
        poll_interval_ms=5,
    )
    manager.prewarm()
    manager.shutdown()
    assert future.cancel_calls == 1
    assert not manager.polling

    future.value = engine
    future.completed.set()
    qtbot.waitUntil(lambda: manager.shutdown_finished, timeout=1000)
    assert engine.quit_calls == 1
    assert manager.state is MatlabEngineState.SHUT_DOWN
    assert manager.engine is None


def test_starting_shutdown_does_not_wait_for_blocking_cancel(qtbot, caplog):
    caplog.set_level(logging.INFO, logger="LabApp.matlab_engine")
    future = ControlledFuture(cancel_result=True, block_cancel=True)
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: future)
    manager.prewarm()

    started = time.perf_counter()
    manager.shutdown()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2
    assert future.cancel_started.wait(1)
    assert future.cancel_calls == 1
    assert manager._startup_disposal_thread is not None
    assert not manager._startup_disposal_thread.daemon
    assert manager.state is MatlabEngineState.SHUTTING_DOWN
    future.release_cancel.set()
    manager._startup_disposal_thread.join(timeout=1)
    assert not manager._startup_disposal_thread.is_alive()
    assert future.result_calls == 0
    assert "MATLAB startup shutdown requested." in caplog.text
    assert "MATLAB startup cancellation returned True after" in caplog.text


def test_starting_shutdown_does_not_wait_for_blocking_result_and_disposes_engine_once(qtbot, caplog):
    caplog.set_level(logging.INFO, logger="LabApp.matlab_engine")
    future = ControlledFuture(cancel_result=False, block_result=True)
    engine = FakeEngine()
    future.value = engine
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: future)
    manager.prewarm()

    started = time.perf_counter()
    manager.shutdown()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.2
    assert future.result_started.wait(1)
    disposal_thread = manager._startup_disposal_thread
    manager.shutdown()
    assert manager._startup_disposal_thread is disposal_thread
    assert future.cancel_calls == 1
    assert future.result_calls == 1
    assert engine.quit_calls == 0

    future.release_result.set()
    disposal_thread.join(timeout=1)
    assert not disposal_thread.is_alive()
    qtbot.waitUntil(lambda: manager.shutdown_finished)
    assert engine.quit_calls == 1
    assert manager.state is MatlabEngineState.SHUT_DOWN
    assert "Waiting for in-progress MATLAB startup to finish before disposal." in caplog.text
    assert "Late MATLAB startup disposed after" in caplog.text


def test_startup_shutdown_cancellation_success_skips_result(qtbot):
    future = ControlledFuture(cancel_result=True)
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: future)
    manager.prewarm()

    manager.shutdown()
    disposal_thread = manager._startup_disposal_thread
    assert disposal_thread is not None
    disposal_thread.join(timeout=1)

    assert not disposal_thread.is_alive()
    assert future.cancel_calls == 1
    assert future.result_calls == 0
    assert manager.state is MatlabEngineState.SHUTTING_DOWN


def test_startup_shutdown_future_exception_is_contained(qtbot, caplog):
    caplog.set_level(logging.INFO, logger="LabApp.matlab_engine")
    future = ControlledFuture(cancel_result=False)
    future.error = RuntimeError("startup failed during shutdown")
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: future)
    manager.prewarm()

    manager.shutdown()
    disposal_thread = manager._startup_disposal_thread
    assert disposal_thread is not None
    disposal_thread.join(timeout=1)

    assert not disposal_thread.is_alive()
    assert future.cancel_calls == 1
    assert future.result_calls == 1
    assert manager.state is MatlabEngineState.SHUTTING_DOWN
    assert "MATLAB startup disposal completed with exception" in caplog.text


def test_starting_disposal_never_emits_qt_callbacks_after_owner_destroyed(qtbot, monkeypatch):
    future = ControlledFuture(cancel_result=False, block_result=True)
    engine = FakeEngine()
    future.value = engine
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(engine=SimpleNamespace(start_matlab=lambda **_kwargs: future)),
        raising=False,
    )
    widget = PlotWidget(ScanSettings())
    manager = widget.matlab_engine_manager
    manager.prewarm()
    future.result_started.wait(1)
    state_changes = QSignalSpy(manager.state_changed)

    widget.cleanup()
    changes_after_shutdown = state_changes.count()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QCoreApplication.processEvents()
    assert not shiboken6.isValid(widget)
    assert not shiboken6.isValid(manager)

    future.release_result.set()
    manager._startup_disposal_thread.join(timeout=1)
    assert not manager._startup_disposal_thread.is_alive()
    assert engine.quit_calls == 1
    assert state_changes.count() == changes_after_shutdown


def test_plotwidget_queues_fig_while_starting_and_reuses_ready_engine(qtbot, monkeypatch):
    future = FakeFuture()
    engine = FakeEngine()
    starts = []
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(engine=SimpleNamespace(start_matlab=lambda **kwargs: starts.append(kwargs) or future)),
        raising=False,
    )
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.pending_saves = 0
    widget.saved_files_list = []
    widget.error_list = []
    started_saves = []
    monkeypatch.setattr(widget, "_start_pending_matlab_fig", lambda: started_saves.append(widget._pending_matlab_fig))
    request = ("payload", "scan.fig", "title", "x", "y")

    widget._queue_matlab_fig(request)
    assert widget.matlab_engine_manager.state is MatlabEngineState.STARTING
    assert widget.pending_saves == 1
    assert widget._pending_matlab_fig == request
    assert widget.matlab_status_label.text() == "Waiting for MATLAB…"
    assert starts == [{"background": True}]
    future.value = engine
    future.complete = True
    qtbot.waitUntil(lambda: widget.matlab_engine_manager.state is MatlabEngineState.READY, timeout=1000)
    assert started_saves == [request]
    widget._pending_matlab_fig = None
    widget._queue_matlab_fig(("payload-2", "scan2.fig", "title", "x", "y"))
    assert started_saves[-1][1] == "scan2.fig"
    assert starts == [{"background": True}]


def test_failed_prewarm_retries_only_for_fig_and_keeps_request(qtbot, monkeypatch):
    first = FakeFuture()
    first.complete = True
    first.error = RuntimeError("first startup failed")
    retry = FakeFuture()
    engine = FakeEngine()
    futures = [first, retry]
    starts = []

    def start_engine(**kwargs):
        starts.append(kwargs)
        return futures[len(starts) - 1]

    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(engine=SimpleNamespace(start_matlab=start_engine)),
        raising=False,
    )
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.pending_saves = 0
    widget.saved_files_list = []
    widget.error_list = []
    started_saves = []
    monkeypatch.setattr(widget, "_start_pending_matlab_fig", lambda: started_saves.append(widget._pending_matlab_fig))
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    widget.matlab_engine_manager.prewarm()
    qtbot.waitUntil(lambda: widget.matlab_engine_manager.state is MatlabEngineState.FAILED, timeout=1000)
    assert warnings == []
    request = ("payload", "scan.fig", "title", "x", "y")
    widget._queue_matlab_fig(request)
    qtbot.waitUntil(lambda: len(starts) == 2, timeout=1000)
    assert starts == [{"background": True}, {"background": True}]
    assert widget.matlab_engine_manager.state is MatlabEngineState.STARTING
    assert widget._pending_matlab_fig == request
    assert widget.pending_saves == 1

    retry.value = engine
    retry.complete = True
    qtbot.waitUntil(lambda: widget.matlab_engine_manager.state is MatlabEngineState.READY, timeout=1000)
    assert started_saves == [request]
    assert warnings == []


def test_failed_explicit_fig_retry_reports_error_and_finishes_pending(qtbot, monkeypatch):
    first = FakeFuture()
    first.complete = True
    first.error = RuntimeError("background startup failed")
    retry = FakeFuture()
    retry.complete = True
    retry.error = RuntimeError("retry failed")
    futures = [first, retry]
    starts = []

    def start_engine(**kwargs):
        starts.append(kwargs)
        return futures[len(starts) - 1]

    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(engine=SimpleNamespace(start_matlab=start_engine)),
        raising=False,
    )
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    warnings = []
    monkeypatch.setattr(plot_widgets.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))

    widget.matlab_engine_manager.prewarm()
    qtbot.waitUntil(lambda: widget.matlab_engine_manager.state is MatlabEngineState.FAILED, timeout=1000)
    widget.pending_saves = 0
    widget.saved_files_list = []
    widget.error_list = []
    widget._completion_reported = False
    widget._queue_matlab_fig(("payload", "scan.fig", "title", "x", "y"))
    qtbot.waitUntil(lambda: widget.pending_saves == 0, timeout=1000)

    assert starts == [{"background": True}, {"background": True}]
    assert widget.matlab_engine_manager.state is MatlabEngineState.FAILED
    assert widget._pending_matlab_fig is None
    assert warnings and "retry failed" in warnings[0]


def test_unhealthy_ready_engine_retries_without_losing_fig_request(qtbot, monkeypatch):
    future = FakeFuture()
    engine = FakeEngine()
    starts = []
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", True)
    monkeypatch.setattr(
        plot_widgets,
        "matlab",
        SimpleNamespace(engine=SimpleNamespace(start_matlab=lambda **kwargs: starts.append(kwargs) or future)),
        raising=False,
    )
    widget = PlotWidget(ScanSettings())
    qtbot.addWidget(widget)
    widget.matlab_engine_manager._engine = engine
    widget.matlab_engine_manager._set_state(MatlabEngineState.READY)
    request = ("payload", "scan.fig", "title", "x", "y")
    widget._pending_matlab_fig = request
    widget.pending_saves = 1
    restarted = []
    monkeypatch.setattr(widget, "_start_pending_matlab_fig", lambda: restarted.append(request))

    widget._handle_matlab_engine_unhealthy("unresponsive")

    assert widget.matlab_engine_manager.state is MatlabEngineState.STARTING
    assert widget._pending_matlab_fig == request
    qtbot.waitUntil(lambda: starts == [{"background": True}])
    future.value = FakeEngine()
    future.complete = True
    qtbot.waitUntil(lambda: widget.matlab_engine_manager.state is MatlabEngineState.READY, timeout=1000)
    assert restarted == [request]
