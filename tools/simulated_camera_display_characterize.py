"""Characterize IOPanel's normal display pipeline using simulated cameras only.

No VmbPy/VmbSystem or physical camera is opened. Output separates frame-copy,
conversion, image presentation, QWidget paint, event-loop, CPU and memory scopes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_model import CameraConfig
from hardware.simulated_camera import SimulatedCamera
from tools.benchmark_scan_io import _windows_memory
from tools.camera_display_characterize import (
    AcquisitionCounter,
    InstrumentedCameraPanel,
    PaintCounter,
    _collect_metrics,
)
from tools.performance_baseline import _environment


def duration_summary(samples: list[float]) -> dict[str, float | int | None]:
    if not samples:
        return {"count": 0, "median_ms": None, "p95_ms": None, "max_ms": None, "samples_ms": []}
    return {
        "count": len(samples),
        "median_ms": statistics.median(samples) * 1000,
        "p95_ms": float(np.percentile(samples, 95)) * 1000,
        "max_ms": max(samples) * 1000,
        "samples_ms": [sample * 1000 for sample in samples],
    }


class MeasuredCameraPanel(InstrumentedCameraPanel):
    """Add diagnostic-only per-stage wall timing around existing production methods."""

    def __init__(self, *args, **kwargs):
        self.conversion_durations: list[float] = []
        self.presentation_durations: list[float] = []
        self.preview_scale_durations: list[float] = []
        super().__init__(*args, **kwargs)

    def _start_conversion_worker(self):
        super()._start_conversion_worker()
        worker = self.conversion_worker
        if worker is None:
            return
        process_frame = worker.process_frame

        def measure_conversion(frame):
            started = time.perf_counter()
            try:
                process_frame(frame)
            finally:
                self.conversion_durations.append(time.perf_counter() - started)

        worker.process_frame = measure_conversion

    def _display_converted_image(self, q_img):
        started = time.perf_counter()
        try:
            super()._display_converted_image(q_img)
        finally:
            self.presentation_durations.append(time.perf_counter() - started)

    def _delayed_display_update(self):
        started = time.perf_counter()
        try:
            super()._delayed_display_update()
        finally:
            self.preview_scale_durations.append(time.perf_counter() - started)


def characterize(*, camera_count: int, duration: float, width: int, height: int, output: Path) -> dict:
    if camera_count not in (1, 2) or duration <= 0 or width < 2 or height < 2:
        raise ValueError("Require one or two cameras, positive duration, and dimensions >= 2")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    cameras: list[SimulatedCamera] = []
    panels: list[MeasuredCameraPanel] = []
    result = {
        "schema_version": 1,
        "kind": "iopanel-simulated-camera-display-characterization",
        "recorded_utc": datetime.now(UTC).isoformat(),
        "evidence_scope": "SimulatedCamera through normal CameraPanel conversion/presentation; no physical camera or SDK",
        "environment": _environment(Path(__file__).resolve().parents[1], "offscreen"),
        "configuration": {"camera_count": camera_count, "duration_s": duration, "width": width, "height": height},
        "cameras": [],
        "process": {},
    }
    try:
        for index in range(camera_count):
            identifier = f"sim-display-{index + 1}"
            camera = SimulatedCamera(
                identifier, f"Simulated {index + 1}", width=width, height=height, frame_interval=1 / 60
            )
            panel = MeasuredCameraPanel(
                None,
                f"Simulated {index + 1}",
                CameraConfig(identifier=identifier, name=f"Simulated {index + 1}", backend="simulation"),
            )
            camera.open()
            panel.set_camera(camera)
            panel.resize(960, 540)
            panel.show()
            cameras.append(camera)
            panels.append(panel)
        app.processEvents()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(panel._latest_pixmap is None for panel in panels):
            app.processEvents()
            time.sleep(0.002)
        if any(panel._latest_pixmap is None or panel.conversion_worker is None for panel in panels):
            raise RuntimeError("Simulated display did not produce a converted frame during warmup")

        paints = [PaintCounter() for _ in panels]
        counters = [AcquisitionCounter() for _ in panels]
        heartbeats: list[list[float]] = [[] for _ in panels]
        timers = []
        before = []
        for panel, paint, counter, heartbeat, camera in zip(panels, paints, counters, heartbeats, cameras, strict=True):
            panel.video_label.installEventFilter(paint)
            camera.new_frame.connect(counter.record)
            worker = panel.conversion_worker
            before.append(
                {
                    "submitted": worker.submitted_frames,
                    "converted": worker.converted_frames,
                    "coalesced": worker.coalesced_frames,
                    "coalesced_images": panel.coalesced_images,
                    "accepted_images": len(panel.accepted_image_timestamps),
                }
            )
            timer = QTimer(panel)
            timer.setInterval(16)
            timer.timeout.connect(lambda target=heartbeat: target.append(time.monotonic()))
            timer.start()
            timers.append(timer)
            counter.timestamps.clear()
            panel.presentation_timestamps.clear()
            paint.timestamps.clear()
            heartbeat.clear()

        tracemalloc.start()
        wall_start, cpu_start = time.monotonic(), time.process_time()
        end = wall_start + duration
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.001)
        wall, cpu = time.monotonic() - wall_start, time.process_time() - cpu_start
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        for timer in timers:
            timer.stop()
        for index, (panel, paint, heartbeat, camera, counter, snapshot) in enumerate(
            zip(panels, paints, heartbeats, cameras, counters, before, strict=True)
        ):
            worker = panel.conversion_worker
            result["cameras"].append(
                {
                    "identifier": camera.identifier,
                    "metrics": _collect_metrics(
                        panel,
                        paint,
                        heartbeat,
                        snapshot,
                        counter.timestamps,
                        [],
                        wall_start,
                        wall,
                        cpu,
                        {"acquired_image_dimensions": [width, height]},
                    ),
                    "stage_durations": {
                        "conversion_including_detached_qimage_copy": duration_summary(panel.conversion_durations),
                        "gui_pixmap_presentation": duration_summary(panel.presentation_durations),
                        "preview_scaling_and_fps_overlay": duration_summary(panel.preview_scale_durations),
                    },
                    "simulated_frame_payload_bytes": width * height,
                    "simulated_source_ownership": "newly allocated uint8 mono array retained by FrameBuffer; no SDK buffer reuse",
                    "converter_ownership": "ImageConversionWorker receives ndarray from signal and emits detached QImage copy",
                }
            )
        result["process"] = {
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "cpu_wall_ratio": cpu / wall if wall else 0,
            "python_traced_current_bytes_after_measurement": traced_current,
            "python_traced_peak_bytes_during_measurement": traced_peak,
            "python_traced_memory_scope": "Python allocations during measurement; excludes native NumPy/OpenCV/Qt image buffers",
            **_windows_memory(),
        }
        probe = cameras[0]._make_frame(0)
        copy_durations = []
        for _ in range(100):
            copy_started = time.perf_counter()
            copied = probe.copy()
            copy_durations.append(time.perf_counter() - copy_started)
            if copied.shape != probe.shape:
                raise AssertionError("NumPy frame copy changed image dimensions")
        result["process"]["owned_array_copy_probe"] = {
            "duration": duration_summary(copy_durations),
            "bytes_per_copy": int(probe.nbytes),
            "scope": "100 NumPy uint8 mono array copies; memory-copy proxy, not an SDK-owned-buffer timing",
        }
    except Exception as exc:  # noqa: BLE001 - retain partial evidence and always clean up.
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        result["measurement_error"] = str(exc)
    finally:
        for panel in panels:
            panel.prepare_camera_shutdown()
            panel.close()
        for camera in cameras:
            camera.close()
        app.processEvents()
        result["cleanup"] = {
            "cameras_closed": all(not camera.is_streaming for camera in cameras),
            "conversion_threads_stopped": all(
                panel.conversion_thread is None or not panel.conversion_thread.isRunning() for panel in panels
            ),
        }
        output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cameras", type=int, choices=(1, 2), default=1)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = characterize(
        camera_count=args.cameras, duration=args.duration, width=args.width, height=args.height, output=args.output
    )
    if result.get("measurement_error"):
        raise SystemExit(result["measurement_error"])
    if not all(result["cleanup"].values()):
        raise SystemExit(f"Cleanup failed: {result['cleanup']}")
    print(f"Measured {args.cameras} simulated camera(s), {args.width}x{args.height}, {args.duration:g}s")


if __name__ == "__main__":
    main()
