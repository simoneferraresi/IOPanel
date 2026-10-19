"""Regression tests for CT400 scan-phase boundaries without physical devices."""

from __future__ import annotations

import pytest

from hardware.ct400 import CT400Error
from hardware.ct400_types import CT400ScanResultKind, LaserInput
from hardware.dummy_ct400 import DummyCT400
from ui import control_panel as control_panel_module
from ui.control_panel import ScanWorker


@pytest.mark.parametrize(
    ("cancel_at", "expected"),
    [
        ("initial_disable", ("initial_disable", "stabilize", "final_disable")),
        ("stabilize", ("initial_disable", "stabilize", "final_disable")),
        ("set_scan", ("initial_disable", "stabilize", "set_scan", "final_disable")),
        (
            "set_sampling_res",
            ("initial_disable", "stabilize", "set_scan", "set_sampling_res", "final_disable"),
        ),
        (
            "set_detector_array",
            ("initial_disable", "stabilize", "set_scan", "set_sampling_res", "set_detector_array", "final_disable"),
        ),
    ],
)
def test_cancellation_gates_preserve_command_order_and_final_disable(monkeypatch, cancel_at, expected):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_3)
    calls = []
    errors, measurements, finished = [], [], []
    worker.error_signal.connect(errors.append)
    worker.measurement_ready.connect(measurements.append)
    worker.finished.connect(lambda: finished.append(True))

    def step(name, original):
        def invoke(*args, **kwargs):
            calls.append(name)
            value = original(*args, **kwargs)
            if name == cancel_at:
                assert worker.stop()
            return value

        return invoke

    original_laser = device.cmd_laser

    def laser(*args, **kwargs):
        name = "initial_disable" if not device.cmd_laser_calls else "final_disable"
        return step(name, original_laser)(*args, **kwargs)

    monkeypatch.setattr(device, "cmd_laser", laser)

    def sleep(_milliseconds):
        calls.append("stabilize")
        if cancel_at == "stabilize":
            assert worker.stop()

    monkeypatch.setattr(control_panel_module.QThread, "msleep", sleep)
    for name in ("set_scan", "set_sampling_res", "set_detector_array", "start_scan"):
        monkeypatch.setattr(device, name, step(name, getattr(device, name)))

    worker.do_scan()

    assert tuple(calls) == expected
    assert len(errors) == 1
    assert errors[0].kind is CT400ScanResultKind.USER_CANCELLED
    assert measurements == []
    assert finished == [True]
    assert device.stop_scan_calls == 0
    assert device.scan_wait_end_calls == 0
    assert worker.final_kind is CT400ScanResultKind.USER_CANCELLED
    assert worker.final_timing_summary["outcome"] == "cancellation"
    assert not worker.recovery_required
    assert device.cmd_laser_calls[-1][1]["laser_input"] is LaserInput.LI_3


def test_stop_requested_inside_scan_start_waits_for_start_return(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_2)
    events = []
    errors, measurements = [], []
    worker.error_signal.connect(errors.append)
    worker.measurement_ready.connect(measurements.append)
    original_start = device.start_scan
    original_stop = device.stop_scan
    original_wait = device.scan_wait_end

    def start():
        events.append("start_enter")
        assert worker.stop()
        assert device.stop_scan_calls == 0
        assert not worker._started
        original_start()
        events.append("start_return")

    def stop():
        events.append("stop")
        original_stop()

    def wait():
        events.append("wait")
        return original_wait()

    monkeypatch.setattr(device, "start_scan", start)
    monkeypatch.setattr(device, "stop_scan", stop)
    monkeypatch.setattr(device, "scan_wait_end", wait)
    worker.do_scan()

    assert events == ["start_enter", "start_return", "stop", "wait"]
    assert device.stop_scan_calls == 1
    assert device.scan_wait_end_calls == 1
    assert len(device.cmd_laser_calls) == 2
    assert len(errors) == 1
    assert errors[0].kind is CT400ScanResultKind.USER_CANCELLED
    assert measurements == []
    assert worker.final_timing_summary["outcome"] == "cancellation"


@pytest.mark.parametrize(
    ("failed_method", "failed_stage"),
    [
        ("set_scan", "set_scan_seconds"),
        ("set_sampling_res", "set_sampling_res_seconds"),
        ("set_detector_array", "set_detector_array_seconds"),
        ("start_scan", "start_scan_seconds"),
        ("scan_wait_end", "scan_wait_end_seconds"),
        ("get_data_points", "get_data_points_seconds"),
    ],
)
def test_failed_phase_still_attempts_final_disable_and_reports_stage(monkeypatch, failed_method, failed_stage):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    errors, measurements, finished = [], [], []
    worker.error_signal.connect(errors.append)
    worker.measurement_ready.connect(measurements.append)
    worker.finished.connect(lambda: finished.append(True))

    def fail(*_args, **_kwargs):
        raise CT400Error(f"simulated {failed_method} failure")

    monkeypatch.setattr(device, failed_method, fail)
    worker.do_scan()

    assert len(errors) == 1
    assert errors[0].message == f"simulated {failed_method} failure"
    assert errors[0].code is None
    assert measurements == []
    assert finished == [True]
    assert device.stop_scan_calls == 0
    assert len(device.cmd_laser_calls) == 2
    assert not device._laser_enabled
    summary = worker.final_timing_summary
    assert summary["outcome"] == "error"
    assert summary["failure_stage"] == failed_stage
    assert summary["failed_stages"] == [failed_stage]
    assert summary["cleanup_failed"] is False
    assert worker.recovery_required
