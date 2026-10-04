from datetime import UTC, datetime, timedelta, timezone
import json

import numpy as np
import pytest

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_export import CSV_COLUMNS, SCAN_EXPORT_SCHEMA_VERSION, build_scan_export_v2
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement


def _measurement(detectors, rows, *, completed_at=None, final_pout=-20.0):
    detector_tuple = tuple(detectors)
    settings = ScanAcquisitionSettings(
        requested_start_wavelength_nm=1510.0,
        requested_end_wavelength_nm=1520.0,
        requested_resolution_pm=25,
        requested_speed_nm_s="7",
        entered_laser_power="3",
        entered_laser_power_unit="dBm",
        laser_power_mw=1.9952623149688795,
        laser_input=LaserInput.LI_2,
        detectors=detector_tuple,
    )
    return ScanMeasurement(
        settings=settings,
        wavelengths_nm=np.array([1510.0, 1515.0, 1520.0]),
        detector_data=np.asarray(rows),
        detectors=detector_tuple,
        final_pout=final_pout,
        result_kind=CT400ScanResultKind.SUCCESS,
        raw_result_code=0,
        result_message="",
        backend="hardware.dummy_ct400.DummyCT400",
        simulated=True,
        completed_at_utc=completed_at or datetime(2026, 9, 29, 12, 34, 56, tzinfo=UTC),
    )


def test_schema_v2_maps_noncontiguous_acquisition_order_by_detector_identity():
    measurement = _measurement((Detector.DE_3, Detector.DE_1), [[30, 31, 32], [10, 11, 12]])

    payload = build_scan_export_v2(measurement)

    assert payload.csv_header.endswith("# " + ", ".join(CSV_COLUMNS))
    np.testing.assert_array_equal(payload.csv_data[:, 1], [10, 11, 12])
    np.testing.assert_array_equal(payload.csv_data[:, 2], [0, 0, 0])
    np.testing.assert_array_equal(payload.csv_data[:, 3], [30, 31, 32])
    np.testing.assert_array_equal(payload.csv_data[:, 4], [0, 0, 0])
    np.testing.assert_array_equal(payload.mat_data["transfer_function_det_1_dB"], [10, 11, 12])
    np.testing.assert_array_equal(payload.mat_data["transfer_function_det_2_dB"], [0, 0, 0])
    np.testing.assert_array_equal(payload.mat_data["transfer_function_det_3_dB"], [30, 31, 32])
    np.testing.assert_array_equal(payload.mat_data["transfer_function_det_4_dB"], [0, 0, 0])


def test_schema_v2_fixed_arrays_do_not_depend_on_acquisition_row_order():
    first = build_scan_export_v2(_measurement((Detector.DE_3, Detector.DE_1), [[30, 31, 32], [10, 11, 12]]))
    second = build_scan_export_v2(_measurement((Detector.DE_1, Detector.DE_3), [[10, 11, 12], [30, 31, 32]]))

    np.testing.assert_array_equal(first.csv_data, second.csv_data)
    assert first.mat_data["active_detector_ids"].tolist() == [3, 1]
    assert second.mat_data["active_detector_ids"].tolist() == [1, 3]


def test_schema_v2_preserves_nonfinite_acquired_values_and_timestamp():
    timestamp = datetime(2026, 9, 29, 14, 34, 56, tzinfo=timezone(timedelta(hours=2)))
    measurement = _measurement((Detector.DE_2,), [[np.nan, np.inf, -np.inf]], completed_at=timestamp, final_pout=None)

    payload = build_scan_export_v2(measurement)

    assert np.isnan(payload.mat_data["transfer_function_det_2_dB"][0])
    assert np.isposinf(payload.mat_data["transfer_function_det_2_dB"][1])
    assert np.isneginf(payload.mat_data["transfer_function_det_2_dB"][2])
    assert "# CompletedAtUTC: 2026-09-29T12:34:56Z" in payload.csv_header
    assert payload.mat_data["completed_at_utc"] == "2026-09-29T12:34:56Z"
    assert "Pout(dBm)" not in payload.csv_header
    assert "pout_dBm" not in payload.mat_data


def test_schema_v2_contains_version_active_metadata_and_scalar_pout():
    payload = build_scan_export_v2(_measurement((Detector.DE_3, Detector.DE_1), [[30, 31, 32], [10, 11, 12]]))

    assert SCAN_EXPORT_SCHEMA_VERSION == 2
    assert "# SchemaVersion: 2" in payload.csv_header
    assert "# ActiveDetectorCount: 2" in payload.csv_header
    assert "# ActiveDetectors: DE_3,DE_1" in payload.csv_header
    assert "# OpticalDetectorMask(DE1-DE4): 1,0,1,0" in payload.csv_header
    assert payload.mat_data["active_detector_count"] == 2
    assert payload.mat_data["active_detector_ids"].tolist() == [3, 1]
    assert payload.mat_data["active_detector_mask"].tolist() == [1, 0, 1, 0]
    assert payload.mat_data["pout_dBm"] == -20.0
    assert payload.csv_header.count("Pout(dBm)") == 1
    assert "Pout_[dBm]" not in payload.csv_header
    assert "Power_Det1_[dB]" not in payload.csv_header
    assert "Power_Det_1_[dB]" not in payload.csv_header


@pytest.mark.parametrize(
    ("comment", "expected"),
    [
        ("Device thermally stabilized before scan.", "Device thermally stabilized before scan."),
        (
            'First pass, TE mode.\r\nChanged "polarization" #2.\rSecond pass:\nstable → yes',
            'First pass, TE mode.\nChanged "polarization" #2.\nSecond pass:\nstable → yes',
        ),
        ("", ""),
        ("café 測定 🧪", "café 測定 🧪"),
    ],
)
def test_schema_v2_comment_csv_and_mat_round_trip(tmp_path, comment, expected):
    import scipy.io as sio

    payload = build_scan_export_v2(
        _measurement((Detector.DE_1,), [[10, 11, 12]]),
        comment=comment,
    )

    comment_lines = [line for line in payload.csv_header.splitlines() if line.startswith("# Comment: ")]
    assert len(comment_lines) == 1
    encoded = comment_lines[0].removeprefix("# Comment: ")
    assert json.loads(encoded) == expected
    assert payload.mat_data["comment"] == expected
    assert SCAN_EXPORT_SCHEMA_VERSION == 2
    assert payload.mat_data["schema_version"] == 2

    mat_path = tmp_path / "comment.mat"
    sio.savemat(mat_path, payload.mat_data, do_compression=True)
    loaded = sio.loadmat(mat_path)["comment"]
    assert "".join(np.asarray(loaded).astype(str).ravel().tolist()) == expected


def test_export_comment_does_not_mutate_measurement():
    measurement = _measurement((Detector.DE_1,), [[10, 11, 12]])
    settings = measurement.settings
    wavelengths = measurement.wavelengths_nm.copy()
    detector_data = measurement.detector_data.copy()
    acquisition_values = (
        measurement.detectors,
        measurement.final_pout,
        measurement.result_kind,
        measurement.raw_result_code,
        measurement.result_message,
        measurement.backend,
        measurement.simulated,
        measurement.completed_at_utc,
    )

    build_scan_export_v2(measurement, comment="saved with notes")

    assert measurement.settings is settings
    np.testing.assert_array_equal(measurement.wavelengths_nm, wavelengths)
    np.testing.assert_array_equal(measurement.detector_data, detector_data)
    assert (
        measurement.detectors,
        measurement.final_pout,
        measurement.result_kind,
        measurement.raw_result_code,
        measurement.result_message,
        measurement.backend,
        measurement.simulated,
        measurement.completed_at_utc,
    ) == acquisition_values


@pytest.mark.parametrize(
    ("detectors", "rows", "message"),
    [
        ((Detector.DE_5,), [[1, 2, 3]], "External/BNC"),
        ((), np.empty((0, 3)), "no wavelength-resolved detector data"),
    ],
)
def test_schema_v2_rejects_unsafe_or_empty_detector_exports(detectors, rows, message):
    with pytest.raises(ValueError, match=message):
        build_scan_export_v2(_measurement(detectors, rows, final_pout=None))
