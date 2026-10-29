"""Driver-free CT400 wavelength-resolved output-power regression tests."""

import io
from ctypes import POINTER, c_double, c_int32, c_uint64
from datetime import UTC, datetime

import numpy as np
import pytest
from scipy.io import savemat

from hardware.ct400 import CT400, CT400CommunicationError
from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from hardware.dummy_ct400 import DummyCT400
from logic.scan_export import CSV_COLUMNS, CSV_COLUMNS_WITH_OUTPUT_POWER, build_scan_export_v2
from logic.scan_import import ScanImportError, load_scan
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement
from ui.control_panel import ScanWorker


class _NativeFunction:
    def __init__(self, values=(), returned=None):
        self.values = values
        self.returned = returned
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        if len(args) == 3:
            _handle, buffer, count = args
            for i, value in enumerate(self.values[:count]):
                buffer[i] = value
            return count if self.returned is None else self.returned
        return 0


class _NativeDLL:
    def __init__(self, values=(0.2, -0.3, 1.5), returned=None):
        self.power = _NativeFunction(values=values, returned=returned)
        self.other = {}

    def __getattr__(self, name):
        if name == "CT400_ScanGetPowerResampledArray":
            return self.power
        if name.startswith("CT400_"):
            return self.other.setdefault(name, _NativeFunction())
        raise AttributeError(name)


def _device(returned=None):
    device = CT400.__new__(CT400)
    device.handle = 0x1234
    device.dll = _NativeDLL(returned=returned)
    device._configure_function_signatures()
    return device


def test_vendor_power_abi_and_lossless_buffer_copy():
    device = _device()
    assert device.dll.power.argtypes == [c_uint64, POINTER(c_double), c_int32]
    assert device.dll.power.restype is c_int32
    first = device.get_output_power_resampled(3)
    np.testing.assert_array_equal(first, [0.2, -0.3, 1.5])
    assert device.dll.power.calls[0][0] == 0x1234
    assert device.dll.power.calls[0][2] == 3
    first[0] = 123
    np.testing.assert_array_equal(device.get_output_power_resampled(3), [0.2, -0.3, 1.5])


@pytest.mark.parametrize("returned", [-1, 2])
def test_vendor_power_errors_are_not_mistaken_for_valid_samples(returned):
    device = _device(returned=returned)
    with pytest.raises(CT400CommunicationError):
        device.get_output_power_resampled(3)


def test_vendor_power_requires_handle_and_nonnegative_count():
    device = _device()
    device.handle = None
    with pytest.raises(CT400CommunicationError, match="initialized"):
        device.get_output_power_resampled(3)
    assert device.dll.power.calls == []
    device.handle = 0x1234
    with pytest.raises(ValueError, match="non-negative"):
        device.get_output_power_resampled(-1)
    assert device.get_output_power_resampled(0).shape == (0,)
    assert device.dll.power.calls == []


def _measurement(power_trace):
    detectors = (Detector.DE_3, Detector.DE_1)
    settings = ScanAcquisitionSettings(1500.0, 1502.0, 10, "5", "1", "mW", 1.0, LaserInput.LI_1, detectors)
    return ScanMeasurement(
        settings=settings,
        wavelengths_nm=np.array([1500.0, 1501.0, 1502.0]),
        detector_data=np.array([[-30.0, -31.0, -32.0], [-10.0, -11.0, -12.0]]),
        detectors=detectors,
        final_pout=-0.1,
        result_kind=CT400ScanResultKind.SUCCESS,
        raw_result_code=0,
        result_message="",
        backend="test.backend",
        simulated=True,
        completed_at_utc=datetime(2026, 10, 29, tzinfo=UTC),
        laser_output_power_db=power_trace,
    )


def _write_csv(path, payload):
    buffer = io.StringIO()
    np.savetxt(buffer, payload.csv_data, delimiter=",", header=payload.csv_header, comments="", fmt="%.6f")
    path.write_text(buffer.getvalue(), encoding="utf-8")


@pytest.mark.parametrize("format", ["csv", "mat"])
def test_power_trace_round_trip_is_wavelength_aligned_and_distinct_from_final_pout(tmp_path, format):
    measurement = _measurement(np.array([0.12, -0.34, 0.56]))
    payload = build_scan_export_v2(measurement)
    assert payload.csv_data.shape == (3, 6)
    assert payload.csv_header.endswith("# " + ", ".join(CSV_COLUMNS_WITH_OUTPUT_POWER))
    np.testing.assert_array_equal(payload.csv_data[:, 5], [0.12, -0.34, 0.56])
    np.testing.assert_array_equal(payload.mat_data["laser_output_power_dB"], [0.12, -0.34, 0.56])
    assert payload.mat_data["pout_dBm"] == -0.1
    path = tmp_path / ("power.csv" if format == "csv" else "power.mat")
    if format == "csv":
        _write_csv(path, payload)
    else:
        savemat(path, payload.mat_data)
    imported = load_scan(path)
    np.testing.assert_array_equal(imported.laser_output_power_db, [0.12, -0.34, 0.56])
    np.testing.assert_array_equal(imported.detector_data, measurement.detector_data)
    assert imported.final_pout == -0.1
    assert not imported.laser_output_power_db.flags.writeable


@pytest.mark.parametrize("format", ["csv", "mat"])
def test_legacy_five_column_export_still_loads_without_power_trace(tmp_path, format):
    payload = build_scan_export_v2(_measurement(None))
    assert payload.csv_data.shape == (3, 5)
    assert payload.csv_header.endswith("# " + ", ".join(CSV_COLUMNS))
    assert "laser_output_power_dB" not in payload.mat_data
    path = tmp_path / ("legacy.csv" if format == "csv" else "legacy.mat")
    if format == "csv":
        _write_csv(path, payload)
    else:
        savemat(path, payload.mat_data)
    imported = load_scan(path)
    assert imported.laser_output_power_db is None


def test_power_trace_measurement_is_defensively_copied_and_shape_checked():
    raw = np.array([1.0, 2.0, 3.0])
    measurement = _measurement(raw)
    raw[0] = 900
    np.testing.assert_array_equal(measurement.laser_output_power_db, [1, 2, 3])
    with pytest.raises(ValueError):
        measurement.laser_output_power_db[0] = 5
    with pytest.raises(ValueError, match="laser_output_power_db must have shape"):
        _measurement([1, 2])


def test_malformed_extended_csv_is_rejected(tmp_path):
    payload = build_scan_export_v2(_measurement([1.0, 2.0, 3.0]))
    path = tmp_path / "bad.csv"
    path.write_text(payload.csv_header + "\n1500,1,2,3,4\n", encoding="utf-8")
    with pytest.raises(ScanImportError, match="unexpected number of columns"):
        load_scan(path)


def test_malformed_extended_mat_is_rejected(tmp_path):
    payload = build_scan_export_v2(_measurement([1.0, 2.0, 3.0]))
    payload.mat_data["laser_output_power_dB"] = np.array([1.0, 2.0])
    path = tmp_path / "bad.mat"
    savemat(path, payload.mat_data)
    with pytest.raises(ScanImportError, match="Output power must contain exactly one sample"):
        load_scan(path)


def test_scan_worker_captures_synthetic_power_without_extra_laser_commands(monkeypatch, qapp):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)
    worker = ScanWorker(device, 1500, 1501, 100, 1.0, LaserInput.LI_1)
    measurements = []
    errors = []
    worker.measurement_ready.connect(measurements.append)
    worker.error_signal.connect(errors.append)
    worker.do_scan()
    assert not errors
    assert len(measurements) == 1
    expected = device.get_output_power_resampled(11)
    np.testing.assert_array_equal(measurements[0].laser_output_power_db, expected)
    assert len(device.cmd_laser_calls) == 2
    assert not worker.recovery_required


def test_optional_power_error_preserves_completed_detector_acquisition(monkeypatch, qapp):
    monkeypatch.setattr(ScanWorker, "_LASER_COMMAND_DELAY_MS", 0)
    device = DummyCT400(scan_duration=0)

    def fail_power(_n):
        raise RuntimeError("Reference unavailable")

    monkeypatch.setattr(device, "get_output_power_resampled", fail_power)
    worker = ScanWorker(device, 1500, 1501, 100, 1.0, LaserInput.LI_1)
    measurements, errors = [], []
    worker.measurement_ready.connect(measurements.append)
    worker.error_signal.connect(errors.append)
    worker.do_scan()
    assert not errors
    assert len(measurements) == 1
    assert measurements[0].laser_output_power_db is None
    assert measurements[0].detector_data.shape == (1, 11)
    assert not worker.recovery_required
    assert len(device.cmd_laser_calls) == 2
