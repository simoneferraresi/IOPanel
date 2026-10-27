"""Simulator-only E2B acknowledgments, no Qt event loop or native instruments."""

from __future__ import annotations

import pytest

from logic.device_lease_registry import (
    DeviceBusyError,
    DeviceLeaseError,
    DeviceLeaseRegistry,
    DeviceRecoveryRequired,
    DeviceResource,
    LeaseState,
)
from logic.synchronized_owner_leases import SynchronizedOwnerLeases


def test_reserve_claims_both_resources_atomically():
    registry = DeviceLeaseRegistry()
    protocol = SynchronizedOwnerLeases(registry)
    blocked = registry.acquire("monitor", (DeviceResource.PM5020,))
    with pytest.raises(DeviceBusyError):
        protocol.reserve(ct400_generation=1, pm5020_generation=1)
    assert registry.snapshot()[0].owner is None
    registry.release(blocked, operations_complete=True)
    request = protocol.reserve(ct400_generation=3, pm5020_generation=4)
    assert all(entry.lease_sequence == request.lease.sequence for entry in registry.snapshot())


def test_arm_must_precede_ct400_start_even_after_ct400_owner_acks():
    protocol = SynchronizedOwnerLeases(DeviceLeaseRegistry())
    request = protocol.reserve(ct400_generation=5, pm5020_generation=6)
    assert protocol.acknowledge_ct400_ready(request, generation=5)
    assert not protocol.may_start_ct400(request)
    with pytest.raises(DeviceLeaseError, match="quiescence"):
        protocol.acknowledge_pm_armed(request, generation=6)
    assert protocol.acknowledge_pm_quiesced(request, generation=6)
    assert protocol.acknowledge_pm_armed(request, generation=6)
    assert protocol.may_start_ct400(request)


def test_pending_cancellation_does_not_release_or_allow_scan_start():
    registry = DeviceLeaseRegistry()
    protocol = SynchronizedOwnerLeases(registry)
    request = protocol.reserve(ct400_generation=5, pm5020_generation=6)
    protocol.acknowledge_ct400_ready(request, generation=5)
    protocol.acknowledge_pm_quiesced(request, generation=6)
    protocol.acknowledge_pm_armed(request, generation=6)
    assert protocol.cancel(request)
    assert not protocol.may_start_ct400(request)
    with pytest.raises(DeviceBusyError):
        registry.acquire("other", (DeviceResource.PM5020,))
    with pytest.raises(DeviceLeaseError, match="Both instrument owners"):
        protocol.release_after_cleanup(request)
    protocol.acknowledge_ct400_complete(request, generation=5)
    protocol.acknowledge_pm_complete(request, generation=6)
    protocol.release_after_cleanup(request)
    assert all(item.owner is None for item in registry.snapshot())


def test_stale_generation_and_request_callbacks_cannot_unlock_devices():
    registry = DeviceLeaseRegistry()
    protocol = SynchronizedOwnerLeases(registry)
    first = protocol.reserve(ct400_generation=5, pm5020_generation=6)
    assert not protocol.acknowledge_pm_quiesced(first, generation=7)
    assert not protocol.acknowledge_ct400_complete(first, generation=4)
    assert not protocol.acknowledge_pm_complete(first, generation=7)
    with pytest.raises(DeviceLeaseError, match="Both"):
        protocol.release_after_cleanup(first)
    protocol.acknowledge_ct400_complete(first, generation=5)
    protocol.acknowledge_pm_complete(first, generation=6)
    protocol.release_after_cleanup(first)
    second = protocol.reserve(ct400_generation=5, pm5020_generation=6)
    assert second.request_id != first.request_id
    assert not protocol.acknowledge_ct400_complete(first, generation=5)
    assert not protocol.cancel(first)
    with pytest.raises(DeviceLeaseError, match="Stale"):
        protocol.release_after_cleanup(first)
    assert protocol.active_request is second


def test_timeout_quarantines_both_until_explicit_operator_recovery():
    registry = DeviceLeaseRegistry()
    protocol = SynchronizedOwnerLeases(registry)
    request = protocol.reserve(ct400_generation=0, pm5020_generation=0)
    protocol.quarantine(request, "owner callback timed out")
    assert all(snapshot.state is LeaseState.UNCERTAIN for snapshot in registry.snapshot())
    protocol.acknowledge_ct400_complete(request, generation=0)
    protocol.acknowledge_pm_complete(request, generation=0)
    with pytest.raises(DeviceRecoveryRequired):
        protocol.release_after_cleanup(request)
    with pytest.raises(DeviceBusyError):
        registry.acquire("new", (DeviceResource.CT400,))
    assert not protocol.may_start_ct400(request)
    with pytest.raises(DeviceRecoveryRequired):
        protocol.confirm_operator_recovery(request)
    protocol.confirm_operator_recovery(request, operator_confirmed=True)
    assert protocol.active_request is None
    assert all(snapshot.owner is None for snapshot in registry.snapshot())


@pytest.mark.parametrize("ct,pm", [(-1, 0), (0, -1), (True, 0), (0, "1")])
def test_generation_input_validation(ct, pm):
    with pytest.raises(ValueError):
        SynchronizedOwnerLeases(DeviceLeaseRegistry()).reserve(ct400_generation=ct, pm5020_generation=pm)
