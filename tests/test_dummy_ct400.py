import numpy as np
import pytest

from hardware.ct400_types import CT400StatusCode, Detector, Enable, LaserInput
from hardware.dummy_ct400 import DummyCT400
from ui.control_panel import ScanWorker


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


def test_dummy_scan_can_complete_immediately_and_inject_an_error():
    device = DummyCT400(scan_duration=0)
    device.start_scan()
    assert device.scan_wait_end() == (CT400StatusCode.SCAN_COMPLETED, "")

    failing_device = DummyCT400(scan_duration=0, scan_error="simulated scan failure")
    failing_device.start_scan()
    assert failing_device.scan_wait_end() == (-1, "simulated scan failure")
    assert failing_device.scan_wait_end() == (CT400StatusCode.SCAN_COMPLETED, "")


def test_scan_worker_acquires_dummy_data_and_cleans_up(monkeypatch):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500.0, 1501.0, 100, 1.0, LaserInput.LI_1)
    completed, errors, finished = [], [], []
    worker.completed_signal.connect(lambda *data: completed.append(data))
    worker.error_signal.connect(errors.append)
    worker.finished.connect(lambda: finished.append(True))

    worker.do_scan()

    assert errors == []
    assert len(completed) == 1
    wavelengths, powers, pout = completed[0]
    np.testing.assert_allclose(wavelengths, np.linspace(1500.0, 1501.0, 11))
    assert powers.shape == (1, 11)
    assert pout == -20.0
    assert finished == [True]
    assert not device._is_scanning
    assert not device._laser_enabled


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
    assert errors[0].code == CT400StatusCode.SCAN_ERROR_GENERIC
    assert errors[0].message == "simulated scan failure"
    assert finished == [True]
    assert not device._is_scanning
    assert not device._laser_enabled


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
