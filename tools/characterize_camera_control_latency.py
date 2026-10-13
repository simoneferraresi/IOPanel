"""Driver-free Qt heartbeat/feature-write characterization for Issue #153.

Run a real CameraPanel and VimbaCam feature path with an in-memory fake feature
device. The fake performs no native SDK discovery. Synchronous blocking here
is deliberately simulated, not evidence of physical GenICam timing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from pathlib import Path

from PySide6.QtCore import QMutexLocker, QTimer
from PySide6.QtWidgets import QApplication

from config_model import CameraConfig
from hardware.camera import VimbaCam
from ui.camera_widgets import CameraPanel


class Feature:
    def __init__(self, value: object, bounds: tuple[float, float] | None = None, delay_ms: float = 0) -> None:
        self.value = value
        self.bounds = bounds
        self.delay_ms = delay_ms
        self.writes = 0

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.writes += 1
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000)
        self.value = value

    def get_range(self):
        if self.bounds is None:
            raise ValueError("No feature range")
        return self.bounds

    def get_increment(self):
        return 0.1

    def is_readable(self):
        return True

    def is_writeable(self):
        return True


class Device:
    def __init__(self, setter_ms: float) -> None:
        self.features = {
            "Gamma": Feature(1.0, (0.1, 4.0)),
            "ExposureTimeAbs": Feature(5000.0, (10.0, 100000.0)),
            "ExposureAuto": Feature("Off"),
            "GainAuto": Feature("Off"),
            "Gain": Feature(5.0, (0.0, 22.0), delay_ms=setter_ms),
        }

    def get_feature_by_name(self, name):
        return self.features[name]


def make_panel(app: QApplication, setter_ms: float, identifier: str):
    device = Device(setter_ms)
    camera = VimbaCam(identifier, camera_name=identifier)
    camera.device = device
    panel = CameraPanel(camera, identifier, CameraConfig(identifier=identifier, name=identifier))
    return device, camera, panel


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(0.95 * (len(ordered) - 1)))]


def run_scenario(
    mode: str,
    *,
    updates: int = 20,
    setter_ms: float = 10.0,
    repetitions: int = 5,
    contention_ms: float = 30.0,
) -> dict:
    if updates < 2 or repetitions < 1 or setter_ms < 0 or contention_ms < 0:
        raise ValueError("Invalid synthetic measurement parameters")
    if mode not in {"direct", "coalesced", "typed", "dual", "mutex_contention"}:
        raise ValueError(f"Unknown camera-control scenario: {mode}")

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    latencies: list[float] = []
    total_writes = 0
    expected_per_run = {"direct": updates, "coalesced": 1, "typed": 1, "dual": 2, "mutex_contention": 1}[mode]
    values = [5.0 + i * 0.5 for i in range(updates)]
    if values[-1] > 22.0:
        raise ValueError("Too many updates for synthetic gain range")

    for iteration in range(repetitions):
        device, camera, panel = make_panel(app, setter_ms, f"FAKE-CAMERA-{iteration}-A")
        other = None
        worker = None
        try:
            if mode == "dual":
                device_b, _camera_b, other = make_panel(app, setter_ms, f"FAKE-CAMERA-{iteration}-B")
            app.processEvents()
            callback: list[float] = []
            start = time.perf_counter()
            QTimer.singleShot(0, lambda callback=callback: callback.append(time.perf_counter()))
            if mode == "direct":
                # Reference only: reproduces one synchronous setter per intermediate value.
                for value in values:
                    panel._handle_parameter_changed("gain", value)
            elif mode == "coalesced":
                panel.gain_control.slider.setSliderDown(True)
                for value in values:
                    panel._handle_parameter_changed("gain", value)
                panel.gain_control.slider.setSliderDown(False)
            elif mode == "dual":
                panel.gain_control.slider.setSliderDown(True)
                other.gain_control.slider.setSliderDown(True)
                for value in values:
                    panel._handle_parameter_changed("gain", value)
                    other._handle_parameter_changed("gain", value)
                panel.gain_control.slider.setSliderDown(False)
                other.gain_control.slider.setSliderDown(False)
            elif mode == "mutex_contention":
                locked = threading.Event()

                def hold_camera_lock(camera=camera, locked=locked):
                    with QMutexLocker(camera.lock):
                        locked.set()
                        time.sleep(contention_ms / 1000)

                worker = threading.Thread(target=hold_camera_lock, daemon=True)
                worker.start()
                if not locked.wait(timeout=2):
                    raise TimeoutError("Synthetic lock holder did not start")
                panel._handle_parameter_changed("gain", values[-1])
            else:
                panel._handle_parameter_changed("gain", values[-1])

            app.processEvents()
            if not callback:
                raise AssertionError("Qt heartbeat callback was not dispatched")
            latencies.append((callback[0] - start) * 1000)
            written = device.features["Gain"].writes
            if mode == "dual":
                written += device_b.features["Gain"].writes
                if device_b.features["Gain"].value != values[-1]:
                    raise AssertionError("Second synthetic camera did not receive its latest gain")
            if written != expected_per_run or abs(device.features["Gain"].value - values[-1]) > 1e-9:
                raise AssertionError("Synthetic feature-write count or final value changed")
            total_writes += written
        finally:
            if worker is not None:
                worker.join(timeout=2)
                if worker.is_alive():
                    raise TimeoutError("Fake QMutex contention thread did not stop")
            panel.close()
            if other is not None:
                other.close()
            app.processEvents()

    return {
        "scenario": mode,
        "update_requests_per_run": updates if mode in ("direct", "coalesced", "dual") else 1,
        "writes_per_run": expected_per_run,
        "total_completed_writes": total_writes,
        "repetitions": repetitions,
        "fake_setter_ms": setter_ms,
        "fake_mutex_hold_ms": contention_ms if mode == "mutex_contention" else 0,
        "qt_callback_delay_median_ms": round(statistics.median(latencies), 3),
        "qt_callback_delay_p95_ms": round(_p95(latencies), 3),
        "qt_callback_delay_max_ms": round(max(latencies), 3),
        "latest_value_applied": True,
        "native_sdk_used": False,
    }


def characterize(*, updates: int = 20, setter_ms: float = 10, repetitions: int = 5) -> dict:
    cases = [
        run_scenario(mode, updates=updates, setter_ms=setter_ms, repetitions=repetitions)
        for mode in ("direct", "coalesced", "typed", "dual", "mutex_contention")
    ]
    return {
        "scope": "real Qt CameraPanel/VimbaCam feature calls with fake feature setters; no VmbPy",
        "caution": "Reference direct mode is the pre-coalescing call pattern, not a historical checkout.",
        "results": cases,
        "decision": (
            "Burst coalescing removes intermediate synchronous writes, but each remaining "
            "feature operation/lock wait still blocks Qt. Retain current ownership model "
            "pending vendor-verified safe SDK serialization and attended testing."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--updates", type=int, default=20)
    parser.add_argument("--setter-ms", type=float, default=10.0)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    result = characterize(updates=args.updates, setter_ms=args.setter_ms, repetitions=args.repetitions)
    content = json.dumps(result, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
