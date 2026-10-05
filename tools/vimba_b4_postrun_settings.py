"""Read-only post-run settings snapshot for the approved IOPanel cameras.

Run only after separate authorization, in an ordinary Windows session with
the lab's system Python and installed Vimba X SDK. Opens only the exact Top
and Side IDs, one at a time. It does not stream, acquire frames, or write any
camera feature.
"""

from __future__ import annotations

import sys
import time
from importlib.metadata import version

from vmbpy import VmbSystem

CAMERAS = {
    "Top": {
        "id": "DEV_000F315B9CE1",
        "baseline": {
            "AcquisitionMode": "Continuous",
            "TriggerMode": "Off",
            "Exposure": 16955.0,
            "Gain": 30.0,
            "PixelFormat": "Mono8",
            "Width": 1292,
            "Height": 964,
            "FrameRate": 30.335204,
            "ExposureAuto": "Off",
            "GainAuto": "Off",
        },
    },
    "Side": {
        "id": "DEV_000F315BA8F9",
        "baseline": {
            "AcquisitionMode": "Continuous",
            "TriggerMode": "Off",
            "Exposure": 6180.0,
            "Gain": 30.0,
            "PixelFormat": "Mono8",
            "Width": 1292,
            "Height": 964,
            "FrameRate": 30.335,
            # The operator report did not include Side auto-mode readback.
        },
    },
}
FEATURES = {
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
DISCOVERY_WAIT_SECONDS = 10.0
FRAME_RATE_TOLERANCE = 0.01


def display_value(value):
    return getattr(value, "name", value)


def read_feature(camera, candidates):
    failures = []
    for name in candidates:
        try:
            feature = camera.get_feature_by_name(name)
            if not feature.is_readable():
                failures.append(f"{name}: not readable")
                continue
            return display_value(feature.get()), None
        except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    return None, "; ".join(failures) or "feature unavailable"


def compare_value(label, current, baseline):
    if baseline is None:
        return "NO BASELINE"
    if current is None:
        return "UNAVAILABLE"
    try:
        current_number = float(current)
        baseline_number = float(baseline)
    except (TypeError, ValueError):
        return "MATCH" if str(current).casefold() == str(baseline).casefold() else "DIFF"
    tolerance = FRAME_RATE_TOLERANCE if label == "FrameRate" else 1e-6
    return "MATCH" if abs(current_number - baseline_number) <= tolerance else "DIFF"


def main() -> int:
    print(f"Python executable: {sys.executable}")
    print(f"Python version: {sys.version.split()[0]}")
    print(f"VmbPy version: {version('vmbpy')}")
    errors = []
    try:
        with VmbSystem.get_instance() as vmb:
            print(f"Vimba runtime: {vmb.get_version()}")
            print(f"Discovery wait: {DISCOVERY_WAIT_SECONDS:g}s")
            time.sleep(DISCOVERY_WAIT_SECONDS)

            for camera_name, entry in CAMERAS.items():
                camera_id = entry["id"]
                try:
                    camera = vmb.get_camera_by_id(camera_id)
                    if camera.get_id() != camera_id:
                        raise RuntimeError(f"Expected {camera_id}, SDK returned {camera.get_id()}")
                    print(f"\n{camera_name} ({camera_id})")
                    with camera:
                        for label, candidates in FEATURES.items():
                            current, error = read_feature(camera, candidates)
                            baseline = entry["baseline"].get(label)
                            status = compare_value(label, current, baseline)
                            print(
                                f"{label}: current={current!r}; "
                                f"baseline={baseline!r}; comparison={status}"
                                + (f"; read error={error}" if error else "")
                            )

                        try:
                            gamma = camera.get_feature_by_name("Gamma")
                            if not gamma.is_readable():
                                raise RuntimeError("Gamma is not readable")
                            gamma_value = gamma.get()
                            gamma_range = gamma.get_range()
                            gamma_writable = gamma.is_writeable()
                            print(
                                f"Gamma: current={gamma_value!r}; "
                                "baseline=NOT RECORDED; supported range="
                                f"{gamma_range!r}; writable={gamma_writable}"
                            )
                        except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
                            errors.append(f"{camera_name} Gamma: {type(exc).__name__}: {exc}")
                            print(f"ERROR: {errors[-1]}", file=sys.stderr)
                except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
                    errors.append(f"{camera_name} ({camera_id}): {type(exc).__name__}: {exc}")
                    print(f"ERROR: {errors[-1]}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001  # Diagnostic probe captures vendor API failures across SDK versions.
        errors.append(f"VmbSystem/cleanup: {type(exc).__name__}: {exc}")
        print(f"ERROR: {errors[-1]}", file=sys.stderr)

    print(f"Errors: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
