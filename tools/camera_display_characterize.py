"""Measure the real IOPanel camera conversion and Qt presentation pipeline.

This operator-supervised diagnostic requires one explicit physical camera ID.
It uses the normal VimbaCam startup path, snapshots features that path may
change, and restores them after the measurement. It never initializes lab
motion/control hardware.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Slot
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_model import CameraConfig
from hardware.camera import VimbaCam
from hardware.camera_capabilities import (
    FEATURE_ALIASES,
    ROI,
    apply_roi,
    centered_offset,
    find_feature,
    inspect_feature,
    read_roi,
    report_value,
)
from hardware.camera_discovery import DISCOVERY_TIMEOUT_SECONDS, wait_for_cameras_by_id
from ui.camera_widgets import CameraPanel


def timing_stats(samples: list[float]) -> dict[str, float | int | None]:
    """Summarize intervals between monotonic event timestamps in seconds."""
    intervals = [b - a for a, b in pairwise(samples) if b >= a]
    if not intervals:
        return {
            "count": len(samples),
            "mean_seconds": None,
            "median_seconds": None,
            "p95_seconds": None,
            "p99_seconds": None,
            "max_seconds": None,
        }
    return {
        "count": len(samples),
        "mean_seconds": statistics.fmean(intervals),
        "median_seconds": statistics.median(intervals),
        "p95_seconds": float(np.percentile(intervals, 95)),
        "p99_seconds": float(np.percentile(intervals, 99)),
        "max_seconds": max(intervals),
    }


def rate_fps(samples: list[float]) -> float:
    return (len(samples) - 1) / (samples[-1] - samples[0]) if len(samples) > 1 and samples[-1] > samples[0] else 0.0


def counter_delta(before: int, after: int) -> int:
    return max(0, after - before)


def coalescing_fraction(coalesced: int, submitted: int) -> float:
    return coalesced / submitted if submitted else 0.0


class InstrumentedCameraPanel(CameraPanel):
    """Count accepted images and production presentation calls."""

    def __init__(self, *args: Any, **kwargs: Any):
        self.presentation_timestamps: list[float] = []
        self.accepted_image_timestamps: list[float] = []
        super().__init__(*args, **kwargs)

    def _accept_converted_image(self, q_img):
        self.accepted_image_timestamps.append(time.monotonic())
        super()._accept_converted_image(q_img)

    def _display_converted_image(self, q_img):
        self.presentation_timestamps.append(time.monotonic())
        super()._display_converted_image(q_img)


class PaintCounter(QObject):
    def __init__(self):
        super().__init__()
        self.timestamps: list[float] = []

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Paint:
            self.timestamps.append(time.monotonic())
        return False


class AcquisitionCounter(QObject):
    """Timestamp normal VimbaCam.new_frame signal delivery."""

    def __init__(self):
        super().__init__()
        self.timestamps: list[float] = []

    @Slot(np.ndarray)
    def record(self, _frame: np.ndarray) -> None:
        self.timestamps.append(time.monotonic())


def parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("size dimensions must be positive")
    return width, height


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-id", required=True, help="Exact physical Vimba camera ID")
    parser.add_argument("--roi", type=parse_size, help="Centered ROI WIDTHxHEIGHT (writes camera settings)")
    parser.add_argument("--maximize-frame-rate-for-roi", action="store_true")
    parser.add_argument("--authorize-settings-changes", action="store_true")
    parser.add_argument("--window-size", type=parse_size, default=(960, 720))
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.duration <= 0:
        parser.error("--duration must be positive")
    if args.maximize_frame_rate_for_roi and not args.roi:
        parser.error("--maximize-frame-rate-for-roi requires --roi")
    if args.roi and not args.authorize_settings_changes:
        parser.error("--roi requires --authorize-settings-changes")
    if (args.authorize_settings_changes or args.maximize_frame_rate_for_roi) and not args.roi:
        parser.error("settings-change options require --roi")


def _set_feature(camera: Any, logical: str, value: Any) -> Any:
    name, feature = find_feature(camera, _aliases(logical))
    if feature is None or not feature.is_writeable():
        raise RuntimeError(f"Feature {logical} is not writable")
    feature.set(value)
    actual = feature.get()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not np.isclose(float(actual), float(value), rtol=1e-6, atol=1e-6):
            raise RuntimeError(f"{name} readback {actual} differs from requested {value}")
    elif report_value(actual) != report_value(value):
        raise RuntimeError(f"{name} readback {report_value(actual)!r} differs from {report_value(value)!r}")
    return actual


def _feature_is_enabled(value: Any) -> bool:
    """Interpret GenICam bools and semantic enum readbacks without identity assumptions."""
    value = report_value(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value).casefold() in {"on", "enabled", "true", "1"}


def _enabled_feature_value(capability: Any) -> Any:
    """Choose an enabled value matching the feature's exposed representation."""
    current = capability.value
    if isinstance(report_value(current), bool):
        return True
    for value in capability.values:
        if _feature_is_enabled(value):
            return value
    return "On" if isinstance(report_value(current), str) else True


def _centered_roi(camera: Any, size: tuple[int, int], original: ROI) -> ROI:
    width, height = size
    x_cap = inspect_feature(camera, FEATURE_ALIASES["offset_x"])
    y_cap = inspect_feature(camera, FEATURE_ALIASES["offset_y"])
    width_sensor = int(inspect_feature(camera, FEATURE_ALIASES["width_max"]).value)
    height_sensor = int(inspect_feature(camera, FEATURE_ALIASES["height_max"]).value)
    return ROI(
        width, height, centered_offset(x_cap, width_sensor, width), centered_offset(y_cap, height_sensor, height)
    )


PRODUCTION_OPEN_FEATURES = (
    "acquisition_mode",
    "trigger_mode",
    "exposure_auto",
    "gain_auto",
    "gamma",
    "pixel_format",
)

DIAGNOSTIC_FEATURE_ALIASES = {**FEATURE_ALIASES, "gamma": ("Gamma",)}


def _aliases(logical: str) -> tuple[str, ...]:
    return DIAGNOSTIC_FEATURE_ALIASES.get(logical, (logical,))


def _feature_snapshot(camera: Any, logical: str) -> dict[str, Any]:
    capability = inspect_feature(camera, _aliases(logical))
    entry: dict[str, Any] = {
        "available": capability.available,
        "readable": capability.available and capability.readable,
        "value": None,
    }
    if not entry["readable"]:
        return entry
    value = capability.value
    if logical in ("frame_rate", "gamma"):
        try:
            value = float(value)
        except (TypeError, ValueError):
            entry["readable"] = False
            return entry
    else:
        # Store semantic GenICam values (for example, "Off" or "Mono8"), not
        # native enum wrapper instances tied to the configuration handle.
        value = report_value(value)
    entry["value"] = value
    return entry


def _snapshot_settings(camera: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"roi": read_roi(camera)}
    for logical in ("frame_rate", "frame_rate_enable", *PRODUCTION_OPEN_FEATURES):
        result[logical] = _feature_snapshot(camera, logical)
    return result


def _restore_settings(camera: Any, snapshot: dict[str, Any], changed_rate: bool) -> dict[str, Any]:
    """Restore all captured settings independently and verify each by readback.

    Restore pixel format before ROI because the format can affect legal geometry
    and rate ranges. If the requested run changed frame rate, first lower it to
    a value legal in the current configuration; after restoring format and ROI,
    apply the exact saved rate. Independent startup selectors and gamma follow.
    The original frame-rate enable state is restored last.
    """
    errors: list[str] = []
    unavailable: list[str] = []
    fields: dict[str, bool | None] = {"roi_confirmed": False}

    def attempt(label: str, operation) -> None:
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - keep attempting later restores
            errors.append(f"{label}: {exc}")

    def captured(logical: str) -> Any | None:
        value = snapshot.get(logical)
        if not isinstance(value, dict) or not value.get("available") or not value.get("readable"):
            unavailable.append(logical)
            return None
        return value["value"]

    saved_rate = captured("frame_rate")
    saved_enable = captured("frame_rate_enable")
    saved_pixel_format = captured("pixel_format")

    if changed_rate and saved_enable is not None:

        def enable_rate_control() -> None:
            cap = inspect_feature(camera, FEATURE_ALIASES["frame_rate_enable"])
            if not cap.available or not cap.readable:
                raise RuntimeError("frame-rate enable readback unavailable")
            if not _feature_is_enabled(cap.value):
                _set_feature(camera, "frame_rate_enable", _enabled_feature_value(cap))
            verified = inspect_feature(camera, FEATURE_ALIASES["frame_rate_enable"])
            if not verified.readable or not _feature_is_enabled(verified.value):
                raise RuntimeError("frame-rate control did not enable")

        attempt("enable frame-rate control for restoration", enable_rate_control)

    if changed_rate and saved_rate is not None:

        def set_safe_rate() -> None:
            cap = inspect_feature(camera, FEATURE_ALIASES["frame_rate"])
            if not cap.available or not cap.writable or cap.minimum is None or cap.maximum is None:
                raise RuntimeError("current frame-rate range is unavailable")
            minimum = float(cap.minimum)
            safe_rate = min(max(float(saved_rate), minimum), float(cap.maximum))
            increment = float(cap.increment or 0)
            if increment > 0:
                safe_rate = minimum + np.floor((safe_rate - minimum) / increment) * increment
            current = cap.value
            if current is None or not np.isclose(float(current), safe_rate, rtol=1e-6, atol=1e-6):
                _set_feature(camera, "frame_rate", safe_rate)
            verified = inspect_feature(camera, FEATURE_ALIASES["frame_rate"])
            if not verified.readable or not np.isclose(float(verified.value), safe_rate, rtol=1e-6, atol=1e-6):
                raise RuntimeError("safe intermediate frame-rate readback mismatch")

        attempt("safe frame-rate before restoring format/ROI", set_safe_rate)

    def restore_feature(logical: str, saved: Any) -> None:
        cap = inspect_feature(camera, _aliases(logical))
        if not cap.available or not cap.readable:
            raise RuntimeError("feature is unavailable or unreadable during restoration")
        current = report_value(cap.value)
        if logical in ("gamma", "frame_rate"):
            equal = bool(np.isclose(float(cap.value), float(saved), rtol=1e-6, atol=1e-6))
        else:
            equal = current == report_value(saved)
        if not equal:
            _set_feature(camera, logical, saved)
        verified = inspect_feature(camera, _aliases(logical))
        if not verified.readable:
            raise RuntimeError("readback unavailable after restoration")
        if logical in ("gamma", "frame_rate"):
            confirmed = bool(np.isclose(float(verified.value), float(saved), rtol=1e-6, atol=1e-6))
        else:
            confirmed = report_value(verified.value) == report_value(saved)
        if not confirmed:
            raise RuntimeError(
                f"readback {report_value(verified.value)!r} does not match saved {report_value(saved)!r}"
            )

    def restore_roi() -> None:
        if read_roi(camera) != snapshot["roi"]:
            apply_roi(camera, snapshot["roi"])
        if read_roi(camera) != snapshot["roi"]:
            raise RuntimeError("ROI readback did not match saved ROI")

    if saved_pixel_format is not None:
        attempt("pixel format restoration", lambda: restore_feature("pixel_format", saved_pixel_format))
    attempt("ROI restoration", restore_roi)

    if saved_rate is not None:
        if changed_rate:
            attempt("exact frame-rate restoration", lambda: restore_feature("frame_rate", saved_rate))
        else:
            # VimbaCam startup does not touch frame rate; confirm its unchanged value.
            attempt("frame-rate verification", lambda: restore_feature("frame_rate", saved_rate))
    for logical in PRODUCTION_OPEN_FEATURES:
        if logical == "pixel_format":
            continue
        saved = captured(logical)
        flag = f"{logical}_confirmed"
        if saved is None:
            fields[flag] = None
            continue
        before_errors = len(errors)
        attempt(f"{logical} restoration", lambda logical=logical, saved=saved: restore_feature(logical, saved))
        fields[flag] = len(errors) == before_errors

    if saved_enable is not None:
        # A saved disabled state must not block the exact rate write; restore it last.
        attempt("frame-rate enable restoration", lambda: restore_feature("frame_rate_enable", saved_enable))

    # Independent final readbacks make confirmations describe the final state,
    # including when an earlier operation reported a write or readback error.
    fields["roi_confirmed"] = False
    attempt("final ROI verification", lambda: fields.__setitem__("roi_confirmed", read_roi(camera) == snapshot["roi"]))
    for logical, field in (
        ("frame_rate", "frame_rate_confirmed"),
        ("frame_rate_enable", "frame_rate_enable_confirmed"),
        ("pixel_format", "pixel_format_confirmed"),
        ("acquisition_mode", "acquisition_mode_confirmed"),
        ("trigger_mode", "trigger_mode_confirmed"),
        ("exposure_auto", "exposure_auto_confirmed"),
        ("gain_auto", "gain_auto_confirmed"),
        ("gamma", "gamma_confirmed"),
    ):
        saved = snapshot.get(logical)
        if not isinstance(saved, dict) or not saved.get("available") or not saved.get("readable"):
            fields[field] = None
            continue
        before_errors = len(errors)
        attempt(
            f"final {logical} verification",
            lambda logical=logical, value=saved["value"]: verify_feature(camera, logical, value),
        )
        fields[field] = len(errors) == before_errors
    return {**fields, "unavailable_features": sorted(set(unavailable)), "errors": errors}


def verify_feature(camera: Any, logical: str, saved: Any) -> None:
    cap = inspect_feature(camera, _aliases(logical))
    if not cap.available or not cap.readable:
        raise RuntimeError("readback unavailable")
    if logical in ("gamma", "frame_rate"):
        matches = bool(np.isclose(float(cap.value), float(saved), rtol=1e-6, atol=1e-6))
    else:
        matches = report_value(cap.value) == report_value(saved)
    if not matches:
        raise RuntimeError(f"readback {report_value(cap.value)!r} does not match saved {report_value(saved)!r}")


def _collect_metrics(
    panel: CameraPanel,
    paint: PaintCounter,
    heartbeat: list[float],
    before: dict[str, int],
    acquisition: list[float],
    fps_samples: list[float],
    started: float,
    wall: float,
    cpu: float,
    dims: dict[str, Any],
) -> dict[str, Any]:
    worker = panel.conversion_worker
    submitted = counter_delta(before["submitted"], worker.submitted_frames if worker else before["submitted"])
    converted = counter_delta(before["converted"], worker.converted_frames if worker else before["converted"])
    conversion_fps = converted / wall if wall else 0.0
    presentation = panel.presentation_timestamps if isinstance(panel, InstrumentedCameraPanel) else []
    return {
        **dims,
        "acquisition": {
            "fps_samples": fps_samples,
            "mean_fps": statistics.fmean(fps_samples) if fps_samples else 0.0,
            "median_fps": statistics.median(fps_samples) if fps_samples else 0.0,
            "camera_frame_signals": len(acquisition),
            "camera_acquisition_fps": rate_fps(acquisition),
        },
        "conversion": {
            "submitted": submitted,
            "converted": converted,
            "coalesced": counter_delta(before["coalesced"], worker.coalesced_frames if worker else before["coalesced"]),
            "conversion_fps": conversion_fps,
            "coalescing_fraction": coalescing_fraction(
                counter_delta(before["coalesced"], worker.coalesced_frames if worker else before["coalesced"]),
                submitted,
            ),
            "max_pending_frames": worker.max_pending_frames if worker else 0,
            "max_pending_frames_at_most_one": (worker.max_pending_frames <= 1) if worker else True,
        },
        "presentation": {
            "accepted_images": counter_delta(
                before["accepted_images"],
                len(panel.accepted_image_timestamps)
                if isinstance(panel, InstrumentedCameraPanel)
                else before["accepted_images"],
            ),
            "coalesced_images": counter_delta(before["coalesced_images"], panel.coalesced_images),
            "presentation_coalescing_fraction": coalescing_fraction(
                counter_delta(before["coalesced_images"], panel.coalesced_images),
                counter_delta(
                    before["accepted_images"],
                    len(panel.accepted_image_timestamps)
                    if isinstance(panel, InstrumentedCameraPanel)
                    else before["accepted_images"],
                ),
            ),
            "presentation_calls": len(presentation),
            "presentation_call_fps": rate_fps(presentation),
            "max_pending_images": panel.max_pending_images,
            "max_pending_images_at_most_one": panel.max_pending_images <= 1,
        },
        "paint": {
            "count": len(paint.timestamps),
            "qt_paint_event_fps": rate_fps(paint.timestamps),
            "timing": timing_stats(paint.timestamps),
        },
        "event_loop": {"heartbeat": timing_stats(heartbeat)},
        "process": {
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "cpu_wall_ratio": cpu / wall if wall else 0.0,
            "interpretation": "Process CPU time divided by wall time; not a machine-wide profiler.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    try:
        from vmbpy import VmbSystem
    except Exception as exc:  # noqa: BLE001
        print(f"VmbPy unavailable: {exc}", file=sys.stderr)
        return 2
    app = QApplication.instance() or QApplication(sys.argv[:1])
    camera: VimbaCam | None = None
    panel: InstrumentedCameraPanel | None = None
    system = VmbSystem.get_instance()
    system_entered = False
    camera_closed = False
    thread_stopped = False
    system_exited = False
    snapshot = None
    restoration = {
        "roi_confirmed": None,
        "frame_rate_confirmed": None,
        "frame_rate_enable_confirmed": None,
        "acquisition_mode_confirmed": None,
        "trigger_mode_confirmed": None,
        "exposure_auto_confirmed": None,
        "gain_auto_confirmed": None,
        "gamma_confirmed": None,
        "pixel_format_confirmed": None,
        "unavailable_features": [],
        "errors": [],
    }
    changed_rate = False
    result: dict[str, Any] = {
        "repository_sha": _git_sha(),
        "timestamp": datetime.now(UTC).isoformat(),
        "camera_id": args.camera_id,
        "measurement_error": None,
        "restoration": restoration,
    }
    config_camera = None
    try:
        system.__enter__()
        system_entered = True
        selected = wait_for_cameras_by_id(
            system,
            [args.camera_id],
            on_wait=lambda: print(
                f"Waiting up to {DISCOVERY_TIMEOUT_SECONDS:g}s for exact camera ID {args.camera_id}", file=sys.stderr
            ),
        )
        config_camera = selected[args.camera_id]
        config_camera.__enter__()
        result["camera_model"] = config_camera.get_model()
        snapshot = _snapshot_settings(config_camera)
        if args.roi:
            target = _centered_roi(config_camera, args.roi, snapshot["roi"])
            print(f"Planned ROI write: {target}; original ROI: {snapshot['roi']}")
            apply_roi(config_camera, target)
            if read_roi(config_camera) != target:
                raise RuntimeError("ROI readback does not match requested ROI")
            if args.maximize_frame_rate_for_roi:
                cap = inspect_feature(config_camera, FEATURE_ALIASES["frame_rate"])
                frame_rate_enable_snapshot = snapshot["frame_rate_enable"]
                if frame_rate_enable_snapshot["available"]:
                    enable = inspect_feature(config_camera, FEATURE_ALIASES["frame_rate_enable"])
                    if not frame_rate_enable_snapshot["readable"] or not enable.readable:
                        raise RuntimeError("Frame-rate enable state is not readable")
                    if not _feature_is_enabled(enable.value):
                        _set_feature(config_camera, "frame_rate_enable", _enabled_feature_value(enable))
                        enable = inspect_feature(config_camera, FEATURE_ALIASES["frame_rate_enable"])
                        if not enable.readable or not _feature_is_enabled(enable.value):
                            raise RuntimeError("Acquisition frame-rate control did not enable")
                cap = inspect_feature(config_camera, FEATURE_ALIASES["frame_rate"])
                if not cap.available or not cap.writable or cap.maximum is None:
                    raise RuntimeError("ROI-dependent maximum frame rate is unavailable")
                _set_feature(config_camera, "frame_rate", float(cap.maximum))
                changed_rate = True
        configured_roi = read_roi(config_camera)
        configured_rate = inspect_feature(config_camera, FEATURE_ALIASES["frame_rate"])
        result["configuration_readback"] = {
            "roi": configured_roi.__dict__,
            "reported_acquisition_frame_rate": report_value(configured_rate.value),
        }
        config_camera.__exit__(None, None, None)
        config_camera = None

        camera = VimbaCam(args.camera_id, camera_name=str(result["camera_model"]))
        if not camera.open():
            raise RuntimeError("VimbaCam could not open the exact requested camera")
        actual_roi = camera.get_roi()
        actual_rate = camera.get_frame_rate_capability()
        actual_rate_value = report_value(actual_rate["feature"].value) if actual_rate else None
        if actual_roi != configured_roi or report_value(actual_rate_value) != report_value(configured_rate.value):
            raise RuntimeError("VimbaCam readback did not match the configured ROI/frame rate")
        panel = InstrumentedCameraPanel(
            None, str(result["camera_model"]), CameraConfig(identifier=args.camera_id, name=str(result["camera_model"]))
        )
        panel.set_camera(camera)
        width, height = args.window_size
        panel.resize(width, height)
        panel.show()
        paint = PaintCounter()
        panel.video_label.installEventFilter(paint)
        heartbeat: list[float] = []
        timer = QTimer(panel)
        timer.setInterval(16)
        timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
        timer.start()
        fps_samples: list[float] = []
        camera.fps_updated.connect(lambda fps: fps_samples.append(float(fps)))
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and (
            camera.frame_monitor.get_fps() <= 0 or panel._latest_pixmap is None or panel.conversion_worker is None
        ):
            app.processEvents()
            time.sleep(0.005)
        if camera.frame_monitor.get_fps() <= 0 or panel._latest_pixmap is None or panel.conversion_worker is None:
            raise RuntimeError("Warmup did not observe acquisition, conversion worker, and a displayed pixmap")
        worker = panel.conversion_worker
        before = {
            "submitted": worker.submitted_frames,
            "converted": worker.converted_frames,
            "coalesced": worker.coalesced_frames,
            "coalesced_images": panel.coalesced_images,
            "accepted_images": len(panel.accepted_image_timestamps),
        }
        acquisition_counter = AcquisitionCounter()
        camera.new_frame.connect(acquisition_counter.record, Qt.ConnectionType.DirectConnection)
        acquisition = acquisition_counter.timestamps
        panel.presentation_timestamps.clear()
        paint.timestamps.clear()
        heartbeat.clear()
        fps_samples.clear()
        wall_start, cpu_start = time.monotonic(), time.process_time()
        end = wall_start + args.duration
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.001)
        wall, cpu = time.monotonic() - wall_start, time.process_time() - cpu_start
        dimensions = {
            "requested_outer_window_size": list(args.window_size),
            "actual_camera_panel_size": [panel.width(), panel.height()],
            "actual_video_label_size": [panel.video_label.width(), panel.video_label.height()],
            "acquired_image_dimensions": [configured_roi.width, configured_roi.height],
        }
        result["metrics"] = _collect_metrics(
            panel, paint, heartbeat, before, acquisition, fps_samples, wall_start, wall, cpu, dimensions
        )
        timer.stop()
        try:
            camera.new_frame.disconnect(acquisition_counter.record)
        except (TypeError, RuntimeError):
            pass
    except Exception as exc:  # noqa: BLE001 - partial evidence is still serialized below
        result["measurement_error"] = str(exc)
    finally:
        if panel is not None:
            try:
                if panel.conversion_thread is not None:
                    panel.close()
                    app.processEvents()
                    thread_stopped = not panel.conversion_thread.isRunning()
                else:
                    panel.close()
                    thread_stopped = True
                if not thread_stopped:
                    result.setdefault("cleanup_errors", []).append("CameraPanel conversion thread did not stop")
            except Exception as exc:  # noqa: BLE001 - finish remaining cleanup steps
                result.setdefault("cleanup_errors", []).append(f"CameraPanel: {exc}")
        if camera is not None:
            try:
                camera.close()
                camera_closed = camera.device is None and not camera.is_streaming
                if not camera_closed:
                    result.setdefault("cleanup_errors", []).append("VimbaCam remained open after close")
            except Exception as exc:  # noqa: BLE001 - finish remaining cleanup steps
                result.setdefault("cleanup_errors", []).append(f"VimbaCam: {exc}")
        if config_camera is not None:
            try:
                config_camera.__exit__(None, None, None)
                config_camera = None
            except Exception as exc:  # noqa: BLE001 - finish restoration/cleanup steps
                result.setdefault("cleanup_errors", []).append(f"configuration camera close: {exc}")
        if snapshot is not None and system_entered:
            try:
                restored = wait_for_cameras_by_id(system, [args.camera_id])[args.camera_id]
                restored.__enter__()
                try:
                    restoration = _restore_settings(restored, snapshot, changed_rate)
                finally:
                    restored.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - report restoration failure distinctly
                restoration = restoration_failure(f"restoration handle: {exc}")
            result["restoration"] = restoration
        if system_entered:
            try:
                system.__exit__(None, None, None)
                system_exited = True
            except Exception as exc:  # noqa: BLE001 - report system shutdown failure
                result.setdefault("cleanup_errors", []).append(f"VmbSystem exit: {exc}")
        result["cleanup"] = {
            "camera_closed": camera_closed,
            "conversion_thread_stopped": thread_stopped,
            "VmbSystem_exited": system_exited,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, default=report_value), encoding="utf-8")
    return diagnostic_exit_status(result)


def diagnostic_exit_status(result: dict[str, Any]) -> int:
    """Return success only when measurement, restoration, and cleanup all passed."""
    cleanup = result.get("cleanup", {})
    return (
        0
        if (
            result.get("measurement_error") is None
            and not result.get("restoration", {}).get("errors")
            and not result.get("cleanup_errors")
            and cleanup.get("camera_closed") is True
            and cleanup.get("conversion_thread_stopped") is True
            and cleanup.get("VmbSystem_exited") is True
        )
        else 1
    )


def restoration_failure(message: str) -> dict[str, Any]:
    """Build a complete report when the restoration camera cannot be opened."""
    return {
        "roi_confirmed": False,
        "frame_rate_confirmed": False,
        "frame_rate_enable_confirmed": False,
        "acquisition_mode_confirmed": False,
        "trigger_mode_confirmed": False,
        "exposure_auto_confirmed": False,
        "gain_auto_confirmed": False,
        "gamma_confirmed": False,
        "pixel_format_confirmed": False,
        "unavailable_features": [],
        "errors": [message],
    }


def _git_sha() -> str | None:
    import subprocess

    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
