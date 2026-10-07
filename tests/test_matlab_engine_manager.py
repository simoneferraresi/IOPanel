from __future__ import annotations

import threading
from types import SimpleNamespace

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
        self.completed.wait()
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
    assert len(calls) == 2
    assert calls == [{"background": True}, {"background": True}]
    futures[1].complete = True
    futures[1].value = FakeEngine()
    qtbot.waitUntil(lambda: manager.state is MatlabEngineState.READY, timeout=1000)
    manager.shutdown()


def test_ready_shutdown_quits_exactly_once(qtbot):
    engine = FakeEngine()
    manager = MatlabEngineManager(available=True, start_engine=lambda **_kwargs: None)
    manager._engine = engine
    manager._set_state(MatlabEngineState.READY)

    manager.shutdown()
    manager.shutdown()

    assert engine.quit_calls == 1
    assert manager.engine is None
    assert manager.state is MatlabEngineState.SHUTTING_DOWN


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
    qtbot.waitUntil(lambda: engine.quit_calls == 1, timeout=1000)
    assert manager.state is MatlabEngineState.SHUTTING_DOWN
    assert manager.engine is None


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
    assert starts == [{"background": True}]
    future.value = FakeEngine()
    future.complete = True
    qtbot.waitUntil(lambda: widget.matlab_engine_manager.state is MatlabEngineState.READY, timeout=1000)
    assert restarted == [request]
