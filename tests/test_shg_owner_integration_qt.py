"""Simulated Qt ownership assertions for shared CT400/PM5020 leases.

No physical driver, laser, camera or proprietary DLL is loaded by these tests.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
import shiboken6
from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy

from app_settings import AppSettings
from config_model import AppConfig
from hardware.dummy_ct400 import DummyCT400
from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020_types import PM5020State
from logic.device_lease_registry import DeviceBusyError, DeviceLeaseRegistry, DeviceResource
from logic.pm5020_monitor import PM5020MonitorWorker
from ui import main_window as main_window_module
from ui import plot_widgets
from ui.pm5020_panel import PM5020Panel


def _owner(registry: DeviceLeaseRegistry, resource: DeviceResource) -> str | None:
    return next(item.owner for item in registry.snapshot() if item.resource is resource)


def test_pm_owner_keeps_lease_during_monitor_pause_and_disconnect(qtbot):
    registry = DeviceLeaseRegistry()
    thread_ids = []

    def simulator_factory():
        thread_ids.append(threading.get_ident())
        return DummyPM5020()

    panel = PM5020Panel(
        worker=PM5020MonitorWorker(simulator_factory=simulator_factory),
        lease_registry=registry,
    )
    qtbot.addWidget(panel)
    try:
        panel.request_connect()
        qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=3500)
        qtbot.waitUntil(lambda: panel._configuration_applied, timeout=3000)
        assert thread_ids and thread_ids[0] != threading.get_ident()
        assert _owner(registry, DeviceResource.PM5020) == "pm5020-owner-worker"
        with pytest.raises(DeviceBusyError):
            registry.acquire("shg", (DeviceResource.CT400, DeviceResource.PM5020))

        panel._start()
        qtbot.waitUntil(lambda: panel._monitor_session, timeout=2000)
        assert _owner(registry, DeviceResource.PM5020) == "pm5020-owner-worker"

        panel.pause_resume_button.click()
        qtbot.waitUntil(lambda: panel._paused, timeout=2000)
        assert _owner(registry, DeviceResource.PM5020) == "pm5020-owner-worker"
        with pytest.raises(DeviceBusyError):
            registry.acquire("shg", (DeviceResource.CT400, DeviceResource.PM5020))

        panel.stop_requested.emit()
        qtbot.waitUntil(lambda: not panel._monitor_session, timeout=3000)
        panel.request_disconnect()
        qtbot.waitUntil(lambda: panel._backend_state is PM5020State.DISCONNECTED, timeout=3000)
        qtbot.waitUntil(lambda: _owner(registry, DeviceResource.PM5020) is None, timeout=3000)
        lease = registry.acquire("shg", (DeviceResource.CT400, DeviceResource.PM5020))
        registry.release(lease, operations_complete=True)
    finally:
        panel.request_shutdown()
        qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=5000)


def test_existing_pm_lease_denies_connect_without_constructing_backend(qtbot):
    registry = DeviceLeaseRegistry()
    competing = registry.acquire("active-sync", (DeviceResource.CT400, DeviceResource.PM5020))
    created = []

    def simulator_factory():
        created.append(True)
        return DummyPM5020()

    panel = PM5020Panel(
        worker=PM5020MonitorWorker(simulator_factory=simulator_factory),
        lease_registry=registry,
    )
    qtbot.addWidget(panel)
    try:
        panel.request_connect()
        qtbot.waitUntil(lambda: not panel._connection_request_pending, timeout=3000)
        assert not created
        assert panel._backend_state is PM5020State.DISCONNECTED
        assert _owner(registry, DeviceResource.PM5020) == "active-sync"
        registry.release(competing, operations_complete=True)
        panel.request_connect()
        qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=3000)
        assert created
    finally:
        panel.request_shutdown()
        qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=5000)


def test_failed_pm_close_quarantines_lease_instead_of_freeing_for_sync(qtbot):
    from logic.device_lease_registry import LeaseState

    class FailingClose(DummyPM5020):
        def close(self):
            raise RuntimeError("simulated close failure")

    registry = DeviceLeaseRegistry()
    panel = PM5020Panel(
        worker=PM5020MonitorWorker(simulator_factory=FailingClose),
        lease_registry=registry,
    )
    qtbot.addWidget(panel)
    try:
        panel.request_connect()
        qtbot.waitUntil(lambda: panel._backend_state is PM5020State.IDLE, timeout=3500)
        panel.request_disconnect()
        qtbot.waitUntil(
            lambda: registry.snapshot()[1].state is LeaseState.UNCERTAIN,
            timeout=3000,
        )
        assert _owner(registry, DeviceResource.PM5020) == "pm5020-owner-worker"
        with pytest.raises(DeviceBusyError):
            registry.acquire("shg", (DeviceResource.CT400, DeviceResource.PM5020))
    finally:
        panel.request_shutdown()
        qtbot.waitUntil(lambda: panel.shutdown_finished, timeout=5000)
    assert registry.snapshot()[1].state is LeaseState.UNCERTAIN


def test_close_during_pending_pm_handoff_keeps_scan_and_alignment_unopened(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(main_window_module.MainWindow, "_begin_lazy_init", lambda _self: None)
    monkeypatch.setattr(plot_widgets, "MATLAB_ENGINE_AVAILABLE", False)
    config = AppConfig(
        instruments={
            "ct400_backend": "simulation",
            "pm5020_backend": "simulation",
            "edfa_backend": "simulation",
        },
        cameras={},
    )
    settings = AppSettings(QSettings(str(tmp_path / "shg-close-no-lazy-views.ini"), QSettings.Format.IniFormat))
    window = main_window_module.MainWindow(config, settings=settings)
    qtbot.addWidget(window)
    window._on_ct400_initialized(DummyCT400(scan_duration=0))
    window.show()
    shg = window.shg_tab
    pm = window._pm5020_panel
    assert pm is not None and pm.backend_mode == "simulation"
    worker = pm.worker
    native_thread = pm.task.worker_thread
    finished = QSignalSpy(shg.finished)
    quiesced = QSignalSpy(worker.synchronized_quiesced)
    cleaned = QSignalSpy(worker.synchronized_cleaned)
    worker.synchronized_quiesced.disconnect(shg._on_pm_quiesced)
    worker.synchronized_cleaned.disconnect(shg._on_pm_cleaned)

    def assert_views_unopened():
        assert window._control_panel is None
        assert window._plot_widget is None
        assert window._alignment_tab is None

    try:
        assert_views_unopened()
        shg.start_capture(destination_root=tmp_path / "cancelled-shg-close")
        assert shg.active
        assert shg._request is None
        assert shg._ct_worker is None
        assert not shg._pm_quiesced
        assert_views_unopened()

        assert not window.close()
        assert window._shutdown_requested
        assert shg._cancelled
        assert not window._shutdown_complete
        window._on_lazy_tab_selected(1)
        window._on_lazy_tab_selected(2)
        window.tab_widget.setCurrentIndex(1)
        window.tab_widget.setCurrentIndex(2)
        assert_views_unopened()

        qtbot.waitUntil(lambda: quiesced.count() == 1, timeout=5000)
        token, generation, ok, reason = quiesced.at(0)
        assert ok, reason
        shg._on_pm_quiesced(token, generation, ok, reason)
        assert shg._pm_quiesced
        assert shg._pm_cleanup_requested
        assert shg._request is None
        assert shg._ct_worker is None
        assert_views_unopened()

        qtbot.waitUntil(lambda: cleaned.count() == 1, timeout=5000)
        cleanup_token, cleanup_generation, cleanup_ok, cleanup_reason = cleaned.at(0)
        assert cleanup_ok, cleanup_reason
        shg._on_pm_cleaned(cleanup_token, cleanup_generation, cleanup_ok, cleanup_reason)
        assert not shg.active
        assert shg._token is None
        assert shg._request is None
        assert finished.count() == 1
        assert window.synchronized_owner_leases._request is None
        assert all(item.owner is None for item in window.device_lease_registry.snapshot())
        assert worker._sync_token is None
        assert worker._sync_lease is None
        assert worker._connection_lease is None
        assert worker._backend is None
        assert not any((tmp_path / "cancelled-shg-close").rglob("*"))
        assert_views_unopened()

        qtbot.waitUntil(lambda: window._shutdown_complete, timeout=5000)
        qtbot.waitUntil(lambda: not shiboken6.isValid(native_thread) or not native_thread.isRunning(), timeout=5000)
        assert pm.shutdown_finished
        assert window.close()
    finally:
        if shg.active:
            # Keep the pre-fix regression run bounded while leaving its failure
            # visible: only the test cleanup supplies inert UI projections.
            sink = SimpleNamespace(set_ct400_operation_state=lambda _state: None)
            with monkeypatch.context() as recovery:
                recovery.setattr(main_window_module.MainWindow, "control_panel", property(lambda _self: sink))
                recovery.setattr(main_window_module.MainWindow, "alignment_tab", property(lambda _self: sink))
                window.close()
                if not quiesced.count():
                    qtbot.waitUntil(lambda: quiesced.count() == 1, timeout=5000)
                if not shg._pm_quiesced:
                    shg._on_pm_quiesced(*quiesced.at(0))
                if shg.active:
                    qtbot.waitUntil(lambda: cleaned.count() == 1, timeout=5000)
                    if shg._pm_ack:
                        shg._finalize()
                    else:
                        shg._on_pm_cleaned(*cleaned.at(0))
                qtbot.waitUntil(lambda: not shg.active, timeout=5000)
                qtbot.waitUntil(lambda: window._shutdown_complete, timeout=5000)
                qtbot.waitUntil(
                    lambda: not shiboken6.isValid(native_thread) or not native_thread.isRunning(), timeout=5000
                )
