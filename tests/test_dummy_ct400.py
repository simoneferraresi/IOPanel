import threading
import time

import numpy as np
import pytest

from hardware.ct400_types import CT400ScanResultKind, Detector, Enable, InstrumentError, LaserInput
from hardware.dummy_ct400 import DummyCT400
from logic.scan_measurement import ScanAcquisitionSettings
from ui.control_panel import CT400ControlPanel, QMessageBox, ScanWorker


def test_dummy_scan_data_honors_range_and_resolution_deterministically():
    device = DummyCT400(scan_duration=0)
    device.set_scan(1.0, 1500.0, 1501.0)
    device.set_sampling_res(100)

    wavelengths, powers = device.get_data_points([Detector.DE_1, Detector.DE_2])
    repeat_wavelengths, repeat_powers = device.get_data_points([Detector.DE_1, Detector.DE_2])

    np.testing.assert_allclose(wavelengths, np.linspace(1500.0, 1501.0, 11))
    np.testing.assert_array_equal(wavelengths, repeat_wavelengths)
    np.testing.assert_array_equal(powers, repeat_powers)
    assert powers.shape == (2, 11)
    assert device.get_all_powers() == device.get_all_powers()


def test_dummy_scan_includes_endpoint_for_decimal_wavelength_range():
    device = DummyCT400(scan_duration=0)
    device.set_scan(1.0, 1550.0, 1550.004)
    device.set_sampling_res(1)

    wavelengths, powers = device.get_data_points([Detector.DE_1])

    np.testing.assert_allclose(wavelengths, [1550.0, 1550.001, 1550.002, 1550.003, 1550.004])
    assert powers.shape == (1, 5)


def test_dummy_scan_waits_once_for_completion_or_returns_documented_scan_error():
    device = DummyCT400(scan_duration=0)
    device.start_scan()
    result = device.scan_wait_end()
    assert (result.raw_code, result.error_message, result.kind) == (0, "", CT400ScanResultKind.SUCCESS)

    failing_device = DummyCT400(scan_duration=0, scan_error="simulated scan failure", scan_result_code=2)
    failing_device.start_scan()
    failed = failing_device.scan_wait_end()
    assert (failed.raw_code, failed.error_message, failed.kind) == (
        2,
        "simulated scan failure",
        CT400ScanResultKind.FATAL_ERROR,
    )


def test_scan_worker_acquires_dummy_data_and_cleans_up(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_3)
    completed, errors, finished = [], [], []
    measurements = []
    worker.completed_signal.connect(lambda *data: completed.append(data))
    worker.measurement_ready.connect(measurements.append)
    worker.error_signal.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))

    worker.do_scan()

    assert errors == []
    assert len(completed) == 1
    wavelengths, powers, pout = completed[0]
    np.testing.assert_allclose(wavelengths, np.linspace(1500.0, 1501.0, 11))
    assert powers.shape == (1, 11)
    assert pout == -20.0
    measurement = measurements[0]
    assert measurement.result_kind == CT400ScanResultKind.SUCCESS
    assert measurement.raw_result_code == 0
    assert measurement.detectors == (Detector.DE_1,)
    assert measurement.simulated is True
    assert "DummyCT400" in measurement.backend
    assert measurement.settings.laser_power_mw == 1.0
    assert measurement.completed_at_utc.tzinfo is not None
    assert finished == [True]
    assert not device._is_scanning
    assert not device._laser_enabled
    assert device.scan_wait_end_calls == 1
    assert device.get_data_points_calls == 1
    assert device.stop_scan_calls == 0
    assert len(device.cmd_laser_calls) == 2
    assert all(_call[1]["laser_input"] == LaserInput.LI_3 for _call in device.cmd_laser_calls)
    assert all(_call[1]["enable"] == Enable.DISABLE for _call in device.cmd_laser_calls)


def test_scan_worker_preserves_warning_result_with_returned_data(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0, scan_error="documented warning", scan_result_code=100)
    settings = ScanAcquisitionSettings(1500, 1501, 100, "5", "3", "dBm", 1.995, LaserInput.LI_2,
                                       (Detector.DE_1,))
    worker = ScanWorker(device, 1500, 1501, 100, 1.995, LaserInput.LI_2, acquisition_settings=settings)
    measurements, warnings = [], []
    worker.measurement_ready.connect(measurements.append)
    worker.warning_signal.connect(lambda code, msg: warnings.append((code, msg)))

    worker.do_scan()

    assert warnings == [(100, "documented warning")]
    measurement = measurements[0]
    assert measurement.result_kind == CT400ScanResultKind.WARNING
    assert measurement.raw_result_code == 100
    assert measurement.result_message == "documented warning"
    assert measurement.settings.entered_laser_power == "3"
    assert measurement.settings.entered_laser_power_unit == "dBm"
    assert measurement.settings.laser_power_mw == 1.995
    assert measurement.detector_data.shape[0] == 1


def test_selected_input_cleanup_occurs_once_after_completed_scan(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_3)

    worker.do_scan()

    assert len(device.cmd_laser_calls) == 2  # pre-scan disable plus finally cleanup
    _cleanup_args, cleanup_kwargs = device.cmd_laser_calls[-1]
    assert cleanup_kwargs["laser_input"] == LaserInput.LI_3
    assert cleanup_kwargs["enable"] == Enable.DISABLE
    assert device.stop_scan_calls == 0


def test_scan_worker_reports_simulated_error_and_still_cleans_up(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0, scan_error="simulated scan failure")
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    completed, errors, finished = [], [], []
    worker.completed_signal.connect(lambda *data: completed.append(data))
    worker.error_signal.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))

    worker.do_scan()

    assert completed == []
    assert len(errors) == 1
    assert errors[0].code == 2
    assert errors[0].message == "simulated scan failure"
    assert finished == [True]
    assert not device._is_scanning
    assert not device._laser_enabled


def test_blocked_wait_is_released_by_one_stop_and_reports_vendor_cancellation(monkeypatch, qtbot):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    wait_gate = threading.Event()
    device = DummyCT400(wait_gate=wait_gate)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_3)
    errors, finished, measurements = [], [], []
    worker.error_signal.connect(errors.append)
    worker.measurement_ready.connect(measurements.append)
    worker.finished.connect(lambda: finished.append(True))
    thread = threading.Thread(target=worker.do_scan)
    thread.start()
    deadline = time.monotonic() + 1
    while not device._is_scanning and time.monotonic() < deadline:
        time.sleep(0.001)
    assert device._is_scanning
    assert worker.stop()
    assert not worker.stop()
    thread.join(timeout=1)
    qtbot.wait(1)

    assert len(errors) == 1
    assert measurements == []
    assert errors[0].code == 1
    assert errors[0].kind == CT400ScanResultKind.USER_CANCELLED
    assert device.cmd_laser_calls[-1][1]["laser_input"] == LaserInput.LI_3
    assert device.cmd_laser_calls[-1][1]["enable"] == Enable.DISABLE
    assert len(device.cmd_laser_calls) == 2
    assert finished == [True]
    assert device.stop_scan_calls == 1
    assert not device._is_scanning
    assert not device._laser_enabled


@pytest.mark.parametrize("cancel_stage", ["before_start", "during_setup"])
def test_early_cancellation_sets_final_kind_and_cleans_up_once(monkeypatch, cancel_stage):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    errors = []
    events = []
    measurements = []
    worker.error_signal.connect(errors.append)
    worker.measurement_ready.connect(measurements.append)

    if cancel_stage == "before_start":
        worker.stop()
    else:
        def cancel_during_setup(*_args):
            events.append("set_scan")
            worker.stop()

        device.set_scan = cancel_during_setup

    device.set_sampling_res = lambda *_args: events.append("sampling")
    device.start_scan = lambda: events.append("start")
    device.scan_wait_end = lambda: events.append("wait")
    device.get_data_points = lambda *_args: events.append("data")

    worker.do_scan()

    assert len(errors) == 1
    assert measurements == []
    assert errors[0].kind == CT400ScanResultKind.USER_CANCELLED
    assert worker._final_kind == CT400ScanResultKind.USER_CANCELLED
    assert events == ([] if cancel_stage == "before_start" else ["set_scan"])
    assert len(device.cmd_laser_calls) == (1 if cancel_stage == "before_start" else 2)
    assert device.cmd_laser_calls[-1][1]["enable"] == Enable.DISABLE


def test_generic_scan_operation_error_uses_neutral_message(monkeypatch):
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        lambda *args: messages.append(args),
    )
    error = InstrumentError(code=None, message="setting sampling failed", source="ScanWorker")

    CT400ControlPanel._handle_scan_error(None, error)

    assert len(messages) == 1
    assert messages[0][1] == "CT400 Operation Error"
    assert messages[0][2] == "CT400 operation failed:\n\nsetting sampling failed"


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_documented_fatal_scan_codes_do_not_retrieve_data(monkeypatch, code):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0, scan_error=f"error {code}", scan_result_code=code)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_3)
    completed, errors, measurements = [], [], []
    worker.completed_signal.connect(lambda *data: completed.append(data))
    worker.measurement_ready.connect(measurements.append)
    worker.error_signal.connect(errors.append)

    worker.do_scan()

    assert completed == []
    assert measurements == []
    assert len(errors) == 1
    assert errors[0].code == code
    assert errors[0].kind == CT400ScanResultKind.FATAL_ERROR
    assert errors[0].message == f"error {code}"
    assert device.cmd_laser_calls[-1][1]["laser_input"] == LaserInput.LI_3
    assert device.cmd_laser_calls[-1][1]["enable"] == Enable.DISABLE
    assert len(device.cmd_laser_calls) == 2


@pytest.mark.parametrize("code", [100, 999])
def test_documented_warnings_are_reported_and_still_retrieve_data(monkeypatch, code):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0, scan_error=f"warning {code}", scan_result_code=code)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    completed, errors, warnings = [], [], []
    worker.completed_signal.connect(lambda *data: completed.append(data))
    worker.error_signal.connect(errors.append)
    worker.warning_signal.connect(lambda *args: warnings.append(args))

    worker.do_scan()

    assert len(completed) == 1
    assert errors == []
    assert warnings == [(code, f"warning {code}")]


@pytest.mark.parametrize("code", [6, -1, -10])
def test_unknown_or_negative_scan_code_is_preserved_and_not_plotted(monkeypatch, code):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0, scan_error=f"raw {code}", scan_result_code=code)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    completed, errors, measurements = [], [], []
    worker.completed_signal.connect(lambda *data: completed.append(data))
    worker.measurement_ready.connect(measurements.append)
    worker.error_signal.connect(errors.append)

    worker.do_scan()

    assert completed == []
    assert measurements == []
    assert errors[0].code == code
    assert errors[0].kind == CT400ScanResultKind.UNEXPECTED
    assert errors[0].message == f"raw {code}"


def test_normal_and_warning_results_do_not_call_scan_stop(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    for kwargs in ({}, {"scan_error": "resolution adjusted", "scan_result_code": 999}):
        device = DummyCT400(scan_duration=0, **kwargs)
        worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
        worker.do_scan()
        assert device.stop_scan_calls == 0


def test_stop_completion_race_uses_waitend_success_result(monkeypatch, qtbot):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)

    class StopDoesNotInterrupt(DummyCT400):
        def stop_scan(self):
            self.stop_scan_calls += 1

    gate = threading.Event()
    device = StopDoesNotInterrupt(wait_gate=gate)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    errors, completed = [], []
    worker.error_signal.connect(errors.append)
    worker.completed_signal.connect(lambda *args: completed.append(args))
    thread = threading.Thread(target=worker.do_scan)
    thread.start()
    deadline = time.monotonic() + 1
    while not device._is_scanning and time.monotonic() < deadline:
        time.sleep(0.001)

    assert worker.stop()
    gate.set()
    thread.join(timeout=1)
    qtbot.wait(1)

    assert device.stop_scan_calls == 1
    assert device.scan_wait_end_calls == 1
    assert errors == []
    assert len(completed) == 1
    assert device.get_data_points_calls == 1


def test_stop_failure_is_reported_and_worker_waits_for_waitend(monkeypatch, qtbot):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)

    class StopFails(DummyCT400):
        def stop_scan(self):
            self.stop_scan_calls += 1
            raise RuntimeError("controlled stop failure")

    gate = threading.Event()
    device = StopFails(wait_gate=gate)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    stop_failures, finished = [], []
    worker.stop_failed_signal.connect(stop_failures.append)
    worker.finished.connect(lambda: finished.append(True))
    thread = threading.Thread(target=worker.do_scan)
    thread.start()
    deadline = time.monotonic() + 1
    while not device._is_scanning and time.monotonic() < deadline:
        time.sleep(0.001)

    worker.stop()
    qtbot.waitUntil(lambda: bool(stop_failures), timeout=1000)
    assert thread.is_alive()
    assert finished == []
    assert "controlled stop failure" in stop_failures[0]
    gate.set()
    thread.join(timeout=1)
    qtbot.wait(1)
    assert finished == [True]
    assert device.stop_scan_calls == 1


def test_dummy_rejects_invalid_scan_duration():
    with pytest.raises(ValueError, match="non-negative"):
        DummyCT400(scan_duration=-1)


def test_dummy_close_stops_active_scan_and_disables_laser():
    device = DummyCT400()
    device.cmd_laser(enable=Enable.ENABLE)
    device.start_scan()

    device.close()

    assert not device.is_connected()
    assert not device._is_scanning
    assert not device._laser_enabled
