"""Run a staged, read-only baseline against explicitly selected Vimba cameras."""

# Vendor SDK boundaries and best-effort diagnostics intentionally preserve broad
# exception details in evidence rather than assuming one binding exception type.
# ruff: noqa: BLE001

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hardware.camera_analysis import frame_timing_metrics
from hardware.camera_capabilities import FEATURE_ALIASES, inspect_camera, inspect_feature, read_roi, report_value
from hardware.camera_discovery import DISCOVERY_TIMEOUT_SECONDS, CameraNotFoundError, wait_for_cameras_by_id


def parse_camera(value: str) -> tuple[str, str]:
    camera_id, sep, label = value.partition("=")
    if not camera_id or (sep and not label):
        raise argparse.ArgumentTypeError("camera must be CAMERA_ID or CAMERA_ID=LABEL")
    return camera_id, label if sep else camera_id


def environment_report() -> dict[str, Any]:
    from vmbpy import VmbSystem

    def version(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    runtime: dict[str, Any] = {}
    with VmbSystem.get_instance() as system:
        for key, method in (
            ("vimba_x", "get_version"),
            ("vmbc", "get_vmbc_version"),
            ("transform", "get_vmb_image_transform_version"),
        ):
            fn = getattr(system, method, None)
            try:
                runtime[key] = str(fn()) if callable(fn) else "unavailable"
            except Exception as exc:  # SDK runtime introspection differs among releases.
                runtime[key] = f"unavailable: {exc}"
    version_text = runtime.get("vimba_x", "")
    for key, pattern in (
        ("vmbc", r"using VmbC:\s*([^,)]+)"),
        ("transform", r"VmbImageTransform:\s*([^,)]+)"),
    ):
        match = re.search(pattern, str(version_text))
        if match:
            runtime[key] = match.group(1).strip()
    return {
        "executable": sys.executable,
        "python": platform.python_version(),
        "architecture": platform.architecture()[0],
        "vmbpy": version("vmbpy"),
        "runtime": runtime,
    }


def discover_exact(system: Any, camera_ids: list[str], *, timeout: float = DISCOVERY_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Poll exact IDs under one deadline and report per-ID arrival times."""
    started = time.monotonic()
    arrivals: dict[str, float] = {}
    visible_ids: tuple[str, ...] = ()

    def record_visible(visible_ids: tuple[str, ...]) -> None:
        nonlocal last_visible_ids
        last_visible_ids = visible_ids
        elapsed = time.monotonic() - started
        for camera_id in camera_ids:
            if camera_id in visible_ids:
                arrivals.setdefault(camera_id, elapsed)

    last_visible_ids: tuple[str, ...] = ()
    try:
        selected = wait_for_cameras_by_id(system, camera_ids, timeout_s=timeout, on_visible=record_visible)
        visible_ids = last_visible_ids
        missing: list[str] = []
        status = "PASS"
    except CameraNotFoundError as exc:
        visible_ids = exc.visible_ids
        selected = {}
        missing = list(exc.missing_ids)
        status = "FAIL"
    return {
        "status": status,
        "requested_ids": camera_ids,
        "time_to_discovery_seconds": arrivals,
        "visible_ids": sorted(visible_ids),
        "missing_ids": missing,
        "elapsed_seconds": time.monotonic() - started,
        "cameras": selected,
    }


def _feature_name_value(camera: Any, logical: str) -> Any:
    cap = inspect_feature(camera, FEATURE_ALIASES[logical])
    return report_value(cap.value)


def _assert_stream_ready(camera: Any) -> None:
    acquisition = str(_feature_name_value(camera, "acquisition_mode"))
    trigger = str(_feature_name_value(camera, "trigger_mode"))
    if acquisition.casefold() != "continuous" or trigger.casefold() != "off":
        raise RuntimeError(
            f"Streaming requires existing AcquisitionMode=Continuous and TriggerMode=Off; "
            f"read {acquisition!r} and {trigger!r}. No settings changed."
        )


def capture_timing(camera: Any, duration_s: float) -> dict[str, Any]:
    """Measure frame timing without retaining images or changing camera features."""
    from vmbpy import FrameStatus

    timestamps: list[float] = []
    frame_ids: list[int] = []
    incomplete = 0
    errors: list[str] = []
    started = time.monotonic()
    finished = threading.Event()

    def handler(cam: Any, _stream: Any, frame: Any) -> None:
        nonlocal incomplete
        try:
            if frame.get_status() == FrameStatus.Complete:
                timestamps.append(time.monotonic())
                frame_ids.append(int(frame.get_id()))
            else:
                incomplete += 1
            if time.monotonic() - started >= duration_s:
                finished.set()
        except Exception as exc:
            errors.append(str(exc))
            finished.set()
        finally:
            try:
                cam.queue_frame(frame)
            except Exception as exc:
                errors.append(f"frame requeue failed: {exc}")
                finished.set()

    stream_started = False
    start_time = time.monotonic()
    try:
        camera.start_streaming(handler, buffer_count=5)
        stream_started = True
        if not finished.wait(duration_s + 10):
            errors.append("stream duration timed out")
    finally:
        if stream_started:
            camera.stop_streaming()
    end_time = time.monotonic()
    timing = frame_timing_metrics(timestamps, frame_ids)
    roi = None
    try:
        roi = read_roi(camera).__dict__
    except Exception:
        roi = None
    reported_fps = _feature_name_value(camera, "frame_rate")
    observations = bool(incomplete or timing.frame_id_gaps)
    if (
        isinstance(reported_fps, (int, float))
        and timing.measured_fps is not None
        and timing.measured_fps < reported_fps
    ):
        observations = True
    return {
        "status": "FAIL" if errors or not timestamps else "PASS WITH OBSERVATIONS" if observations else "PASS",
        "start_monotonic": start_time,
        "end_monotonic": end_time,
        "duration_seconds": end_time - start_time,
        "roi": roi,
        "complete_frames": len(timestamps),
        "incomplete_frames": incomplete,
        "reported_frame_rate": reported_fps,
        "callback_errors": errors,
        **timing.__dict__,
    }


def network_audit() -> dict[str, Any]:
    """Collect best-effort Windows adapter inventory using read-only cmdlets."""
    if sys.platform != "win32":
        return {"status": "unavailable", "reason": "Windows-only audit"}
    commands = {
        "adapters": "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,LinkSpeed,MacAddress | ConvertTo-Json -Depth 4",
        "ip": "Get-NetIPInterface -AddressFamily IPv4 | Select-Object InterfaceAlias,NlMtu,ConnectionState | ConvertTo-Json -Depth 4",
        "properties": "Get-NetAdapterAdvancedProperty | Select-Object Name,DisplayName,DisplayValue | ConvertTo-Json -Depth 4",
        "statistics": "Get-NetAdapterStatistics | Select-Object Name,ReceivedErrors,OutboundErrors,ReceivedDiscardedPackets,OutboundDiscardedPackets | ConvertTo-Json -Depth 4",
    }
    result: dict[str, Any] = {"status": "PASS WITH OBSERVATIONS", "commands": {}}
    for key, command in commands.items():
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            result["commands"][key] = json.loads(completed.stdout or "null")
        except Exception as exc:  # permissions and driver availability are optional observations.
            result["commands"][key] = {"unavailable": str(exc)}
    return result


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def build_summary(report: dict[str, Any]) -> str:
    lines = [
        "# Camera validation summary",
        "",
        f"Timestamp: {report['timestamp']}",
        f"Repository SHA: {report['repository_sha']}",
        "",
        "## Stages",
        "",
    ]
    for stage, result in report["stages"].items():
        reason = result.get("reason")
        suffix = f" ({reason})" if reason else ""
        lines.append(f"- **{stage}:** {result.get('status', 'NOT RUN')}{suffix}")
    lines.extend(
        [
            "",
            "## Cameras",
            "",
            "| Label | ID | Model | Pixel format | ROI | Gain | Exposure | FPS |",
            "|---|---|---|---|---|---:|---:|---:|",
        ]
    )
    labels = {camera["id"]: camera["label"] for camera in report.get("requested_cameras", [])}
    for camera_id, capability in report.get("capabilities", {}).items():
        identity = capability.get("identity", {})
        features = capability.get("features", {})

        def value(name: str, feature_map: dict[str, Any] = features) -> Any:
            return feature_map.get(name, {}).get("value")

        roi = f"{value('width')}×{value('height')} +{value('offset_x')}+{value('offset_y')}"
        lines.append(
            f"| {labels.get(camera_id, camera_id)} | {camera_id} | {identity.get('model')} | "
            f"{value('pixel_format')} | {roi} | {value('gain')} | {value('exposure')} | {value('frame_rate')} |"
        )
    lines.extend(["", "## Timing", ""])
    for key in ("single_timing", "dual_timing"):
        if key in report:
            lines.append(f"### {key.replace('_', ' ').title()}")
            lines.append("")
            for camera_id, metrics in report[key].items():
                lines.append(
                    f"- {labels.get(camera_id, camera_id)}: {metrics.get('status')}, "
                    f"{metrics.get('complete_frames', 0)} complete / {metrics.get('incomplete_frames', 0)} incomplete, "
                    f"measured {metrics.get('measured_fps')} FPS, gaps {metrics.get('frame_id_gaps')}, "
                    f"callback errors {metrics.get('callback_errors', [])}"
                )
            lines.append("")
    if "network" in report:
        lines.extend(["## Network audit", "", f"Status: {report['network'].get('status')}", ""])
    lines += ["", "No camera feature settings were modified by the baseline.", ""]
    return "\n".join(lines)


def run_baseline(
    cameras: list[tuple[str, str]],
    output_dir: Path,
    duration: float,
    *,
    audit_network: bool = False,
    require_validated_profile: bool = False,
    endurance_duration: float | None = None,
    environment_reporter: Any = environment_report,
    vmb_system_factory: Any = None,
    timing_capture: Any = None,
    dual_capture: Any = None,
) -> tuple[dict[str, Any], int]:
    report: dict[str, Any] = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repository_sha": _repository_sha(),
        "requested_cameras": [{"id": camera_id, "label": label} for camera_id, label in cameras],
        "stages": {},
        "resources": {"camera_closes": {}, "vmbsystem_exit": "NOT STARTED"},
        "settings_modified": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        report["environment"] = environment_reporter()
        _write_json(output_dir / "environment.json", report["environment"])
        report["stages"]["environment"] = {"status": "PASS"}
        if require_validated_profile:
            expected = {"python": "3.12.8", "vmbpy": "1.2.2"}
            mismatches = {
                key: {"expected": value, "actual": report["environment"].get(key)}
                for key, value in expected.items()
                if report["environment"].get(key) != value
            }
            if mismatches:
                report["stages"]["environment"] = {"status": "FAIL", "profile_mismatches": mismatches}
                report["exit_code"] = 2
                _write_json(output_dir / "summary.json", report)
                (output_dir / "summary.md").write_text(build_summary(report), encoding="utf-8")
                return report, 2
    except Exception as exc:
        report["stages"]["environment"] = {"status": "FAIL", "reason": str(exc)}
        report["exit_code"] = 2
        _write_json(output_dir / "summary.json", report)
        (output_dir / "summary.md").write_text(build_summary(report), encoding="utf-8")
        return report, 2

    if vmb_system_factory is None:
        from vmbpy import VmbSystem

        vmb_system_factory = VmbSystem.get_instance
    timing_capture = timing_capture or capture_timing
    dual_capture = dual_capture or _capture_dual

    requested = [camera_id for camera_id, _ in cameras]
    report["resources"]["vmbsystem_exit"] = "IN PROGRESS"
    with vmb_system_factory() as system:
        discovery = discover_exact(system, requested)
        report["discovery"] = {key: value for key, value in discovery.items() if key != "cameras"}
        _write_json(output_dir / "discovery.json", report["discovery"])
        report["stages"]["discovery"] = {"status": discovery["status"], "reason": ", ".join(discovery["missing_ids"])}
        if discovery["status"] != "PASS":
            report["resources"]["vmbsystem_exit"] = "PASS"
            if audit_network:
                report["network"] = network_audit()
                _write_json(output_dir / "network.json", report["network"])
                report["stages"]["network"] = {"status": report["network"]["status"]}
            report["exit_code"] = 1
            _write_json(output_dir / "summary.json", report)
            (output_dir / "summary.md").write_text(build_summary(report), encoding="utf-8")
            return report, 1

        opened: dict[str, Any] = {}
        capability_ok = True
        for camera_id, label in cameras:
            try:
                with discovery["cameras"][camera_id] as camera:
                    opened[camera_id] = camera
                    capability = inspect_camera(camera)
                    serialized = json.dumps(capability, allow_nan=False)
                    filename = f"{_safe_name(label)}-capabilities.json"
                    (output_dir / filename).write_text(
                        json.dumps(json.loads(serialized), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                    )
                    report.setdefault("capabilities", {})[camera_id] = capability
                    report["stages"][f"capabilities:{camera_id}"] = {"status": "PASS", "file": filename}
                    report["resources"]["camera_closes"][camera_id] = "PASS"
            except Exception as exc:
                capability_ok = False
                report["stages"][f"capabilities:{camera_id}"] = {"status": "FAIL", "reason": str(exc)}
                report["resources"]["camera_closes"][camera_id] = "FAIL OR CAMERA STAGE FAILED"
                break
        report["stages"]["capabilities"] = {"status": "PASS" if capability_ok else "FAIL"}

        stream_results: dict[str, Any] = {}
        if capability_ok:
            for camera_id, label in cameras:
                try:
                    with discovery["cameras"][camera_id] as camera:
                        _assert_stream_ready(camera)
                        result = timing_capture(camera, duration)
                    stream_results[camera_id] = result
                    report["resources"]["camera_closes"][camera_id] = "PASS"
                    _write_json(output_dir / f"{_safe_name(label)}-timing.json", result)
                    report["stages"][f"single:{camera_id}"] = {"status": result["status"]}
                    if result["status"] == "FAIL":
                        break
                except Exception as exc:
                    stream_results[camera_id] = {"status": "FAIL", "reason": str(exc)}
                    report["resources"]["camera_closes"][camera_id] = "FAIL OR CAMERA STAGE FAILED"
                    report["stages"][f"single:{camera_id}"] = stream_results[camera_id]
                    break
            for camera_id, label in cameras:
                if camera_id not in stream_results:
                    stream_results[camera_id] = {"status": "NOT RUN", "reason": "earlier single-camera stage failed"}
                    report["stages"][f"single:{camera_id}"] = stream_results[camera_id]
            report["single_timing"] = stream_results
            report["stages"]["single_timing"] = {
                "status": "FAIL" if any(r["status"] == "FAIL" for r in stream_results.values()) else "PASS"
            }
            if len(cameras) == 2 and report["stages"]["single_timing"]["status"] == "PASS":
                try:
                    with ExitStack() as stack:
                        selected = [stack.enter_context(discovery["cameras"][camera_id]) for camera_id in requested]
                        for camera in selected:
                            _assert_stream_ready(camera)
                        report["dual_timing"] = dual_capture(selected, duration)
                    _write_json(output_dir / "dual-timing.json", report["dual_timing"])
                    report["stages"]["dual_timing"] = {
                        "status": "FAIL"
                        if any(v["status"] == "FAIL" for v in report["dual_timing"].values())
                        else "PASS"
                    }
                except Exception as exc:
                    report["stages"]["dual_timing"] = {"status": "FAIL", "reason": str(exc)}
            short_stages = [
                value["status"]
                for key, value in report["stages"].items()
                if key == "single_timing" or key == "dual_timing"
            ]
            if endurance_duration is not None:
                if any(status == "FAIL" for status in short_stages):
                    report["stages"]["endurance"] = {"status": "NOT RUN", "reason": "short timing stage failed"}
                else:
                    endurance_results = {}
                    for camera_id, label in cameras:
                        try:
                            with discovery["cameras"][camera_id] as camera:
                                _assert_stream_ready(camera)
                                endurance_results[camera_id] = timing_capture(camera, endurance_duration)
                            _write_json(
                                output_dir / f"{_safe_name(label)}-endurance.json", endurance_results[camera_id]
                            )
                        except Exception as exc:
                            endurance_results[camera_id] = {"status": "FAIL", "reason": str(exc)}
                    report["endurance"] = endurance_results
                    report["stages"]["endurance"] = {
                        "status": "FAIL"
                        if any(result["status"] == "FAIL" for result in endurance_results.values())
                        else "PASS"
                    }
        else:
            report["stages"]["single_timing"] = {"status": "NOT RUN", "reason": "capability audit failed"}
            report["stages"]["dual_timing"] = {"status": "NOT RUN", "reason": "capability audit failed"}
    if audit_network:
        report["network"] = network_audit()
        _write_json(output_dir / "network.json", report["network"])
        report["stages"]["network"] = {"status": report["network"]["status"]}
    report["resources"]["vmbsystem_exit"] = "PASS"
    statuses = [stage["status"] for stage in report["stages"].values()]
    exit_code = 1 if "FAIL" in statuses else 0
    report["exit_code"] = exit_code
    _write_json(output_dir / "summary.json", report)
    (output_dir / "summary.md").write_text(build_summary(report), encoding="utf-8")
    return report, exit_code


def _capture_dual(cameras: list[Any], duration_s: float) -> dict[str, Any]:
    from vmbpy import FrameStatus

    states = {
        str(camera.get_id()): {"timestamps": [], "frame_ids": [], "incomplete": 0, "errors": []} for camera in cameras
    }
    started = time.monotonic()
    started_at = started
    done = threading.Event()

    def handler_for(camera_id: str):
        def handler(cam: Any, _stream: Any, frame: Any) -> None:
            state = states[camera_id]
            try:
                if frame.get_status() == FrameStatus.Complete:
                    state["timestamps"].append(time.monotonic())
                    state["frame_ids"].append(int(frame.get_id()))
                else:
                    state["incomplete"] += 1
                if time.monotonic() - started >= duration_s:
                    done.set()
            except Exception as exc:  # callback errors are preserved per camera.
                state["errors"].append(str(exc))
                done.set()
            finally:
                try:
                    cam.queue_frame(frame)
                except Exception as exc:
                    state["errors"].append(str(exc))
                    done.set()

        return handler

    started_cameras = []
    try:
        for camera in cameras:
            camera.start_streaming(handler_for(str(camera.get_id())), buffer_count=5)
            started_cameras.append(camera)
        if not done.wait(duration_s + 10):
            for state in states.values():
                state["errors"].append("stream duration timed out")
    finally:
        for camera in reversed(started_cameras):
            try:
                camera.stop_streaming()
            except Exception as exc:
                states[str(camera.get_id())]["errors"].append(f"stream stop failed: {exc}")
    ended_at = time.monotonic()
    result = {}
    camera_by_id = {str(camera.get_id()): camera for camera in cameras}
    for camera_id, state in states.items():
        timing = frame_timing_metrics(state["timestamps"], state["frame_ids"])
        reported_fps = _feature_name_value(camera_by_id[camera_id], "frame_rate")
        observations = bool(state["incomplete"] or timing.frame_id_gaps)
        if (
            isinstance(reported_fps, (int, float))
            and timing.measured_fps is not None
            and timing.measured_fps < reported_fps
        ):
            observations = True
        try:
            roi = read_roi(camera_by_id[camera_id]).__dict__
        except Exception:
            roi = None
        status = (
            "FAIL"
            if state["errors"] or not state["timestamps"]
            else "PASS WITH OBSERVATIONS"
            if observations
            else "PASS"
        )
        result[camera_id] = {
            "status": status,
            "start_monotonic": started_at,
            "end_monotonic": ended_at,
            "duration_seconds": ended_at - started_at,
            "roi": roi,
            "complete_frames": len(state["timestamps"]),
            "incomplete_frames": state["incomplete"],
            "reported_frame_rate": reported_fps,
            "callback_errors": state["errors"],
            **timing.__dict__,
        }
    return result


def _repository_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return None


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--camera",
        required=True,
        action="append",
        type=parse_camera,
        help="Exact CAMERA_ID or CAMERA_ID=LABEL; repeat for each camera",
    )
    parser.add_argument(
        "--baseline", action="store_true", default=True, help="Run read-only capability and timing baseline (default)"
    )
    parser.add_argument("--duration", type=float, default=30.0, help="Single and dual stream duration in seconds")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--network-audit", action="store_true", help="Collect read-only Windows adapter inventory")
    parser.add_argument(
        "--run-endurance", action="store_true", help="Run optional endurance timing after baseline success"
    )
    parser.add_argument("--endurance-duration", type=float, default=300.0)
    parser.add_argument(
        "--require-validated-profile", action="store_true", help="Require Python 3.12.8 and VmbPy 1.2.2"
    )
    parser.add_argument(
        "--authorize-settings-changes",
        action="store_true",
        help="Authorization gate for future settings-changing stages",
    )
    settings_mode = parser.add_mutually_exclusive_group()
    settings_mode.add_argument("--quality-sweep", action="store_true")
    settings_mode.add_argument("--roi", help="ROI timing mode, WIDTHxHEIGHT")
    settings_mode.add_argument("--auto-once", choices=("exposure", "gain"))
    parser.add_argument("--gain-values")
    parser.add_argument("--exposure-values-us")
    parser.add_argument("--frames", type=int, default=200, help="Frames per characterization operating point")
    args = parser.parse_args()
    if (
        args.duration <= 0
        or args.endurance_duration <= 0
        or not args.camera
        or len({camera_id for camera_id, _ in args.camera}) != len(args.camera)
    ):
        parser.error("durations must be positive and exact camera IDs must be unique")
    changing = args.quality_sweep or args.roi or args.auto_once
    if changing and not args.authorize_settings_changes:
        parser.error("--quality-sweep, --roi, and --auto-once require --authorize-settings-changes")
    if args.authorize_settings_changes and not changing:
        parser.error("--authorize-settings-changes requires a settings-changing stage")
    if args.quality_sweep and (not args.gain_values or not args.exposure_values_us):
        parser.error("--quality-sweep requires --gain-values and --exposure-values-us")
    if args.output_dir is None:
        args.output_dir = Path("camera-validation-results") / datetime.now().astimezone().strftime("%Y-%m-%dT%H%M%S")
    if changing:
        if len(args.camera) != 1:
            parser.error("settings-changing characterization accepts one exact camera per run")
        if not 1 <= args.frames <= 500:
            parser.error("--frames must be between 1 and 500")
        if args.quality_sweep and len(args.gain_values.split(",")) * len(args.exposure_values_us.split(",")) > 25:
            parser.error("quality sweep is limited to 25 operating points")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        camera_id = args.camera[0][0]
        characterize = [
            sys.executable,
            str(Path(__file__).with_name("camera_characterize.py")),
            "--camera-id",
            camera_id,
            "--authorize-settings-changes",
            "--output",
            str(args.output_dir / "characterization.json"),
            "--frames",
            str(args.frames),
        ]
        if args.quality_sweep:
            characterize += [
                "--quality-sweep",
                "--gain-values",
                args.gain_values,
                "--exposure-values-us",
                args.exposure_values_us,
            ]
        elif args.auto_once:
            characterize += ["--auto-once", args.auto_once]
        else:
            characterize += ["--stream-test", "--duration", str(args.duration), "--roi", args.roi]
        completed = subprocess.run(characterize, check=False)
        code = 0 if completed.returncode == 0 else 3 if completed.returncode == 3 else 1
        report = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "repository_sha": _repository_sha(),
            "requested_cameras": [{"id": camera_id, "label": label} for camera_id, label in args.camera],
            "settings_modified": True,
            "settings_change_authorized": True,
            "characterization_output": "characterization.json",
            "stages": {"characterization": {"status": "PASS" if code == 0 else "FAIL", "exit_code": code}},
            "exit_code": code,
        }
        _write_json(args.output_dir / "summary.json", report)
        (args.output_dir / "summary.md").write_text(build_summary(report), encoding="utf-8")
        return code
    try:
        report, code = run_baseline(
            args.camera,
            args.output_dir,
            args.duration,
            audit_network=args.network_audit,
            require_validated_profile=args.require_validated_profile,
            endurance_duration=args.endurance_duration if args.run_endurance else None,
        )
    except Exception as exc:
        print(f"Camera validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"output_dir": str(args.output_dir), "exit_code": code, "stages": report["stages"]}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
