"""M4-E1: supervised-design synchronized coordinator, *simulators only*.

This module refuses physical backends. It exercises ordering and failure
recovery before a separate physical-ownership/timeout qualification.
No Qt, Thorlabs DLL, CT400 DLL or EDFA operations are performed here.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event, Lock

from hardware.ct400_types import CT400ScanResultKind, Detector, Enable, ScanWaitResult
from hardware.dummy_ct400 import DummyCT400
from hardware.dummy_pm5020 import DummyPM5020
from hardware.pm5020_types import BurstAcquisitionConfig, BurstPowerData, PM5020State
from logic.trigger_sync_alignment import (
    SynchronizedBurst,
    SyncPulse,
    align_ct400_burst,
    parse_ct400_sync_text,
)

logger = logging.getLogger("LabApp.SimulatedSync")


class SynchronizedAcquisitionError(RuntimeError):
    """Simulated coordinated scan could not produce a valid result."""


class SynchronizedAcquisitionCancelled(SynchronizedAcquisitionError):
    """Operator cancellation; partial measurements must not be exported."""


@dataclass(frozen=True, slots=True)
class SimulatedSyncSettings:
    start_nm: float = 1545.0
    stop_nm: float = 1555.0
    speed_nm_s: int = 10
    laser_power_mw: float = 1.0
    resolution_pm: int = 50
    detectors: tuple[Detector, ...] = (Detector.DE_1,)

    def __post_init__(self) -> None:
        import math

        if not (math.isfinite(self.start_nm) and math.isfinite(self.stop_nm) and self.start_nm < self.stop_nm):
            raise ValueError("A finite increasing wavelength range is required")
        if type(self.speed_nm_s) is not int or not 10 <= self.speed_nm_s <= 100:
            raise ValueError("CT400 speed must be an integer within 10–100 nm/s")
        if not math.isfinite(self.laser_power_mw) or not 1 <= self.laser_power_mw <= 10:
            raise ValueError("CT400 laser power must be within 1–10 mW")
        if type(self.resolution_pm) is not int or not 1 <= self.resolution_pm <= 250:
            raise ValueError("CT400 resolution must be an integer within 1–250 pm")
        if (
            not self.detectors
            or any(
                type(item) is not Detector or item not in tuple(Detector(i) for i in range(1, 5))
                for item in self.detectors
            )
            or len(set(self.detectors)) != len(self.detectors)
        ):
            raise ValueError("Select one or more unique CT400 detectors DE_1–DE_4")


@dataclass(frozen=True, slots=True)
class SimulatedSynchronizedCapture:
    settings: SimulatedSyncSettings
    status: ScanWaitResult
    pm_burst: BurstPowerData
    sync_text: str
    pulses: tuple[SyncPulse, ...]
    aligned: SynchronizedBurst
    ct400_wavelengths_nm: tuple[float, ...]
    ct400_detector_data: tuple[tuple[float, ...], ...]
    completed_at_utc: datetime
    simulated: bool = True
    index_base: int = 0
    timing_qualified: bool = False


class SimulatedSynchronizedCoordinator:
    """Run one coordinated scan with connected *Dummy* devices exclusively.

    The caller is responsible for preconnecting simulators. The sync-text
    supplier models the CT400's post-scan vendor file (it does not synthesize
    evidence of actual external trigger pulses). It is called only after
    scan completion. This prototype is intentionally not wired into the GUI.

    The coordinator owns its in-flight simulated burst, but not the existing
    device connections. After an abnormal run it closes the PM5020 to disarm
    it, preserving the underlying exception; re-connect before retrying.
    """

    def __init__(
        self,
        ct400: DummyCT400,
        pm5020: DummyPM5020,
        sync_text_source: Callable[[], str],
        *,
        settings: SimulatedSyncSettings | None = None,
        burst_config: BurstAcquisitionConfig | None = None,
    ) -> None:
        if not isinstance(ct400, DummyCT400) or not isinstance(pm5020, DummyPM5020):
            raise TypeError("M4-E1 accepts DummyCT400 and DummyPM5020 only; physical hardware is prohibited")
        if not callable(sync_text_source):
            raise TypeError("sync_text_source must supply archived-format CT400 sync text")
        self._ct = ct400
        self._pm = pm5020
        self._sync_text_source = sync_text_source
        self.settings = settings or SimulatedSyncSettings()
        self.burst_config = burst_config or BurstAcquisitionConfig()
        self._run_lock = Lock()
        self._cancel_event = Event()
        self._scan_started = False

    def cancel(self) -> None:
        """Request simulated cancellation, including an in-progress scan wait."""
        if self._run_lock.locked():
            self._cancel_event.set()
            if self._scan_started:
                self._ct.stop_scan()

    def run(self) -> SimulatedSynchronizedCapture:
        """Execute synchronously; callers may run this on their own test worker."""
        if not self._run_lock.acquire(blocking=False):
            raise SynchronizedAcquisitionError("A synchronized simulated acquisition is already running")
        self._cancel_event.clear()
        self._scan_started = False
        sync_attempted = False
        try:
            if not self._ct.is_connected() or not self._pm.is_ready:
                raise SynchronizedAcquisitionError("Both simulators must be connected and PM5020 idle")
            if self._cancel_event.is_set():
                raise SynchronizedAcquisitionCancelled("Cancelled before configuration")

            settings = self.settings
            self._ct.set_sampling_res(settings.resolution_pm)
            selected = set(settings.detectors)
            self._ct.set_detector_array(
                Enable.ENABLE if Detector.DE_2 in selected else Enable.DISABLE,
                Enable.ENABLE if Detector.DE_3 in selected else Enable.DISABLE,
                Enable.ENABLE if Detector.DE_4 in selected else Enable.DISABLE,
                Enable.DISABLE,
            )
            self._ct.set_scan(settings.laser_power_mw, settings.start_nm, settings.stop_nm)
            self._pm.configure_burst(self.burst_config)

            sync_attempted = True
            self._ct.set_external_synchronization(Enable.ENABLE)
            if self._cancel_event.is_set():
                raise SynchronizedAcquisitionCancelled("Cancelled before PM5020 arming")
            self._pm.start_burst()
            if self._pm.state is not PM5020State.ARMED:
                raise SynchronizedAcquisitionError("PM5020 did not enter ARMED state")
            if self._cancel_event.is_set():
                raise SynchronizedAcquisitionCancelled("Cancelled before CT400 scan")

            self._ct.start_scan()
            self._scan_started = True
            if self._cancel_event.is_set():
                self._ct.stop_scan()
            status = self._ct.scan_wait_end()
            self._scan_started = False
            if self._cancel_event.is_set() or status.kind is CT400ScanResultKind.USER_CANCELLED:
                raise SynchronizedAcquisitionCancelled("CT400 synchronized scan cancelled")
            if status.kind not in (CT400ScanResultKind.SUCCESS, CT400ScanResultKind.WARNING):
                raise SynchronizedAcquisitionError(
                    f"CT400 scan failed: raw code {status.raw_code}; {status.error_message}"
                )

            wavelengths, detector_data = self._ct.get_data_points(list(settings.detectors))
            sync_text = self._sync_text_source()
            pulses = parse_ct400_sync_text(sync_text)
            burst = self._pm.retrieve_burst(cancel_event=self._cancel_event)
            if self._cancel_event.is_set():
                raise SynchronizedAcquisitionCancelled("Cancelled during PM5020 retrieval")
            aligned = align_ct400_burst(pulses, burst, index_base=0)
            return SimulatedSynchronizedCapture(
                settings=settings,
                status=status,
                pm_burst=burst,
                sync_text=sync_text,
                pulses=pulses,
                aligned=aligned,
                ct400_wavelengths_nm=tuple(float(w) for w in wavelengths),
                ct400_detector_data=tuple(tuple(float(p) for p in row) for row in detector_data),
                completed_at_utc=datetime.now(UTC),
            )
        except Exception as exc:
            if self._scan_started:
                try:
                    self._ct.stop_scan()
                    self._ct.scan_wait_end()
                except Exception as cleanup_exc:  # noqa: BLE001 - preserve original failure
                    exc.add_note(f"Simulated CT400 stop/wait cleanup also failed: {cleanup_exc}")
                self._scan_started = False
            if self._pm.state in (PM5020State.ARMED, PM5020State.RETRIEVING, PM5020State.ERROR):
                try:
                    self._pm.close()
                except Exception as cleanup_exc:  # noqa: BLE001 - cleanup must preserve original failure
                    exc.add_note(f"Simulated PM5020 recovery close also failed: {cleanup_exc}")
            raise
        finally:
            try:
                if sync_attempted:
                    self._ct.set_external_synchronization(Enable.DISABLE)
            except Exception as cleanup_exc:
                active_exc = sys.exc_info()[1]
                if active_exc is None:
                    raise SynchronizedAcquisitionError("Could not disable simulated CT400 sync output") from cleanup_exc
                active_exc.add_note(f"Could not disable simulated CT400 sync output: {cleanup_exc}")
                logger.exception("Simulated trigger-output cleanup failed")
            finally:
                self._run_lock.release()


def synthetic_ct400_sync_text(
    *,
    first_index: int = 14,
    point_count: int = 240,
    start_nm: float = 1545.0,
    stop_nm: float = 1555.0,
) -> str:
    """Build an *explicitly synthetic* vendor-style file for offline testing.

    These are fake pulse positions, not asserted physical CT400/PM5020 timing.
    The count/indices must fit the separately configured dummy PM buffer.
    """
    import math

    if type(first_index) is not int or first_index < 0 or type(point_count) is not int or point_count < 2:
        raise ValueError("Invalid synthetic sync index range")
    if not (math.isfinite(start_nm) and math.isfinite(stop_nm) and 0 < start_nm < stop_nm):
        raise ValueError("Invalid synthetic wavelength range")
    return "".join(
        f"{first_index + i} {start_nm + (stop_nm - start_nm) * i / (point_count - 1):.9f}\n" for i in range(point_count)
    )
