"""Bounded exact-ID discovery helpers for Vimba camera systems."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

DISCOVERY_TIMEOUT_SECONDS = 10.0
DISCOVERY_POLL_INTERVAL_SECONDS = 0.25


class CameraDiscoveryError(RuntimeError):
    """Base error for a camera discovery operation."""


class CameraNotFoundError(CameraDiscoveryError):
    """Raised when the requested exact camera IDs do not appear in time."""

    def __init__(self, camera_ids: tuple[str, ...], timeout_s: float, visible_ids: tuple[str, ...]):
        missing = tuple(camera_id for camera_id in camera_ids if camera_id not in visible_ids)
        self.camera_ids = camera_ids
        self.missing_ids = missing
        self.timeout_s = timeout_s
        self.visible_ids = visible_ids
        missing_text = ", ".join(missing)
        visible_text = ", ".join(visible_ids) if visible_ids else "none"
        super().__init__(
            f"Timed out after {timeout_s:g}s waiting for exact camera ID(s): {missing_text}. "
            f"VmbSystem was active; visible camera IDs at timeout: {visible_text}."
        )


class CameraDiscoveryCancelled(CameraDiscoveryError):
    """Raised when a caller cancels a pending discovery wait."""


def wait_for_cameras_by_id(
    system: Any,
    camera_ids: Iterable[str],
    *,
    timeout_s: float = DISCOVERY_TIMEOUT_SECONDS,
    poll_interval_s: float = DISCOVERY_POLL_INTERVAL_SECONDS,
    cancel_event: threading.Event | None = None,
    on_wait: Callable[[], None] | None = None,
    on_visible: Callable[[tuple[str, ...]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Return all requested camera objects, polling one shared bounded deadline.

    The system is queried immediately and every result is matched by exact ID.
    No camera is opened here. SDK errors are allowed to propagate so runtime
    failures remain distinct from a camera that did not appear before timeout.
    """
    requested = tuple(dict.fromkeys(camera_ids))
    if not requested or any(not camera_id for camera_id in requested):
        raise ValueError("At least one non-empty exact camera ID is required")
    if timeout_s < 0 or poll_interval_s <= 0:
        raise ValueError("timeout_s must be non-negative and poll_interval_s must be positive")

    deadline = clock() + timeout_s
    did_wait = False
    visible_ids: tuple[str, ...] = ()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise CameraDiscoveryCancelled("Camera discovery was cancelled")

        cameras = system.get_all_cameras()
        by_id = {camera.get_id(): camera for camera in cameras}
        visible_ids = tuple(by_id)
        if on_visible is not None:
            on_visible(visible_ids)
        if all(camera_id in by_id for camera_id in requested):
            return {camera_id: by_id[camera_id] for camera_id in requested}

        remaining = deadline - clock()
        if remaining <= 0:
            raise CameraNotFoundError(requested, timeout_s, visible_ids)

        if not did_wait:
            did_wait = True
            if on_wait is not None:
                on_wait()
        delay = min(poll_interval_s, remaining)
        if cancel_event is None:
            sleeper(delay)
        elif cancel_event.wait(delay):
            raise CameraDiscoveryCancelled("Camera discovery was cancelled")


def wait_for_camera_by_id(system: Any, camera_id: str, **kwargs: Any) -> Any:
    """Return one camera selected by its exact ID after bounded discovery."""
    return wait_for_cameras_by_id(system, (camera_id,), **kwargs)[camera_id]


def poll_camera_list(
    system: Any,
    *,
    timeout_s: float = DISCOVERY_TIMEOUT_SECONDS,
    poll_interval_s: float = DISCOVERY_POLL_INTERVAL_SECONDS,
    cancel_event: threading.Event | None = None,
    on_update: Callable[[list[Any]], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """Publish camera-list changes during a bounded discovery observation.

    This is intended for the manual discovery dialog, which has no requested
    exact ID and can display early results while waiting for later devices.
    """
    if timeout_s < 0 or poll_interval_s <= 0:
        raise ValueError("timeout_s must be non-negative and poll_interval_s must be positive")
    deadline = clock() + timeout_s
    last_ids: tuple[str, ...] | None = None
    cameras: list[Any] = []
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return cameras
        cameras = list(system.get_all_cameras())
        camera_ids = tuple(camera.get_id() for camera in cameras)
        if camera_ids != last_ids:
            last_ids = camera_ids
            if on_update is not None:
                on_update(cameras)
        remaining = deadline - clock()
        if remaining <= 0:
            return cameras
        delay = min(poll_interval_s, remaining)
        if cancel_event is None:
            sleeper(delay)
        elif cancel_event.wait(delay):
            return cameras
