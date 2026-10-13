"""Hardware-free timing characterization of the real AlignmentWorker raster path.

Fake Python DLL functions exercise PiezoController's existing clamping and
bounds-read logic. Sleep and device service times advance a VIRTUAL clock;
the reported host wall time is Python execution overhead, not lab throughput.
No native DLL is loaded and no laser hardware can be accessed.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from ctypes import POINTER, c_double, cast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from hardware import alignment_worker
from hardware.alignment_worker import AlignmentSettings, AlignmentWorker, MappingSettings, SpiralSearchSettings
from hardware.ct400_types import Detector, PowerData
from hardware.piezo import PiezoController


class VirtualClock:
    def __init__(self) -> None:
        self.wait_ms = 0
        self.dll_ms = 0.0
        self.read_ms = 0.0

    def sleep_ms(self, milliseconds: int) -> None:
        self.wait_ms += milliseconds


class FakePiezoDLL:
    """Imitates only the voltage functions that the mapping worker calls."""

    def __init__(self, clock: VirtualClock, service_ms: float = 0.1) -> None:
        self.clock = clock
        self.service_ms = service_ms
        self.voltage = {"x": 5.0, "y": 5.0, "z": 5.0}
        self.calls: Counter[str] = Counter()
        self.noop_writes = 0
        self.writes: list[tuple[str, float]] = []

    def __getattr__(self, method: str):
        for axis in ("x", "y", "z"):
            label = axis.upper()
            operations = {
                f"Get{label}AxisMinVoltage": "min",
                f"Get{label}AxisMaxVoltage": "max",
                f"Get{label}AxisVoltage": "read",
                f"Set{label}AxisVoltage": "write",
            }
            if method not in operations:
                continue
            operation = operations[method]

            def invoke(_handle, argument, *, axis=axis, operation=operation, method=method):
                self.calls[method] += 1
                self.clock.dll_ms += self.service_ms
                if operation == "write":
                    value = float(argument.value)
                    if abs(value - self.voltage[axis]) <= 1e-12:
                        self.noop_writes += 1
                    self.voltage[axis] = value
                    self.writes.append((axis, value))
                else:
                    value = {"min": 0.0, "max": 10.0, "read": self.voltage[axis]}[operation]
                    cast(argument, POINTER(c_double))[0] = value
                return 0

            return invoke
        raise AttributeError(method)


class FakePowerMeter:
    def __init__(self, dll: FakePiezoDLL, clock: VirtualClock, service_ms: float = 0.5) -> None:
        self.dll = dll
        self.clock = clock
        self.service_ms = service_ms
        self.read_calls = 0
        self.laser_states: list[int] = []
        self.fail_after: int | None = None
        self.on_read = None
        self.override_dbm: float | None = None

    def cmd_laser(self, *, laser_input, enable, wavelength, power) -> None:
        self.laser_states.append(int(enable))

    def get_all_powers(self) -> PowerData:
        self.read_calls += 1
        if self.fail_after is not None and self.read_calls >= self.fail_after:
            raise RuntimeError("injected synthetic power read failure")
        self.clock.read_ms += self.service_ms
        if self.on_read is not None:
            self.on_read()
        dbm = -30.0 + 2 * (self.dll.voltage["x"] - 5) + 3 * (self.dll.voltage["y"] - 5)
        return PowerData(pout=0.0, detectors={Detector.DE_1: self.override_dbm if self.override_dbm is not None else dbm})


def make_worker(clock: VirtualClock) -> tuple[AlignmentWorker, FakePiezoDLL, FakePowerMeter]:
    dll = FakePiezoDLL(clock)
    piezo = object.__new__(PiezoController)
    piezo._dll = dll  # Overrides the class-level DLL with fake Python functions.
    piezo.hdl = 1
    piezo.port = "SIMULATED-DLL"
    meter = FakePowerMeter(dll, clock)
    worker = AlignmentWorker(meter, piezo, piezo)
    return worker, dll, meter


def mapping_settings(width: int, height: int, samples: int, settling_ms: int = 3) -> MappingSettings:
    if width < 1 or height < 1 or samples < 1 or settling_ms < 0:
        raise ValueError("Invalid synthetic map size, samples or settling time")
    return MappingSettings(
        laser_wavelength_nm=1550.0,
        laser_power=1.0,
        power_unit="mW",
        input_port=1,
        x_min_nm=0,
        x_max_nm=width - 1,
        x_step_nm=1,
        y_min_nm=0,
        y_max_nm=height - 1,
        y_step_nm=1,
        samples_per_point=samples,
        stage_to_map="left",
        settling_time_ms=settling_ms,
    )


def characterize(width: int, height: int, samples: int, settling_ms: int = 3) -> dict:
    """Execute unmodified worker logic against virtual-delay synthetic drivers."""
    clock = VirtualClock()
    worker, dll, meter = make_worker(clock)
    settings = mapping_settings(width, height, samples, settling_ms)
    results: list[tuple] = []
    errors: list[str] = []
    progress: list[tuple[int, int]] = []
    worker.mapping_finished.connect(lambda *args: results.append(args))
    worker.mapping_progress.connect(lambda *args: progress.append(args))
    worker.error_occurred.connect(errors.append)
    worker.begin_operation()

    start = time.perf_counter()
    with patch.object(alignment_worker, "QThread", SimpleNamespace(msleep=clock.sleep_ms)):
        worker.run_mapping(settings)
    wall_ms = (time.perf_counter() - start) * 1000

    if errors or len(results) != 1:
        raise AssertionError(f"Unexpected synthetic mapping error: {errors}")
    x, y, measured = results[0]
    expected_dbm = -30 + (
        2 * x[:, None] * PiezoController.VOLTS_PER_NM * 1000 + 3 * y[None, :] * PiezoController.VOLTS_PER_NM * 1000
    )
    if measured.shape != (width, height) or not np.allclose(measured, 10 ** (expected_dbm / 10)):
        raise AssertionError("Power grid values or X/Y orientation changed")
    if dll.voltage["x"] != 5 or dll.voltage["y"] != 5:
        raise AssertionError("Mapping did not restore the stage's initial position")
    if meter.laser_states != [1, 0] or len(progress) != width * height:
        raise AssertionError("Mapping lifecycle or progress notifications changed")

    points = width * height
    expected_dll_calls = 6 * points + 18
    expected_reads = points * samples
    expected_wait_ms = 200 + points * settling_ms + (20 * expected_reads if samples > 1 else 0)
    observed_calls = sum(dll.calls.values())
    if (observed_calls, meter.read_calls, clock.wait_ms) != (
        expected_dll_calls,
        expected_reads,
        expected_wait_ms,
    ):
        raise AssertionError("Observed mock calls/waits do not match the source cost model")

    return {
        "mode": "fake_piezo_dll_and_fake_ct400_virtual_delays",
        "shape": [width, height],
        "samples_per_point": samples,
        "points": points,
        "dll_calls_predicted": expected_dll_calls,
        "dll_calls_observed": observed_calls,
        "detector_reads_predicted": expected_reads,
        "detector_reads_observed": meter.read_calls,
        "wait_ms_predicted": expected_wait_ms,
        "wait_ms_observed": clock.wait_ms,
        "dll_service_ms_virtual": round(clock.dll_ms, 3),
        "detector_service_ms_virtual": round(clock.read_ms, 3),
        "total_service_and_wait_ms_virtual": round(clock.wait_ms + clock.dll_ms + clock.read_ms, 3),
        "python_overhead_ms_wall": round(wall_ms, 3),
        "axis_voltage_writes": len(dll.writes),
        "noop_voltage_writes": dll.noop_writes,
        "progress_signals": len(progress),
        "restored_initial_position": True,
        "physical_measurement": False,
    }


def characterize_search(mode: str, samples: int = 1) -> dict:
    """Run fine/spiral search using a flat fake-power landscape."""
    if mode not in ("fine", "spiral"):
        raise ValueError("Unknown synthetic alignment mode")
    clock = VirtualClock()
    worker, dll, meter = make_worker(clock)
    meter.override_dbm = -30.0
    settings = AlignmentSettings(
        laser_wavelength_nm=1550.0,
        laser_power=1.0,
        power_unit="mW",
        input_port=1,
        iterations=1,
        step_nm=5.0,
        samples_per_point=samples,
        coupling_type="butt",
        settling_time_ms=3,
    )
    completed = []
    errors: list[str] = []
    progress = []
    worker.alignment_finished.connect(lambda *args: completed.append(args))
    worker.progress_updated.connect(lambda *args: progress.append(args))
    worker.error_occurred.connect(errors.append)
    worker.begin_operation()
    start = time.perf_counter()
    with patch.object(alignment_worker, "QThread", SimpleNamespace(msleep=clock.sleep_ms)):
        if mode == "fine":
            worker.run_alignment(settings)
        else:
            worker.run_spiral_alignment(settings, SpiralSearchSettings(radius_um=0.1, step_um=0.1))
    elapsed_ms = (time.perf_counter() - start) * 1000
    if errors or len(completed) != 1 or "successful" not in completed[0][0]:
        raise AssertionError(f"Unexpected fake {mode} alignment outcome: {errors} / {completed}")
    if meter.laser_states != [1, 0] or worker._is_running:
        raise AssertionError("The synthetic search did not release laser ownership")
    return {
        "mode": mode,
        "fake_power_landscape": "flat_-30_dBm",
        "samples_per_point": samples,
        "dll_calls_observed": sum(dll.calls.values()),
        "voltage_writes_observed": len(dll.writes),
        "detector_reads_observed": meter.read_calls,
        "virtual_wait_ms_observed": clock.wait_ms,
        "virtual_total_ms_observed": round(clock.wait_ms + clock.dll_ms + clock.read_ms, 3),
        "python_overhead_ms_wall": round(elapsed_ms, 3),
        "progress_signals": len(progress),
        "laser_cleanup_requested": True,
        "physical_measurement": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--small-only", action="store_true", help="Omit the 100x100 cases")
    args = parser.parse_args()
    shapes = ((10, 10),) if args.small_only else ((10, 10), (100, 100))
    cases = [characterize(w, h, samples) for w, h in shapes for samples in (1, 3, 5)]
    payload = {
        "method": "real AlignmentWorker/PiezoController methods; fake ctypes DLL, fake meter, virtual delays",
        "hardware": "none",
        "cases": cases,
        "search_cases": [characterize_search("fine"), characterize_search("spiral")],
    }
    encoded = json.dumps(payload, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
