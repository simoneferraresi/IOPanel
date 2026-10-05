"""Bounded, read-only-settings streaming check for the approved Top camera.

Run with the laboratory's installed system Python in an ordinary Windows
session. This script never changes camera features or saves image data.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from contextlib import contextmanager
from importlib.metadata import version
from itertools import pairwise

import numpy as np
from vmbpy import FrameStatus, VmbSystem

APPROVED_CAMERA_ID = "DEV_000F315B9CE1"
TARGET_COMPLETE_FRAMES = 10
MAX_ACQUISITION_SECONDS = 15.0
FRAME_BUFFER_COUNT = 5
DISCOVERY_WAIT_SECONDS = 10.0


def read_feature(camera, candidates: tuple[str, ...]):
    """Return (name, value, error); only use SDK feature reads."""
    errors = []
    for name in candidates:
        try:
            feature = camera.get_feature_by_name(name)
        except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        try:
            if not feature.is_readable():
                errors.append(f"{name}: not readable")
                continue
            return name, feature.get(), None
        except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return None, None, "; ".join(errors) or "feature unavailable"


def display_value(value) -> str:
    return getattr(value, "name", str(value))


@contextmanager
def managed_context(resource, result: dict, closed_key: str, label: str):
    """Make SDK context cleanup explicit and report failures during exit."""
    resource.__enter__()
    try:
        yield resource
    finally:
        try:
            resource.__exit__(*sys.exc_info())
        except Exception as exc:
            result["cleanup_errors"].append(f"{label} context exit: {type(exc).__name__}: {exc}")
            raise
        else:
            result[closed_key] = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera-id",
        required=True,
        help="Exact approved camera ID; this run only permits the approved Top camera.",
    )
    args = parser.parse_args()
    if args.camera_id != APPROVED_CAMERA_ID:
        parser.error(f"this script is restricted to {APPROVED_CAMERA_ID}")

    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"VmbPy version: {version('vmbpy')}")
    print(f"Approved camera ID: {args.camera_id}")

    result = {
        "complete_frames": [],
        "other_frames": [],
        "callback_errors": [],
        "cleanup_errors": [],
        "camera_context_closed": False,
        "system_context_closed": False,
        "stream_stop_attempted": False,
        "stream_stopped": False,
        "stream_start_attempted": False,
        "settings": {},
        "stop_reason": None,
        "fatal_error": None,
    }
    done = threading.Event()
    callback_lock = threading.Lock()
    acquisition_started_at = None

    def frame_handler(camera, _stream, frame):
        """Record small metadata/statistics, then always requeue the SDK frame."""
        reached_target = False
        callback_failed = False
        try:
            status = frame.get_status()
            if status == FrameStatus.Complete:
                width = frame.get_width()
                height = frame.get_height()
                pixel_format = frame.get_pixel_format()
                frame_id = frame.get_id()
                camera_timestamp = frame.get_timestamp()
                image_mean = None
                intensity_error = None
                try:
                    image = frame.as_numpy_ndarray()
                    flat = image.reshape(-1)
                    stride = max(1, flat.size // 64)
                    sample = flat[::stride][:64]
                    if sample.size:
                        image_mean = float(np.mean(sample, dtype=np.float64))
                except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
                    intensity_error = f"{type(exc).__name__}: {exc}"

                record = {
                    "status": display_value(status),
                    "width": width,
                    "height": height,
                    "pixel_format": display_value(pixel_format),
                    "frame_id": frame_id,
                    "camera_timestamp": camera_timestamp,
                    "arrival_seconds": time.monotonic() - acquisition_started_at,
                    "sample_mean_intensity": image_mean,
                    "intensity_error": intensity_error,
                }
                with callback_lock:
                    if len(result["complete_frames"]) < TARGET_COMPLETE_FRAMES:
                        result["complete_frames"].append(record)
                    reached_target = len(result["complete_frames"]) >= TARGET_COMPLETE_FRAMES
            else:
                with callback_lock:
                    if len(result["other_frames"]) < 10:
                        result["other_frames"].append(display_value(status))
        except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
            result["callback_errors"].append(f"frame callback: {type(exc).__name__}: {exc}")
            callback_failed = True
        finally:
            # VmbPy 1.0.5 documents queue_frame() as the final operation on a
            # callback frame. Do not inspect or retain the frame after this.
            try:
                camera.queue_frame(frame)
            except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
                result["callback_errors"].append(f"frame requeue: {type(exc).__name__}: {exc}")
                callback_failed = True
            if reached_target or callback_failed:
                done.set()

    try:
        with managed_context(VmbSystem.get_instance(), result, "system_context_closed", "VmbSystem") as vmb:
            print(f"Vimba runtime: {vmb.get_version()}")
            print(f"Waiting {DISCOVERY_WAIT_SECONDS:g}s for camera discovery...")
            time.sleep(DISCOVERY_WAIT_SECONDS)
            try:
                camera = vmb.get_camera_by_id(args.camera_id)
                if camera.get_id() != args.camera_id:
                    raise RuntimeError("SDK returned a camera with a different ID")
                print(f"Selected target identity: {camera.get_id()} / {camera.get_model()}")

                with managed_context(camera, result, "camera_context_closed", "Camera"):
                    checks = {
                        "AcquisitionMode": ("AcquisitionMode",),
                        "TriggerMode": ("TriggerMode",),
                        "Exposure": ("ExposureTime", "ExposureTimeAbs"),
                        "Gain": ("Gain", "GainRaw"),
                        "PixelFormat": ("PixelFormat",),
                        "Width": ("Width",),
                        "Height": ("Height",),
                        "FrameRate": ("AcquisitionFrameRate", "AcquisitionFrameRateAbs"),
                        "ExposureAuto": ("ExposureAuto",),
                        "GainAuto": ("GainAuto",),
                    }
                    for label, candidates in checks.items():
                        name, value, error = read_feature(camera, candidates)
                        result["settings"][label] = {
                            "feature": name,
                            "value": display_value(value) if error is None else None,
                            "read_error": error,
                        }
                        print(
                            f"Current {label}: "
                            f"{display_value(value) if error is None else 'UNAVAILABLE'}"
                            + (f" ({error})" if error else "")
                        )

                    mode = result["settings"]["AcquisitionMode"]["value"]
                    trigger = result["settings"]["TriggerMode"]["value"]
                    if mode is None or trigger is None:
                        result["stop_reason"] = (
                            "Could not verify AcquisitionMode and TriggerMode; streaming was not started."
                        )
                    elif mode.casefold() != "continuous" or trigger.casefold() != "off":
                        result["stop_reason"] = (
                            "Existing settings are not passive free-running "
                            "(requires AcquisitionMode=Continuous and TriggerMode=Off); "
                            "no features were changed."
                        )

                    if result["stop_reason"] is None:
                        acquisition_started_at = time.monotonic()
                        deadline = acquisition_started_at + MAX_ACQUISITION_SECONDS
                        result["stream_start_attempted"] = True
                        try:
                            camera.start_streaming(
                                handler=frame_handler,
                                buffer_count=FRAME_BUFFER_COUNT,
                            )
                            done.wait(max(0.0, deadline - time.monotonic()))
                        finally:
                            # Also attempt stop if start_streaming raised after
                            # partial setup; VmbPy says stop is a no-op if idle.
                            result["stream_stop_attempted"] = True
                            try:
                                camera.stop_streaming()
                                result["stream_stopped"] = True
                            except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
                                result["cleanup_errors"].append(f"stop_streaming: {type(exc).__name__}: {exc}")
                    else:
                        print(result["stop_reason"])

            except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
                result["fatal_error"] = f"{type(exc).__name__}: {exc}"
                print(f"Camera test error: {result['fatal_error']}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        print(f"VmbSystem/cleanup error: {result['fatal_error']}", file=sys.stderr)

    frames = result["complete_frames"]
    print(f"Complete frames: {len(frames)} / {TARGET_COMPLETE_FRAMES}")
    for index, frame in enumerate(frames, start=1):
        print(f"Frame {index}: {frame}")
    arrival_times = [frame["arrival_seconds"] for frame in frames]
    distinct_arrivals = len(arrival_times) == len(set(arrival_times)) and all(
        earlier < later for earlier, later in pairwise(arrival_times)
    )
    frame_ids = [frame["frame_id"] for frame in frames if frame["frame_id"] is not None]
    distinct_frame_ids = len(frame_ids) == len(set(frame_ids))
    within_deadline = all(frame["arrival_seconds"] <= MAX_ACQUISITION_SECONDS for frame in frames)
    print(f"Distinct increasing arrival times: {distinct_arrivals}")
    print(f"Distinct SDK frame IDs when available: {distinct_frame_ids}")
    print(f"All recorded frames arrived within {MAX_ACQUISITION_SECONDS}s: {within_deadline}")
    print(f"Non-complete frame statuses: {result['other_frames']}")
    print(f"Callback/requeue errors: {result['callback_errors']}")
    print(f"Stream stop attempted: {result['stream_stop_attempted']}")
    print(f"Stream stopped normally: {result['stream_stopped']}")
    print(f"Camera context closed normally: {result['camera_context_closed']}")
    print(f"VmbSystem context closed normally: {result['system_context_closed']}")
    print(f"Cleanup errors: {result['cleanup_errors']}")
    if result["fatal_error"]:
        print(f"Fatal error: {result['fatal_error']}")

    if result["stop_reason"]:
        print("RESULT: NOT RUN — existing camera settings were unsuitable or unavailable")
        return 2
    if result["fatal_error"] or result["cleanup_errors"] or result["callback_errors"]:
        print("RESULT: FAIL")
        return 1
    if (
        result["stream_stopped"]
        and result["camera_context_closed"]
        and result["system_context_closed"]
        and len(frames) == TARGET_COMPLETE_FRAMES
        and distinct_arrivals
        and distinct_frame_ids
        and within_deadline
    ):
        print("RESULT: PASS — Top camera streaming path only")
        return 0
    print("RESULT: FAIL — frame target or normal cleanup not achieved")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
