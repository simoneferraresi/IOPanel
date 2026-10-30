"""Regression coverage for deferred MATLAB imports and lifetime."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from logic import matlab_deferred_start
from logic.matlab_engine_manager import MatlabEngineManager, MatlabEngineState


def test_deferred_matlab_import_never_runs_on_caller_thread(monkeypatch):
    gui_thread = threading.get_ident()
    entered = threading.Event()
    release = threading.Event()
    seen: list[tuple[str, int]] = []
    engine = object()

    class NativeFuture:
        def result(self):
            seen.append(("native-result", threading.get_ident()))
            assert release.wait(5), "test failed to release native startup"
            return engine

    def importer():
        seen.append(("import", threading.get_ident()))
        entered.set()
        return SimpleNamespace(start_matlab=lambda **kwargs: NativeFuture())

    monkeypatch.setattr(matlab_deferred_start, "_import_engine_module", importer)
    try:
        result = matlab_deferred_start.start_matlab_with_deferred_import(background=True)
        assert entered.wait(2)
        assert not result.done()
        assert result.cancel() is False
        release.set()
        assert result.result(timeout=3) is engine
        assert all(thread_id != gui_thread for _phase, thread_id in seen)
        assert [phase for phase, _thread_id in seen] == ["import", "native-result"]
    finally:
        release.set()


def test_deferred_matlab_import_failure_is_future_error(monkeypatch):
    def fail_import():
        raise ImportError("simulated MATLAB binding failure")

    monkeypatch.setattr(matlab_deferred_start, "_import_engine_module", fail_import)
    future = matlab_deferred_start.start_matlab_with_deferred_import()
    with pytest.raises(RuntimeError, match="simulated MATLAB binding failure"):
        future.result(timeout=3)


def test_explicit_synchronous_deferred_start_is_rejected():
    with pytest.raises(ValueError, match="background=True"):
        matlab_deferred_start.start_matlab_with_deferred_import(background=False)


def test_close_while_import_or_native_start_is_in_progress_quits_late_engine(monkeypatch, qtbot):
    entered = threading.Event()
    release = threading.Event()
    native_called = threading.Event()
    engine = SimpleNamespace(quit_calls=0)

    def quit_engine():
        engine.quit_calls += 1

    engine.quit = quit_engine

    class NativeFuture:
        def result(self):
            native_called.set()
            assert release.wait(5), "test failed to release native startup"
            return engine

    def importer():
        entered.set()
        return SimpleNamespace(start_matlab=lambda **kwargs: NativeFuture())

    monkeypatch.setattr(matlab_deferred_start, "_import_engine_module", importer)
    manager = MatlabEngineManager(available=True, start_engine=matlab_deferred_start.start_matlab_with_deferred_import)
    try:
        manager.prewarm()
        assert manager.state is MatlabEngineState.STARTING
        assert entered.wait(2)
        assert native_called.wait(2)
        manager.shutdown()
        assert manager.state is MatlabEngineState.SHUTTING_DOWN
        assert not manager.shutdown_finished
        release.set()
        qtbot.waitUntil(lambda: manager.shutdown_finished, timeout=4000)
        assert engine.quit_calls == 1
        assert manager.engine is None
    finally:
        release.set()
