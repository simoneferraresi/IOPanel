"""Fail-closed ownership acknowledgments for a future Qt synchronized acquisition.

This pure-Python state machine does not dispatch Qt signals or call instrument
APIs. A physical profile must not use it as authorization to operate devices.
Workers must remain the only callers of their respective native methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from threading import RLock

from logic.device_lease_registry import (
    DeviceLease,
    DeviceLeaseError,
    DeviceLeaseRegistry,
    DeviceResource,
    LeaseState,
)


class OwnerPhase(Enum):
    RESERVED = auto()
    PM_QUIESCED = auto()
    PM_ARMED = auto()
    CT_READY = auto()
    CT_COMPLETE = auto()
    PM_COMPLETE = auto()


@dataclass(frozen=True, slots=True, eq=False)
class OwnerRequest:
    """Identity-bearing request; generations identify the two owner sessions."""

    request_id: int
    ct400_generation: int
    pm5020_generation: int
    lease: DeviceLease


class SynchronizedOwnerLeases:
    """Acknowledge the safe ordering of queued *simulator-only* owner actions.

    Only owner-thread acknowledgments may advance phases. Cancellation never
    releases ownership, and an uncertain native outcome is quarantined by the
    shared registry. A new request always has a different request ID.
    """

    def __init__(self, registry: DeviceLeaseRegistry) -> None:
        self.registry = registry
        self._lock = RLock()
        self._next_request_id = 1
        self._request: OwnerRequest | None = None
        self._acks: set[OwnerPhase] = set()

    def reserve(self, *, ct400_generation: int, pm5020_generation: int) -> OwnerRequest:
        if type(ct400_generation) is not int or ct400_generation < 0:
            raise ValueError("CT400 generation must be a nonnegative integer")
        if type(pm5020_generation) is not int or pm5020_generation < 0:
            raise ValueError("PM5020 generation must be a nonnegative integer")
        with self._lock:
            if self._request is not None:
                raise DeviceLeaseError("A synchronized owner request is already active")
            lease = self.registry.acquire(
                "synchronized-acquisition",
                (DeviceResource.CT400, DeviceResource.PM5020),
            )
            request = OwnerRequest(self._next_request_id, ct400_generation, pm5020_generation, lease)
            self._next_request_id += 1
            self._request = request
            self._acks.clear()
            return request

    def _matches(
        self,
        request: OwnerRequest,
        *,
        ct400_generation: int | None = None,
        pm5020_generation: int | None = None,
    ) -> bool:
        return (
            request is self._request
            and (ct400_generation is None or ct400_generation == request.ct400_generation)
            and (pm5020_generation is None or pm5020_generation == request.pm5020_generation)
        )

    def acknowledge_pm_quiesced(self, request: OwnerRequest, *, generation: int) -> bool:
        """Ack only after the PM owner stops polling and drains any active read."""
        with self._lock:
            if not self._matches(request, pm5020_generation=generation):
                return False
            self._acks.add(OwnerPhase.PM_QUIESCED)
            return True

    def acknowledge_pm_armed(self, request: OwnerRequest, *, generation: int) -> bool:
        """Ack only after the PM owner successfully arms burst measurement."""
        with self._lock:
            if not self._matches(request, pm5020_generation=generation):
                return False
            if OwnerPhase.PM_QUIESCED not in self._acks:
                raise DeviceLeaseError("PM5020 must acknowledge quiescence before arming")
            self._acks.add(OwnerPhase.PM_ARMED)
            return True

    def acknowledge_ct400_ready(self, request: OwnerRequest, *, generation: int) -> bool:
        """CT400 owner has accepted the request, but has NOT started scanning."""
        with self._lock:
            if not self._matches(request, ct400_generation=generation):
                return False
            self._acks.add(OwnerPhase.CT_READY)
            return True

    def may_start_ct400(self, request: OwnerRequest) -> bool:
        with self._lock:
            return bool(
                self._matches(request)
                and {OwnerPhase.PM_ARMED, OwnerPhase.CT_READY} <= self._acks
                and not self.registry.cancellation_requested(request.lease)
                and not any(
                    snapshot.lease_sequence == request.lease.sequence
                    and snapshot.state is LeaseState.UNCERTAIN
                    for snapshot in self.registry.snapshot()
                )
            )

    def cancel(self, request: OwnerRequest) -> bool:
        with self._lock:
            if not self._matches(request):
                return False
            self.registry.request_cancel(request.lease)
            return True

    def acknowledge_ct400_complete(self, request: OwnerRequest, *, generation: int) -> bool:
        """CT400 scan and serialized owner-thread cleanup are completed."""
        with self._lock:
            if not self._matches(request, ct400_generation=generation):
                return False
            self._acks.add(OwnerPhase.CT_COMPLETE)
            return True

    def acknowledge_pm_complete(self, request: OwnerRequest, *, generation: int) -> bool:
        """PM burst/retrieval and owner-thread cleanup are completed."""
        with self._lock:
            if not self._matches(request, pm5020_generation=generation):
                return False
            self._acks.add(OwnerPhase.PM_COMPLETE)
            return True

    def release_after_cleanup(self, request: OwnerRequest) -> None:
        """Release only with both owners' explicit cleanup acknowledgments."""
        with self._lock:
            if not self._matches(request):
                raise DeviceLeaseError("Stale or foreign owner request")
            if {OwnerPhase.CT_COMPLETE, OwnerPhase.PM_COMPLETE} - self._acks:
                raise DeviceLeaseError("Both instrument owners must acknowledge completed cleanup")
            self.registry.release(request.lease, operations_complete=True)
            self._request = None
            self._acks.clear()

    def quarantine(self, request: OwnerRequest, reason: str) -> None:
        """Timeout or ambiguous cleanup cannot free any resource."""
        with self._lock:
            if not self._matches(request):
                raise DeviceLeaseError("Stale or foreign owner request")
            self.registry.mark_uncertain(request.lease, reason)

    def confirm_operator_recovery(self, request: OwnerRequest, *, operator_confirmed: bool = False) -> None:
        """Explicit manual recovery only; not callable from timeout/cancel handlers."""
        with self._lock:
            if not self._matches(request):
                raise DeviceLeaseError("Stale or foreign owner request")
            self.registry.confirm_recovery(request.lease, operator_confirmed=operator_confirmed)
            self._request = None
            self._acks.clear()

    @property
    def active_request(self) -> OwnerRequest | None:
        with self._lock:
            return self._request
