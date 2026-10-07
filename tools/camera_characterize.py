"""Operator-authorized one- or two-camera timing and image-quality characterization.

Default invocation is read-only. Setting-changing runs require
``--authorize-settings-changes`` and print the proposed changes before use.
This tool is prepared for a later lab session; it is not run by the application.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hardware.camera_analysis import analyze_frames, frame_timing_metrics
from hardware.camera_capabilities import (
    FEATURE_ALIASES,
    ROI,
    apply_roi,
    find_feature,
    inspect_camera,
    inspect_feature,
    read_roi,
    report_value,
)
from hardware.camera_discovery import DISCOVERY_TIMEOUT_SECONDS, CameraNotFoundError, wait_for_cameras_by_id


def _read(camera: Any, logical_name: str) -> tuple[str, Any]:
    name, feature = find_feature(camera, FEATURE_ALIASES[logical_name])
    if feature is None or not feature.is_readable():
        raise RuntimeError(f"Required feature {logical_name} is unavailable/read-only")
    return name, feature.get()


def _verify_continuous_untriggered(camera: Any) -> None:
    mode = _read(camera, "acquisition_mode")[1]
    trigger = _read(camera, "trigger_mode")[1]
    mode_name = str(getattr(mode, "name", mode)).casefold()
    trigger_name = str(getattr(trigger, "name", trigger)).casefold()
    if mode_name != "continuous" or trigger_name != "off":
        raise RuntimeError(
            f"Streaming characterization requires AcquisitionMode=Continuous and TriggerMode=Off; "
            f"read {mode_name!r} and {trigger_name!r}. No settings were changed."
        )


def _set(camera: Any, logical_name: str, value: Any) -> None:
    name, feature = find_feature(camera, FEATURE_ALIASES[logical_name])
    if feature is None or not feature.is_writeable():
        raise RuntimeError(f"Feature {logical_name} is not writable")
    feature.set(value)
    actual = feature.get()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not np.isclose(float(actual), float(value), rtol=1e-6, atol=1e-6):
            raise RuntimeError(f"{name} readback {actual} differs from requested {value}")
    else:
        requested_value = report_value(value)
        actual_value = report_value(actual)
        if actual_value != requested_value:
            raise RuntimeError(f"{name} readback {actual_value!r} differs from requested {requested_value!r}")


def _restorable_value(value: Any) -> Any:
    """Replace native enum wrappers in setting snapshots with stable values."""
    return report_value(value)


def _parse_values(text: str, label: str) -> list[float]:
    try:
        values = [float(part.strip()) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be a comma-separated list of numbers") from exc
    if not values or not all(np.isfinite(values)):
        raise argparse.ArgumentTypeError(f"{label} must contain finite values")
    return values


def _validate_requested(camera: Any, logical: str, value: float) -> None:
    cap = inspect_feature(camera, FEATURE_ALIASES[logical])
    if not cap.available or not cap.writable or cap.minimum is None or cap.maximum is None:
        raise RuntimeError(f"Cannot validate {logical}; feature range/writability is unavailable")
    if not cap.minimum <= value <= cap.maximum:
        raise ValueError(f"{logical} {value} is outside queried range [{cap.minimum}, {cap.maximum}]")
    if cap.increment:
        steps = (value - cap.minimum) / cap.increment
        if not np.isclose(steps, round(steps), atol=1e-5):
            raise ValueError(f"{logical} {value} does not match queried increment {cap.increment} from {cap.minimum}")


def _capture(
    camera: Any,
    count: int | None,
    timeout_s: float,
    duration_s: float | None = None,
    collect_images: bool = True,
) -> tuple[list[np.ndarray], list[float], list[int], int]:
    from vmbpy import FrameStatus

    done = threading.Event()
    frames: list[np.ndarray] = []
    times: list[float] = []
    ids: list[int] = []
    incomplete = 0
    complete_count = 0
    start = time.monotonic()
    errors: list[Exception] = []

    def handler(cam, _stream, frame):
        nonlocal complete_count, incomplete
        try:
            if frame.get_status() == FrameStatus.Complete:
                if collect_images:
                    frames.append(np.array(frame.as_numpy_ndarray(), copy=True))
                times.append(time.monotonic())
                ids.append(int(frame.get_id()))
                complete_count += 1
                if (count is not None and complete_count >= count) or (
                    duration_s is not None and time.monotonic() - start >= duration_s
                ):
                    done.set()
            else:
                incomplete += 1
        except Exception as exc:  # noqa: BLE001 - propagate SDK callback failures after stop.
            errors.append(exc)
            done.set()
        finally:
            try:
                cam.queue_frame(frame)
            except Exception as exc:  # noqa: BLE001 - record frame requeue failures.
                errors.append(exc)
                done.set()

    camera.start_streaming(handler, buffer_count=5)
    try:
        if not done.wait(timeout_s):
            raise TimeoutError(f"received {complete_count} of {count} requested complete frames")
    finally:
        camera.stop_streaming()
    if errors:
        raise RuntimeError(f"stream callback failed: {errors[0]}") from errors[0]
    return frames, times, ids, incomplete


def _capture_simultaneous(cameras: list[Any], duration_s: float) -> dict[str, Any]:
    """Measure two selected cameras concurrently without retaining image buffers."""
    from vmbpy import FrameStatus

    states = {
        camera.get_id(): {"timestamps": [], "frame_ids": [], "incomplete_frames": 0, "errors": []} for camera in cameras
    }
    start = time.monotonic()
    done = threading.Event()

    def make_handler(camera_id: str):
        def handler(cam, _stream, frame):
            try:
                state = states[camera_id]
                if frame.get_status() == FrameStatus.Complete:
                    state["timestamps"].append(time.monotonic())
                    state["frame_ids"].append(int(frame.get_id()))
                    if time.monotonic() - start >= duration_s:
                        done.set()
                else:
                    state["incomplete_frames"] += 1
            except Exception as exc:  # noqa: BLE001 - capture SDK errors for operator report.
                states[camera_id]["errors"].append(str(exc))
                done.set()
            finally:
                try:
                    cam.queue_frame(frame)
                except Exception as exc:  # noqa: BLE001 - capture frame-requeue errors.
                    states[camera_id]["errors"].append(f"frame requeue: {exc}")
                    done.set()

        return handler

    started = []
    try:
        for camera in cameras:
            camera.start_streaming(make_handler(camera.get_id()), buffer_count=5)
            started.append(camera)
        if not done.wait(duration_s + 10):
            raise TimeoutError("simultaneous stream test exceeded its timeout")
    finally:
        for camera in reversed(started):
            camera.stop_streaming()

    results = {}
    camera_by_id = {camera.get_id(): camera for camera in cameras}
    for camera_id, state in states.items():
        timing = frame_timing_metrics(state["timestamps"], state["frame_ids"])
        try:
            current_roi = read_roi(camera_by_id[camera_id]).__dict__
        except Exception:  # noqa: BLE001 - ROI fields can be absent on some devices.
            current_roi = None
        results[camera_id] = {
            "roi": current_roi,
            "complete_frames": len(state["timestamps"]),
            "incomplete_frames": state["incomplete_frames"],
            "reported_frame_rate": inspect_feature(camera_by_id[camera_id], FEATURE_ALIASES["frame_rate"]).value,
            "callback_errors": state["errors"],
            **timing.__dict__,
        }
    return results


def _single_stream_test(camera: Any, args: argparse.Namespace) -> dict[str, Any]:
    if args.roi:
        return _roi_stream_test(camera, args)
    original_roi = None
    result: dict[str, Any] = {}
    try:
        if args.roi:
            width, height = _parse_roi_size(args.roi)
            original_roi = read_roi(camera)
            proposed = _centered_roi(camera, original_roi, width, height)
            print(f"Planned ROI write: {proposed}; original ROI: {original_roi}")
            apply_roi(camera, proposed)
        _frames, timestamps, ids, incomplete = _capture(
            camera, None, args.duration + 10, duration_s=args.duration, collect_images=False
        )
        timing = frame_timing_metrics(timestamps, ids)
        try:
            current_roi = read_roi(camera).__dict__
        except Exception:  # noqa: BLE001 - ROI fields are optional for non-camera device adapters.
            current_roi = None
        result["measurements"] = [
            {
                "roi": current_roi,
                "complete_frames": len(timestamps),
                "incomplete_frames": incomplete,
                "reported_frame_rate": inspect_feature(camera, FEATURE_ALIASES["frame_rate"]).value,
                **timing.__dict__,
            }
        ]
    finally:
        if original_roi is not None:
            try:
                apply_roi(camera, original_roi)
                result["roi_restoration_confirmed"] = read_roi(camera) == original_roi
            except Exception as exc:  # noqa: BLE001 - explicit restoration status is required.
                result["roi_restoration_confirmed"] = False
                result["roi_restoration_error"] = str(exc)
    return result


def _capability_report(capability: Any) -> dict[str, Any]:
    return {
        "available": capability.available,
        "readable": capability.readable,
        "writable": capability.writable,
        "value": report_value(capability.value),
        "minimum": report_value(capability.minimum),
        "maximum": report_value(capability.maximum),
        "increment": report_value(capability.increment),
        "unit": capability.unit,
    }


def _numeric_frame_rate(capability: Any, *, writable: bool = False) -> float:
    if (
        not capability.available
        or not capability.readable
        or (writable and not capability.writable)
        or isinstance(capability.value, bool)
        or not isinstance(capability.value, (int, float))
        or isinstance(capability.maximum, bool)
        or not isinstance(capability.maximum, (int, float))
        or isinstance(capability.minimum, bool)
        or not isinstance(capability.minimum, (int, float))
        or not np.isfinite(float(capability.minimum))
        or not np.isfinite(float(capability.maximum))
        or capability.maximum < capability.minimum
    ):
        access = "readable, writable, and numeric" if writable else "readable and numeric"
        raise RuntimeError(f"Acquisition frame-rate capability is not {access}")
    return float(capability.value)


def _roi_stream_test(camera: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Measure a centered ROI and optionally its maximum supported frame rate."""
    width, height = _parse_roi_size(args.roi)
    original_roi = read_roi(camera)
    original_rate = inspect_feature(camera, FEATURE_ALIASES["frame_rate"])
    original_enable = inspect_feature(camera, FEATURE_ALIASES["frame_rate_enable"])
    original_rate_value = _numeric_frame_rate(original_rate) if original_rate.available else None
    original_enable_value = original_enable.value if original_enable.available and original_enable.readable else None
    requested_roi = _centered_roi(camera, original_roi, width, height)
    results: dict[str, Any] = {
        "requested_roi": requested_roi.__dict__,
        "original_roi": original_roi.__dict__,
        "frame_rate_capabilities": {"original_roi": _capability_report(original_rate)},
    }
    restore_rate = False
    errors: list[str] = []
    roi_confirmed = False
    frame_rate_confirmed = False
    enable_confirmed = original_enable_value is None
    try:
        print(f"Planned ROI write: {requested_roi}; original ROI: {original_roi}")
        apply_roi(camera, requested_roi)
        reduced_rate = inspect_feature(camera, FEATURE_ALIASES["frame_rate"])
        if args.maximize_frame_rate_for_roi:
            if original_enable.available:
                if not original_enable.readable:
                    raise RuntimeError("Acquisition frame-rate enable state is not readable")
                if report_value(original_enable.value) is not True:
                    if not original_enable.writable:
                        raise RuntimeError("Acquisition frame-rate enable feature is not writable")
                    _set(camera, "frame_rate_enable", True)
                # Some cameras expose the writable frame-rate range only while enabled.
                reduced_rate = inspect_feature(camera, FEATURE_ALIASES["frame_rate"])
            _numeric_frame_rate(reduced_rate, writable=True)
            maximum = float(reduced_rate.maximum)
            print(f"Planned frame-rate write after ROI re-query: {maximum:g} FPS")
            restore_rate = True
            _set(camera, "frame_rate", maximum)
        results["frame_rate_capabilities"].update(
            {
                "reduced_roi": _capability_report(reduced_rate),
                "reduced_roi_maximum": report_value(reduced_rate.maximum),
                "original_frame_rate": report_value(original_rate_value),
                "original_enable": report_value(original_enable_value),
            }
        )
        _frames, timestamps, ids, incomplete = _capture(
            camera, None, args.duration + 10, duration_s=args.duration, collect_images=False
        )
        timing = frame_timing_metrics(timestamps, ids)
        actual_roi = read_roi(camera)
        results["measurements"] = [
            {
                "requested_roi": requested_roi.__dict__,
                "actual_roi": actual_roi.__dict__,
                "reported_frame_rate": report_value(inspect_feature(camera, FEATURE_ALIASES["frame_rate"]).value),
                "complete_frames": len(timestamps),
                "incomplete_frames": incomplete,
                **timing.__dict__,
            }
        ]
    finally:
        # Enable rate control before restoring a rate, lower it to a value legal
        # for the reduced ROI, restore the original ROI, then restore the exact
        # original rate and finally its original enable state.
        if args.maximize_frame_rate_for_roi and original_enable.available and original_enable_value is False:
            try:
                _set(camera, "frame_rate_enable", True)
            except Exception as exc:  # noqa: BLE001 - report every restoration problem.
                errors.append(f"enable frame-rate control for restoration: {exc}")
        if restore_rate and original_rate_value is not None:
            try:
                current_rate = inspect_feature(camera, FEATURE_ALIASES["frame_rate"])
                current_max = float(current_rate.maximum)
                current_min = float(current_rate.minimum)
                increment = float(current_rate.increment or 0)
                safe_rate = min(max(original_rate_value, current_min), current_max)
                if increment > 0:
                    safe_rate = current_min + np.floor((safe_rate - current_min) / increment) * increment
                _set(camera, "frame_rate", safe_rate)
            except Exception as exc:  # noqa: BLE001 - continue with ROI restoration.
                errors.append(f"safe frame-rate restore before ROI: {exc}")
        try:
            apply_roi(camera, original_roi)
            roi_confirmed = read_roi(camera) == original_roi
            if not roi_confirmed:
                errors.append("ROI readback did not match original ROI")
        except Exception as exc:  # noqa: BLE001 - report restoration failure explicitly.
            errors.append(f"ROI restoration: {exc}")
        if original_rate_value is not None:
            try:
                current_rate_value = _numeric_frame_rate(inspect_feature(camera, FEATURE_ALIASES["frame_rate"]))
                if not np.isclose(current_rate_value, original_rate_value, rtol=1e-6, atol=1e-6):
                    _set(camera, "frame_rate", original_rate_value)
                restored_rate = _numeric_frame_rate(inspect_feature(camera, FEATURE_ALIASES["frame_rate"]))
                frame_rate_confirmed = bool(np.isclose(restored_rate, original_rate_value, rtol=1e-6, atol=1e-6))
                if not frame_rate_confirmed:
                    errors.append("frame-rate readback did not match original value")
            except Exception as exc:  # noqa: BLE001 - preserve failure in the structured result.
                errors.append(f"frame-rate restoration: {exc}")
        if original_enable_value is not None:
            try:
                current_enable = inspect_feature(camera, FEATURE_ALIASES["frame_rate_enable"])
                if report_value(current_enable.value) != report_value(original_enable_value):
                    _set(camera, "frame_rate_enable", original_enable_value)
                restored_enable = inspect_feature(camera, FEATURE_ALIASES["frame_rate_enable"])
                enable_confirmed = report_value(restored_enable.value) == report_value(original_enable_value)
                if not enable_confirmed:
                    errors.append("frame-rate enable readback did not match original state")
            except Exception as exc:  # noqa: BLE001 - report restoration failure explicitly.
                enable_confirmed = False
                errors.append(f"frame-rate enable restoration: {exc}")
        if original_rate_value is None:
            frame_rate_confirmed = not args.maximize_frame_rate_for_roi
        results["restoration"] = {
            "roi_confirmed": roi_confirmed,
            "frame_rate_confirmed": frame_rate_confirmed,
            "frame_rate_enable_confirmed": enable_confirmed,
            "errors": errors,
        }
    return results


def _centered_roi(camera: Any, original: ROI, width: int, height: int) -> ROI:
    for logical in ("binning_horizontal", "binning_vertical"):
        cap = inspect_feature(camera, FEATURE_ALIASES[logical])
        if cap.available and (not cap.readable or str(cap.value) != "1"):
            raise ValueError(f"Centered ROI characterization requires {logical} to be readable and equal to 1")
    x_cap = inspect_feature(camera, FEATURE_ALIASES["offset_x"])
    y_cap = inspect_feature(camera, FEATURE_ALIASES["offset_y"])
    return ROI(
        width,
        height,
        _center_offset(x_cap, original.width + original.offset_x, width),
        _center_offset(y_cap, original.height + original.offset_y, height),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera-id",
        required=True,
        nargs="+",
        help="One exact camera ID, or two explicit IDs for a simultaneous stream test",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stream-test", action="store_true", help="Read-only stream pacing test")
    mode.add_argument("--quality-sweep", action="store_true", help="Exposure/gain cross-product test (writes settings)")
    mode.add_argument("--auto-once", choices=("exposure", "gain"), help="Measure existing one-shot auto behavior")
    parser.add_argument("--duration", type=float, default=10.0, help="Stream-test duration in seconds")
    parser.add_argument("--roi", help="Optional explicit acquisition ROI as WIDTHxHEIGHT (requires authorization)")
    parser.add_argument(
        "--maximize-frame-rate-for-roi",
        action="store_true",
        help="After applying --roi, set the queried maximum frame rate (requires explicit authorization)",
    )
    parser.add_argument("--frames", type=int, default=200, help="Complete frames per quality operating point")
    parser.add_argument("--saturation-threshold", type=float, default=250.0)
    parser.add_argument("--near-zero-threshold", type=float, default=2.0)
    parser.add_argument("--gain-values", help="Comma-separated queried-range Gain values")
    parser.add_argument("--exposure-values-us", help="Comma-separated queried-range exposure values in microseconds")
    parser.add_argument("--authorize-settings-changes", action="store_true", help="Required for quality sweep writes")
    parser.add_argument("--signal-roi", help="Optional analysis rectangle x,y,width,height")
    parser.add_argument("--background-roi", help="Optional analysis rectangle x,y,width,height")
    parser.add_argument("--centroid", action="store_true", help="Enable intensity-weighted centroid metrics")
    parser.add_argument("--output", help="Optional JSON output path")
    args = parser.parse_args()
    if args.duration <= 0 or not 1 <= args.frames <= 500:
        parser.error("duration must be positive and frames must be between 1 and 500")
    if args.maximize_frame_rate_for_roi and not args.stream_test:
        parser.error("--maximize-frame-rate-for-roi requires --stream-test")
    if args.maximize_frame_rate_for_roi and not args.roi:
        parser.error("--maximize-frame-rate-for-roi requires --roi WIDTHxHEIGHT")
    if args.maximize_frame_rate_for_roi and not args.authorize_settings_changes:
        parser.error("--maximize-frame-rate-for-roi requires --authorize-settings-changes")
    if args.maximize_frame_rate_for_roi and len(args.camera_id) != 1:
        parser.error("--maximize-frame-rate-for-roi supports exactly one camera ID")
    if (args.quality_sweep or args.auto_once or args.roi) and not args.authorize_settings_changes:
        parser.error("--quality-sweep, --auto-once, and --roi require --authorize-settings-changes")
    if args.roi and not args.stream_test:
        parser.error("--roi is only valid with --stream-test")
    if args.authorize_settings_changes and not (args.quality_sweep or args.auto_once or args.roi):
        parser.error("--authorize-settings-changes requires --quality-sweep, --auto-once, or --roi")
    if args.quality_sweep and (not args.gain_values or not args.exposure_values_us):
        parser.error("--quality-sweep requires both --gain-values and --exposure-values-us")
    if args.quality_sweep and len(args.gain_values.split(",")) * len(args.exposure_values_us.split(",")) > 25:
        parser.error("a quality sweep is limited to 25 operating points per run")
    if len(args.camera_id) > 1 and not args.stream_test:
        parser.error("multiple camera IDs are supported only for --stream-test")
    if len(args.camera_id) > 2:
        parser.error("at most two camera IDs may be supplied")

    try:
        from vmbpy import VmbSystem
    except Exception as exc:  # noqa: BLE001 - SDK binding/runtime import failures vary by install.
        print(f"VmbPy unavailable: {exc}", file=sys.stderr)
        return 2

    results: dict[str, Any] = {
        "camera_ids": args.camera_id,
        "mode": "quality-sweep"
        if args.quality_sweep
        else "auto-once"
        if args.auto_once
        else "stream-test"
        if args.stream_test
        else "read-only",
        "measurements": [],
    }
    gains = _parse_values(args.gain_values, "gain-values") if args.quality_sweep else []
    exposures = _parse_values(args.exposure_values_us, "exposure-values-us") if args.quality_sweep else []
    with VmbSystem.get_instance() as system, ExitStack() as stack:
        try:
            selected = wait_for_cameras_by_id(
                system,
                args.camera_id,
                on_wait=lambda: print(
                    "Waiting for camera ID(s) "
                    f"{', '.join(args.camera_id)} to become discoverable "
                    f"(timeout {DISCOVERY_TIMEOUT_SECONDS:g} s)...",
                    file=sys.stderr,
                ),
            )
        except CameraNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        cameras = [stack.enter_context(selected[camera_id]) for camera_id in args.camera_id]
        camera = cameras[0]
        results["camera_model"] = camera.get_model()
        results["camera_models"] = {candidate.get_id(): candidate.get_model() for candidate in cameras}
        if args.stream_test:
            for candidate in cameras:
                _verify_continuous_untriggered(candidate)
            if len(cameras) == 2:
                results["measurement_mode"] = "two-camera simultaneous"
                original_rois: dict[str, tuple[Any, ROI]] = {}
                try:
                    if args.roi:
                        width, height = _parse_roi_size(args.roi)
                        planned = []
                        for candidate in cameras:
                            original = read_roi(candidate)
                            proposed = _centered_roi(candidate, original, width, height)
                            original_rois[candidate.get_id()] = (candidate, original)
                            planned.append((candidate, proposed))
                        for candidate, proposed in planned:
                            print(f"Planned ROI write for {candidate.get_id()}: {proposed}")
                        for candidate, proposed in planned:
                            apply_roi(candidate, proposed)
                    results["simultaneous_measurements"] = _capture_simultaneous(cameras, args.duration)
                finally:
                    if original_rois:
                        restore_errors = []
                        for camera_id, (candidate, original) in original_rois.items():
                            try:
                                apply_roi(candidate, original)
                                if read_roi(candidate) != original:
                                    raise RuntimeError("ROI readback did not match original")
                            except Exception as exc:  # noqa: BLE001 - report each camera restore failure.
                                restore_errors.append(f"{camera_id}: {exc}")
                        results["roi_restoration_confirmed"] = not restore_errors
                        if restore_errors:
                            results["roi_restoration_errors"] = restore_errors
            else:
                results["measurement_mode"] = "single-camera"
                results.update(_single_stream_test(camera, args))
        elif args.quality_sweep:
            _verify_continuous_untriggered(camera)
            for value in gains:
                _validate_requested(camera, "gain", value)
            for value in exposures:
                _validate_requested(camera, "exposure", value)
            names = {}
            snapshot = {}
            for logical in ("gain", "exposure", "gain_auto", "exposure_auto"):
                names[logical], value = _read(camera, logical)
                snapshot[logical] = _restorable_value(value)
            print("Planned writes (after validation against actual queried ranges):")
            for exposure in exposures:
                for gain in gains:
                    print(f"  Exposure={exposure:g} us, Gain={gain:g}")
            restoration_ok = False
            try:
                for exposure in exposures:
                    for gain in gains:
                        _set(camera, "exposure_auto", "Off")
                        _set(camera, "gain_auto", "Off")
                        _set(camera, "exposure", exposure)
                        _set(camera, "gain", gain)
                        captured, timestamps, ids, incomplete = _capture(
                            camera, args.frames, max(60.0, args.frames * 0.2)
                        )
                        record = {
                            "exposure_us": exposure,
                            "gain": gain,
                            "image": analyze_frames(
                                captured,
                                signal_roi=_parse_roi(args.signal_roi),
                                background_roi=_parse_roi(args.background_roi),
                                saturation_threshold=args.saturation_threshold,
                                near_zero_threshold=args.near_zero_threshold,
                                centroid=args.centroid,
                            ),
                            "timing": frame_timing_metrics(timestamps, ids).__dict__,
                            "reported_frame_rate": inspect_feature(camera, FEATURE_ALIASES["frame_rate"]).value,
                            "incomplete_frames": incomplete,
                        }
                        results["measurements"].append(record)
            finally:
                errors = []
                for logical in ("exposure", "gain", "exposure_auto", "gain_auto"):
                    try:
                        _set(camera, logical, snapshot[logical])
                    except Exception as exc:  # noqa: BLE001 - report each restoration failure.
                        errors.append(f"{logical}: {exc}")
                restoration_ok = not errors
                results["restoration"] = {"confirmed": restoration_ok, "errors": errors}
        elif args.auto_once:
            _verify_continuous_untriggered(camera)
            snapshot = {}
            for logical in ("gain", "exposure", "gain_auto", "exposure_auto"):
                _, value = _read(camera, logical)
                snapshot[logical] = _restorable_value(value)
            results["initial_settings"] = {"gain": snapshot["gain"], "exposure_us": snapshot["exposure"]}
            selected_auto = "exposure_auto" if args.auto_once == "exposure" else "gain_auto"
            print(
                f"Planned write: {FEATURE_ALIASES[selected_auto][0]}=Once; "
                f"initial Exposure={snapshot['exposure']}, Gain={snapshot['gain']}"
            )
            try:
                _set(camera, selected_auto, "Once")
                _capture(camera, 30, 60.0, collect_images=False)
                captured, timestamps, ids, incomplete = _capture(camera, args.frames, max(60.0, args.frames * 0.2))
                _, final_exposure = _read(camera, "exposure")
                _, final_gain = _read(camera, "gain")
                results["auto_result"] = {
                    "exposure_us": final_exposure,
                    "gain": final_gain,
                    "image": analyze_frames(
                        captured,
                        saturation_threshold=args.saturation_threshold,
                        near_zero_threshold=args.near_zero_threshold,
                        centroid=args.centroid,
                    ),
                    "timing": frame_timing_metrics(timestamps, ids).__dict__,
                    "reported_frame_rate": inspect_feature(camera, FEATURE_ALIASES["frame_rate"]).value,
                    "incomplete_frames": incomplete,
                }
            finally:
                errors = []
                for logical in ("exposure", "gain", "exposure_auto", "gain_auto"):
                    try:
                        _set(camera, logical, snapshot[logical])
                    except Exception as exc:  # noqa: BLE001 - report each restoration failure.
                        errors.append(f"{logical}: {exc}")
                results["restoration"] = {"confirmed": not errors, "errors": errors}
        else:
            results["capabilities"] = inspect_camera(camera)
            results["acquisition_started"] = False
    output = json.dumps(results, indent=2, default=str)
    print(output)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output + "\n")
    restoration = results.get("restoration", {})
    restoration_confirmed = restoration.get(
        "confirmed",
        restoration.get("roi_confirmed", True)
        and restoration.get("frame_rate_confirmed", True)
        and restoration.get("frame_rate_enable_confirmed", True),
    )
    roi_restoration_confirmed = results.get("roi_restoration_confirmed", True)
    return 0 if restoration_confirmed and roi_restoration_confirmed else 3


def _parse_roi(text: str | None) -> tuple[int, int, int, int] | None:
    if text is None:
        return None
    try:
        values = tuple(int(value.strip()) for value in text.split(","))
    except ValueError as exc:
        raise ValueError("ROI must be x,y,width,height") from exc
    if len(values) != 4:
        raise ValueError("ROI must be x,y,width,height")
    return values


def _parse_roi_size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(value) for value in text.lower().split("x", maxsplit=1))
    except (ValueError, TypeError) as exc:
        raise ValueError("ROI must use WIDTHxHEIGHT, for example 1292x480") from exc
    return width, height


def _center_offset(cap, current_extent: int, requested_extent: int) -> int:
    minimum = int(cap.minimum or 0)
    increment = max(1, int(cap.increment or 1))
    target = minimum + (current_extent - requested_extent) // 2
    return minimum + round((target - minimum) / increment) * increment


if __name__ == "__main__":
    raise SystemExit(main())
