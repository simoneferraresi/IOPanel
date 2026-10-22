"""M4-E2A: fail-closed *logical* reservations for CT400 and PM5020.

No instrument APIs, Qt, native DLLs, waits or polling. A reservation is NOT a
driver mutex: all live call sites must opt in before cross-panel exclusivity
can be claimed. In particular, this module is not wired to any live worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import Event, Lock
from typing import Iterable


class DeviceResource(str, Enum):
    CT400 = "ct400"
    PM5020 = "pm5020"


class LeaseState(str, Enum):
    RESERVED = "reserved"
    UNCERTAIN = "uncertain"


class DeviceLeaseError(RuntimeError):
    """Invalid, conflicting, or unsafe lease operation."""


class DeviceBusyError(DeviceLeaseError):
    """At least one requested instrument already belongs to another operation."""


class DeviceRecoveryRequired(DeviceLeaseError):
    """Reservation cannot be released until recovery is explicitly confirmed."""


@dataclass(frozen=True, slots=True, eq=False)
class DeviceLease:
    """Identity-bearing capability. Copying its fields cannot forge the owner."""

    sequence: int
    owner: str
    resources: frozenset[DeviceResource]


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    resource: DeviceResource
    owner: str | None
    lease_sequence: int | None
    state: LeaseState | None
    cancel_requested: bool
    reason: str | None


@dataclass(slots=True)
class _LeaseRecord:
    lease: DeviceLease
    cancel: Event
    state: LeaseState = LeaseState.RESERVED
    reason: str | None = None


class DeviceLeaseRegistry:
    """Nonblocking atomic multi-device claims with explicit completion/recovery.

    A client must call release(..., operations_complete=True) *only after* its
    native operations and owner-thread cleanup have definitively finished.
    Cancellation is a request, not permission to release. Uncertain device
    state retains ownership until an external operator confirms recovery.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._next_sequence = 1
        self._leases: dict[int, _LeaseRecord] = {}
        self._occupied: dict[DeviceResource, int] = {}

    @staticmethod
    def _resources(value: Iterable[DeviceResource]) -> frozenset[DeviceResource]:
        try:
            selected = tuple(value)
        except TypeError as exc:
            raise ValueError("resources must be an iterable of DeviceResource") from exc
        if not selected:
            raise ValueError("At least one device resource must be requested")
        if any(type(item) is not DeviceResource for item in selected):
            raise TypeError("Only DeviceResource members are accepted")
        if len(set(selected)) != len(selected):
            raise ValueError("Duplicate device resources are not allowed")
        return frozenset(selected)

    def acquire(self, owner: str, resources: Iterable[DeviceResource]) -> DeviceLease:
        """Acquire all requested devices together, or acquire none; never wait."""
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("A nonempty operation owner is required")
        requested = self._resources(resources)
        with self._lock:
            busy = requested.intersection(self._occupied)
            if busy:
                names = ", ".join(sorted(item.value for item in busy))
                raise DeviceBusyError(f"Device reservation denied: {names} already owned")
            lease = DeviceLease(self._next_sequence, owner.strip(), requested)
            self._next_sequence += 1
            self._leases[lease.sequence] = _LeaseRecord(lease=lease, cancel=Event())
            for resource in requested:
                self._occupied[resource] = lease.sequence
            return lease

    def _record(self, lease: DeviceLease) -> _LeaseRecord:
        if not isinstance(lease, DeviceLease):
            raise DeviceLeaseError("A valid DeviceLease token is required")
        record = self._leases.get(lease.sequence)
        if record is None or record.lease is not lease:
            raise DeviceLeaseError("Stale, released or foreign device lease token")
        return record

    def request_cancel(self, lease: DeviceLease) -> None:
        """Acknowledge an intent to stop. Never abandon a native operation."""
        with self._lock:
            self._record(lease).cancel.set()

    def cancellation_requested(self, lease: DeviceLease) -> bool:
        with self._lock:
            return self._record(lease).cancel.is_set()

    def mark_uncertain(self, lease: DeviceLease, reason: str) -> None:
        """Quarantine all leased devices after unknown native/cleanup outcome."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("An explicit recovery reason is required")
        with self._lock:
            record = self._record(lease)
            record.state = LeaseState.UNCERTAIN
            record.reason = reason.strip()
            record.cancel.set()

    def release(self, lease: DeviceLease, *, operations_complete: bool = False) -> None:
        """Release only after all owner-thread operations have completed.

        Never release on timeout; use mark_uncertain and operator recovery.
        """
        with self._lock:
            record = self._record(lease)
            if record.state is LeaseState.UNCERTAIN:
                raise DeviceRecoveryRequired("Device state uncertain: operator-confirmed recovery required")
            if operations_complete is not True:
                raise DeviceLeaseError("Native operations must be confirmed complete before releasing a lease")
            self._release_record(record)

    def confirm_recovery(self, lease: DeviceLease, *, operator_confirmed: bool = False) -> None:
        """Release quarantined resources only after external physical recovery.

        The boolean is a declaration by the caller, NOT device verification.
        This method must never be called automatically on timeout or errors.
        """
        with self._lock:
            record = self._record(lease)
            if record.state is not LeaseState.UNCERTAIN:
                raise DeviceLeaseError("Only an uncertain lease can be released by recovery confirmation")
            if operator_confirmed is not True:
                raise DeviceRecoveryRequired("External operator must confirm recovery explicitly")
            self._release_record(record)

    def _release_record(self, record: _LeaseRecord) -> None:
        lease = record.lease
        for resource in lease.resources:
            if self._occupied.get(resource) != lease.sequence:
                raise DeviceLeaseError("Inconsistent resource ownership; refusing partial release")
        for resource in lease.resources:
            del self._occupied[resource]
        del self._leases[lease.sequence]

    def snapshot(self) -> tuple[ResourceSnapshot, ...]:
        """Thread-safe immutable UI-readable state. No instrument activity."""
        with self._lock:
            result = []
            for resource in DeviceResource:
                lease_id = self._occupied.get(resource)
                if lease_id is None:
                    result.append(ResourceSnapshot(resource, None, None, None, False, None))
                    continue
                record = self._leases[lease_id]
                result.append(
                    ResourceSnapshot(
                        resource,
                        record.lease.owner,
                        lease_id,
                        record.state,
                        record.cancel.is_set(),
                        record.reason,
                    )
                )
            return tuple(result)
