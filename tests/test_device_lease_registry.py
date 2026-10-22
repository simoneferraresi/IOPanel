"""M4-E2A pure Python device-lease protocol; never starts an instrument."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from logic.device_lease_registry import (
    DeviceBusyError,
    DeviceLease,
    DeviceLeaseError,
    DeviceLeaseRegistry,
    DeviceRecoveryRequired,
    DeviceResource,
    LeaseState,
)


def test_atomic_multi_resource_reservation_and_disjoint_owners():
    registry = DeviceLeaseRegistry()
    ct = registry.acquire("ct400-monitor", (DeviceResource.CT400,))
    pm = registry.acquire("pm-monitor", (DeviceResource.PM5020,))
    with pytest.raises(DeviceBusyError, match="ct400"):
        registry.acquire("coordinated", (DeviceResource.CT400, DeviceResource.PM5020))
    assert {entry.owner for entry in registry.snapshot()} == {"ct400-monitor", "pm-monitor"}
    registry.release(ct, operations_complete=True)
    registry.release(pm, operations_complete=True)
    joined = registry.acquire("coordinated", (DeviceResource.CT400, DeviceResource.PM5020))
    assert joined.resources == frozenset({DeviceResource.CT400, DeviceResource.PM5020})
    assert all(item.owner == "coordinated" for item in registry.snapshot())
    registry.release(joined, operations_complete=True)
    assert all(item.owner is None for item in registry.snapshot())


def test_failed_multi_device_request_does_not_partially_claim_resources():
    registry = DeviceLeaseRegistry()
    ct = registry.acquire("existing", (DeviceResource.CT400,))
    with pytest.raises(DeviceBusyError):
        registry.acquire("new", (DeviceResource.CT400, DeviceResource.PM5020))
    assert registry.snapshot()[1].owner is None
    pm = registry.acquire("pm-only", (DeviceResource.PM5020,))
    registry.release(ct, operations_complete=True)
    registry.release(pm, operations_complete=True)


def test_complete_acknowledgement_required_and_stale_tokens_cannot_release():
    registry = DeviceLeaseRegistry()
    lease = registry.acquire("scan", (DeviceResource.CT400,))
    with pytest.raises(DeviceLeaseError, match="confirmed complete"):
        registry.release(lease)
    forged = DeviceLease(lease.sequence, lease.owner, lease.resources)
    with pytest.raises(DeviceLeaseError, match="Stale"):
        registry.release(forged, operations_complete=True)
    other_registry = DeviceLeaseRegistry()
    with pytest.raises(DeviceLeaseError, match="foreign"):
        other_registry.release(lease, operations_complete=True)
    registry.release(lease, operations_complete=True)
    with pytest.raises(DeviceLeaseError, match="Stale"):
        registry.request_cancel(lease)
    fresh = registry.acquire("scan", (DeviceResource.CT400,))
    assert fresh.sequence > lease.sequence
    registry.release(fresh, operations_complete=True)


def test_cancel_never_releases_devices_or_grants_competing_owner():
    registry = DeviceLeaseRegistry()
    lease = registry.acquire("capture", (DeviceResource.CT400, DeviceResource.PM5020))
    registry.request_cancel(lease)
    assert registry.cancellation_requested(lease)
    assert all(entry.cancel_requested for entry in registry.snapshot())
    with pytest.raises(DeviceBusyError):
        registry.acquire("other", (DeviceResource.CT400,))
    registry.release(lease, operations_complete=True)
    assert not any(entry.cancel_requested for entry in registry.snapshot())


def test_unknown_driver_outcome_quarantines_all_resources_without_timeout_release():
    registry = DeviceLeaseRegistry()
    lease = registry.acquire("capture", (DeviceResource.CT400, DeviceResource.PM5020))
    registry.mark_uncertain(lease, "native scan wait ended without an acknowledged result")
    assert all(item.state is LeaseState.UNCERTAIN for item in registry.snapshot())
    assert all(item.reason and "native scan" in item.reason for item in registry.snapshot())
    with pytest.raises(DeviceRecoveryRequired):
        registry.release(lease, operations_complete=True)
    with pytest.raises(DeviceRecoveryRequired):
        registry.confirm_recovery(lease)
    with pytest.raises(DeviceBusyError):
        registry.acquire("monitor", (DeviceResource.PM5020,))
    registry.confirm_recovery(lease, operator_confirmed=True)
    assert all(item.state is None for item in registry.snapshot())


def test_recovery_confirmation_cannot_bypass_normal_completed_reservation():
    registry = DeviceLeaseRegistry()
    lease = registry.acquire("normal", (DeviceResource.CT400,))
    with pytest.raises(DeviceLeaseError, match="Only an uncertain"):
        registry.confirm_recovery(lease, operator_confirmed=True)
    registry.release(lease, operations_complete=True)


@pytest.mark.parametrize(
    "owner,resources,error",
    [
        ("", (DeviceResource.CT400,), ValueError),
        ("   ", (DeviceResource.CT400,), ValueError),
        ("a", (), ValueError),
        ("a", (DeviceResource.CT400, DeviceResource.CT400), ValueError),
        ("a", ("ct400",), TypeError),
        ("a", None, ValueError),
    ],
)
def test_invalid_lease_requests_are_rejected_without_reserving(owner, resources, error):
    registry = DeviceLeaseRegistry()
    with pytest.raises(error):
        registry.acquire(owner, resources)
    assert all(entry.owner is None for entry in registry.snapshot())


def test_uncertain_reason_required():
    registry = DeviceLeaseRegistry()
    lease = registry.acquire("capture", (DeviceResource.PM5020,))
    with pytest.raises(ValueError, match="reason"):
        registry.mark_uncertain(lease, " ")
    registry.release(lease, operations_complete=True)


def test_parallel_claim_race_has_exactly_one_success():
    registry = DeviceLeaseRegistry()
    barrier = Barrier(8)

    def contest(i: int):
        barrier.wait(timeout=3)
        try:
            return registry.acquire(f"worker-{i}", (DeviceResource.CT400, DeviceResource.PM5020))
        except DeviceBusyError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        leases = tuple(pool.map(contest, range(8)))
    winning = [item for item in leases if item is not None]
    assert len(winning) == 1
    assert all(item.lease_sequence == winning[0].sequence for item in registry.snapshot())
    registry.release(winning[0], operations_complete=True)
    assert all(item.lease_sequence is None for item in registry.snapshot())
