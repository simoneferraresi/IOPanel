import json
import logging

import numpy as np
import pytest

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from hardware.dummy_ct400 import DummyCT400
from logic.scan_measurement import ScanAcquisitionSettings
from ui import control_panel as control_panel_module
from ui.control_panel import ScanWorker


@pytest.fixture
def timed_worker(monkeypatch):
    clock = [0.0]
    events = []
    device = DummyCT400(scan_duration=0)
    durations = {
        "set_scan": 0.02,
        "set_sampling_res": 0.03,
        "set_detector_array": 0.04,
        "start_scan": 0.05,
        "scan_wait_end": 0.06,
        "get_data_points": 0.07,
        "get_all_powers": 0.08,
    }

    monkeypatch.setattr(control_panel_module.time, "perf_counter", lambda: clock[0])
    command_count = [0]

    def timed_command(*args, **kwargs):
        command_count[0] += 1
        name = "initial_laser_disable" if command_count[0] == 1 else "final_laser_disable"
        events.append(name)
        clock[0] += 0.01 if command_count[0] == 1 else 0.09
        return original_command(*args, **kwargs)

    original_command = device.cmd_laser
    monkeypatch.setattr(device, "cmd_laser", timed_command)

    for method_name, duration in durations.items():
        original = getattr(device, method_name)

        def timed_method(*args, _method=method_name, _duration=duration, _original=original, **kwargs):
            events.append(_method)
            clock[0] += _duration
            return _original(*args, **kwargs)

        monkeypatch.setattr(device, method_name, timed_method)

    def timed_sleep(milliseconds):
        events.append("stabilization_delay")
        clock[0] += milliseconds / 1000

    monkeypatch.setattr(control_panel_module.QThread, "msleep", timed_sleep)
    settings = ScanAcquisitionSettings(
        1500.0,
        1501.0,
        100,
        "5",
        "1",
        "mW",
        1.0,
        LaserInput.LI_1,
        (Detector.DE_1, Detector.DE_3),
    )
    worker = ScanWorker(
        device,
        1500.0,
        1501.0,
        100,
        1.0,
        LaserInput.LI_1,
        acquisition_settings=settings,
        configured_speed_nm_s=10,
    )
    measurements = []
    errors = []
    worker.measurement_ready.connect(measurements.append)
    worker.error_signal.connect(errors.append)
    return worker, device, clock, events, measurements, errors, command_count


def _summary(caplog):
    record = next(record for record in caplog.records if record.getMessage().startswith("Scan timing summary: "))
    return json.loads(record.getMessage().split(": ", 1)[1])


@pytest.mark.parametrize(
    ("result_code", "expected_outcome", "measurement_count", "recovery_required"),
    [
        (0, "success", 1, False),
        (100, "warning", 1, False),
        (1, "cancellation", 0, False),
        (2, "error", 0, True),
    ],
)
def test_timing_summary_measures_stages_and_preserves_scan_result(
    timed_worker, caplog, result_code, expected_outcome, measurement_count, recovery_required
):
    worker, device, _clock, events, measurements, errors, _command_count = timed_worker
    if result_code:
        device._scan_result_code = result_code
        device._scan_error = "controlled result"
    with caplog.at_level(logging.INFO, logger="LabApp.control_panel"):
        worker.do_scan()

    summary = _summary(caplog)
    stages = summary["stage_seconds"]
    assert summary["outcome"] == expected_outcome
    assert worker.recovery_required is recovery_required
    assert summary["requested_start_wavelength_nm"] == 1500.0
    assert summary["requested_end_wavelength_nm"] == 1501.0
    assert summary["configured_speed_nm_s_at_connect"] == 10
    assert summary["scan_panel_speed_nm_s"] == "5"
    assert summary["scan_panel_speed_differs_from_configured"] is True
    assert summary["resolution_pm"] == 100
    assert summary["active_detector_count"] == 2
    assert summary["returned_sample_count"] == (11 if measurement_count else None)
    assert summary["simulated"] is True
    assert worker.final_timing_summary["scan_id"] == summary["scan_id"]
    if measurements:
        assert measurements[0].scan_id == summary["scan_id"]
    assert "DummyCT400" in summary["backend"]
    assert summary["known_stabilization_delay_seconds"] == 0.15
    expected_worker_duration = 0.45 if not measurement_count else 0.60
    assert summary["worker_duration_seconds"] == pytest.approx(expected_worker_duration)
    assert summary["failed_stages"] == []
    expected_stages = {
        "initial_laser_disable_seconds": 0.01,
        "stabilization_delay_observed_seconds": 0.15,
        "set_scan_seconds": 0.02,
        "set_sampling_res_seconds": 0.03,
        "set_detector_array_seconds": 0.04,
        "start_scan_seconds": 0.05,
        "scan_wait_end_seconds": 0.06,
        "final_laser_disable_seconds": 0.09,
    }
    if measurement_count:
        expected_stages.update({"get_data_points_seconds": 0.07, "get_all_powers_seconds": 0.08})
    assert stages == pytest.approx(expected_stages)
    assert events == [
        "initial_laser_disable",
        "stabilization_delay",
        "set_scan",
        "set_sampling_res",
        "set_detector_array",
        "start_scan",
        "scan_wait_end",
        *(["get_data_points", "get_all_powers"] if measurement_count else []),
        "final_laser_disable",
    ]
    assert len(measurements) == measurement_count
    if not measurement_count:
        assert len(errors) == 1
        expected_error_kind = (
            CT400ScanResultKind.USER_CANCELLED
            if expected_outcome == "cancellation"
            else CT400ScanResultKind.FATAL_ERROR
        )
        assert errors[0].kind is expected_error_kind
    else:
        assert errors == []
    assert device.scan_wait_end_calls == 1
    if measurements:
        measurement = measurements[0]
        assert measurement.settings is worker.acquisition_settings
        assert measurement.detectors == (Detector.DE_1, Detector.DE_3)
        assert measurement.detector_data.shape == (2, 11)
        expected_wavelengths = np.linspace(1500.0, 1501.0, 11)
        expected_curve = -10 * np.exp(-((expected_wavelengths - 1500.5) ** 2) / (2 * (1.0 / 6) ** 2)) - 30
        np.testing.assert_allclose(measurement.wavelengths_nm, expected_wavelengths)
        np.testing.assert_allclose(measurement.detector_data, np.vstack((expected_curve, expected_curve + 1.0)))


@pytest.mark.parametrize(
    ("failing_method", "failed_stage"),
    [("set_sampling_res", "set_sampling_res_seconds"), ("get_data_points", "get_data_points_seconds")],
)
def test_timing_summary_keeps_partial_measurements_after_stage_exception(
    timed_worker, monkeypatch, caplog, failing_method, failed_stage
):
    worker, device, clock, events, measurements, errors, command_count = timed_worker

    def fail(*_args, **_kwargs):
        clock[0] += 0.025
        raise RuntimeError(f"{failing_method} failed")

    monkeypatch.setattr(device, failing_method, fail)
    with caplog.at_level(logging.INFO, logger="LabApp.control_panel"):
        worker.do_scan()

    summary = _summary(caplog)
    assert summary["outcome"] == "error"
    assert summary["stage_seconds"][failed_stage] == pytest.approx(0.025)
    assert summary["failed_stages"] == [failed_stage]
    assert "final_laser_disable_seconds" in summary["stage_seconds"]
    assert summary["cleanup_failed"] is False
    assert worker.recovery_required
    assert measurements == []
    assert len(errors) == 1
    assert command_count[0] == 2
    assert events[-1] == "final_laser_disable"


def test_timing_summary_records_cleanup_failure_without_changing_measurement(timed_worker, monkeypatch, caplog):
    worker, device, clock, events, measurements, errors, _command_count = timed_worker
    original_timed_command = device.cmd_laser
    call_count = [0]

    def fail_cleanup(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return original_timed_command(*args, **kwargs)
        events.append("final_laser_disable")
        clock[0] += 0.09
        raise RuntimeError("cleanup command failed")

    monkeypatch.setattr(device, "cmd_laser", fail_cleanup)
    with caplog.at_level(logging.INFO, logger="LabApp.control_panel"):
        worker.do_scan()

    summary = _summary(caplog)
    assert summary["outcome"] == "success"
    assert summary["cleanup_failed"] is True
    assert summary["stage_seconds"]["final_laser_disable_seconds"] == pytest.approx(0.09)
    assert summary["failed_stages"] == ["final_laser_disable_seconds"]
    assert worker.recovery_required
    assert len(measurements) == 1
    assert errors == []
    assert device.scan_wait_end_calls == 1
