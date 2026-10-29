"""Regression tests for nonblocking Vimba X startup and shutdown ownership."""

from __future__ import annotations

import threading

from PySide6.QtCore import QTimer

from config_model import AppConfig, CameraConfig
from ui import main_window as main_window_module


def _window(qtbot, monkeypatch, system):
    """Build a physical-config GUI without calling any native camera SDK."""
    class FakeVmbSystem:
        @staticmethod
        def get_instance():
            return system

    monkeypatch.setattr(main_window_module, "VIMBA_AVAILABLE", True)
    monkeypatch.setattr(main_window_module, "VmbSystem", FakeVmbSystem)
    monkeypatch.setattr(main_window_module.MainWindow, "_init_ct400_lazy", lambda _self: None)
    config = AppConfig(
        instruments={"ct400_backend": "simulation"},
        cameras={
            "camera-1": CameraConfig(
                identifier="camera-1", enabled=True, name="Physical fixture", backend="vimba"
            )
        },
    )
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)
    window.show()
    return window


def test_vimba_native_startup_does_not_block_gui_and_gates_camera_workers(qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    enter_threads = []
    exit_calls = []
    cameras_started = []

    class BlockingSystem:
        def __enter__(self):
            enter_threads.append(threading.get_ident())
            entered.set()
            assert release.wait(timeout=5), "test startup was not released"
            return self

        def __exit__(self, *_args):
            exit_calls.append(True)

    window = _window(qtbot, monkeypatch, BlockingSystem())
    gui_thread_id = threading.get_ident()
    monkeypatch.setattr(window, "_init_cameras_lazy", lambda: cameras_started.append(True))
    try:
        window._begin_lazy_init()
        qtbot.waitUntil(entered.is_set, timeout=2000)
        assert enter_threads == [enter_threads[0]]
        assert enter_threads[0] != gui_thread_id
        assert window._vimba_init_task is not None
        assert window.vmb_instance is None
        assert not cameras_started

        # QTimer callbacks must keep running throughout the simulated SDK stall.
        callbacks = []
        QTimer.singleShot(20, lambda: callbacks.append("gui responsive"))
        qtbot.waitUntil(lambda: bool(callbacks), timeout=1000)
        assert not cameras_started

        release.set()
        qtbot.waitUntil(lambda: bool(cameras_started), timeout=3000)
        assert window.vmb_instance is not None
        assert window._vimba_init_task is None
        assert window._init_tasks == set()
        assert cameras_started == [True]
        assert not exit_calls
    finally:
        release.set()
        window.close()
    qtbot.waitUntil(lambda: window._shutdown_complete, timeout=4000)
    assert exit_calls == [True]


def test_close_during_vimba_startup_defers_native_teardown_and_never_starts_cameras(qtbot, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    exits = []
    cameras_started = []

    class BlockingSystem:
        def __enter__(self):
            entered.set()
            assert release.wait(timeout=5), "test startup was not released"
            return self

        def __exit__(self, *_args):
            exits.append(True)

    window = _window(qtbot, monkeypatch, BlockingSystem())
    monkeypatch.setattr(window, "_init_cameras_lazy", lambda: cameras_started.append(True))
    try:
        window._begin_lazy_init()
        qtbot.waitUntil(entered.is_set, timeout=2000)
        assert window.close() is False
        assert window._pending_init_close
        assert not window._shutdown_complete
        assert exits == []
        assert not cameras_started
    finally:
        release.set()
    qtbot.waitUntil(lambda: window._shutdown_complete, timeout=5000)
    assert exits == [True]
    assert cameras_started == []
    assert window._vimba_init_task is None
    assert window._init_tasks == set()


def test_vimba_startup_failure_is_nonmodal_and_still_initializes_failure_placeholders(qtbot, monkeypatch):
    cameras_started = []

    class FailingSystem:
        def __enter__(self):
            raise RuntimeError("simulated transport-layer failure")

        def __exit__(self, *_args):
            raise AssertionError("failed context was never entered")

    window = _window(qtbot, monkeypatch, FailingSystem())
    monkeypatch.setattr(window, "_init_cameras_lazy", lambda: cameras_started.append(True))
    window._begin_lazy_init()
    qtbot.waitUntil(lambda: bool(cameras_started), timeout=3000)
    assert window.vmb_instance is None
    assert window._vimba_init_task is None
    assert window._init_tasks == set()
    assert cameras_started == [True]
    assert "Vimba X startup failed" in window.statusBar().currentMessage()
    window.close()
    qtbot.waitUntil(lambda: window._shutdown_complete, timeout=4000)
