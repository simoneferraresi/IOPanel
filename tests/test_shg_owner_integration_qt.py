"""Simulated Qt ownership assertions for shared CT400/PM5020 leases.

No physical driver, laser, camera or proprietary DLL is loaded by these tests.
"""

from __future__ import annotations

import threading

import pytest

from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020_types import PM5020State
from logic.device_lease_registry import DeviceBusyError, DeviceLeaseRegistry, DeviceResource
from logic.pm5020_monitor import PM5020MonitorWorker
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
