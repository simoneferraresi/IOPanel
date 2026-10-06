from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from scipy.io import loadmat, savemat

from hardware.ct400_types import Detector, LaserInput
from logic.power_monitor_export import (
    POWER_MONITOR_CSV_COLUMNS,
    POWER_MONITOR_EXPORT_SCHEMA_VERSION,
    build_power_monitor_export_v1,
    default_power_monitor_export_name,
    derive_power_monitor_export_targets,
)
from logic.power_monitor_recording import (
    PowerMonitorAcquisitionSettings,
    PowerMonitorRecording,
    PowerMonitorRecordingStopReason,
)


def _settings(detectors):
    return PowerMonitorAcquisitionSettings(
        1550.5,
        "1.25",
        "mW",
        1.25,
        LaserInput.LI_2,
        detectors,
        237,
    )


def _recording(detectors=(Detector.DE_3, Detector.DE_1), elapsed=None, detector_data=None, pout=None, **overrides):
    elapsed = np.asarray([0.0, 0.27, 0.66, 1.41] if elapsed is None else elapsed, dtype=float)
    detector_data = np.asarray([[30, 31, 32, 33], [10, 11, 12, 13]] if detector_data is None else detector_data)
    pout = np.asarray([-5, -4, -3, -2] if pout is None else pout, dtype=float)
    started = datetime(2026, 10, 5, 13, 20, 15, 123456, tzinfo=UTC)
    values = {
        "settings": _settings(detectors),
        "elapsed_s": elapsed,
        "pout_data": pout,
        "detector_data": detector_data,
        "detectors": detectors,
        "started_at_utc": started,
        "completed_at_utc": started + timedelta(seconds=2),
        "duration_s": 2.0,
        "backend": "hardware.ct400.CT400",
        "simulated": False,
        "stop_reason": PowerMonitorRecordingStopReason.USER_STOPPED,
    }
    values.update(overrides)
    return PowerMonitorRecording(**values)


def test_export_maps_detector_rows_by_identity_preserves_irregular_time_and_pout():
    recording = _recording()
    payload = build_power_monitor_export_v1(recording)

    assert POWER_MONITOR_EXPORT_SCHEMA_VERSION == 1
    assert "# SchemaVersion: 1" in payload.csv_header
    assert payload.csv_header.endswith("# " + ", ".join(POWER_MONITOR_CSV_COLUMNS))
    np.testing.assert_array_equal(payload.csv_data[:, 0], [0.0, 0.27, 0.66, 1.41])
    np.testing.assert_array_equal(payload.csv_data[:, 1], recording.pout_data)
    np.testing.assert_array_equal(payload.csv_data[:, 2], [10, 11, 12, 13])
    assert np.isnan(payload.csv_data[:, 3]).all()
    np.testing.assert_array_equal(payload.csv_data[:, 4], [30, 31, 32, 33])
    assert np.isnan(payload.csv_data[:, 5]).all()
    assert payload.mat_data["detector_unit"] == "dBm"
    assert payload.mat_data["pout_unit"] == "dBm"


def test_export_preserves_nonfinite_values_and_zero_detector_recording():
    recording = _recording(
        detectors=(),
        detector_data=np.empty((0, 4)),
        pout=[np.nan, np.inf, -np.inf, 0.0],
    )
    payload = build_power_monitor_export_v1(recording)

    np.testing.assert_array_equal(payload.csv_data[:, 0], recording.elapsed_s)
    np.testing.assert_array_equal(payload.csv_data[:, 1], recording.pout_data)
    assert np.isnan(payload.csv_data[:, 2:]).all()
    assert np.isnan(payload.mat_data["power_det_1_dBm"]).all()
    assert payload.mat_data["active_detector_count"] == 0
    assert payload.mat_data["active_detectors"] == ""
    assert payload.mat_data["optical_detector_mask_de1_de4"].tolist() == [0, 0, 0, 0]


def test_export_metadata_comment_normalization_mat_round_trip_and_immutability(tmp_path):
    recording = _recording()
    elapsed_before = recording.elapsed_s.copy()
    pout_before = recording.pout_data.copy()
    detector_before = recording.detector_data.copy()
    comment = 'line one\r\nline two\rquoted "text" # µ'
    normalized = 'line one\nline two\nquoted "text" # µ'

    payload = build_power_monitor_export_v1(recording, comment=comment)
    for key in (
        "MeasurementType",
        "SchemaVersion",
        "StartedAtUTC",
        "CompletedAtUTC",
        "Duration_s",
        "SampleCount",
        "Wavelength_nm",
        "RequestedPollInterval_ms",
        "EnteredLaserPower",
        "EnteredLaserPowerUnit",
        "LaserPower_mW",
        "LaserInput",
        "ActiveDetectorCount",
        "ActiveDetectors",
        "OpticalDetectorMask(DE1-DE4)",
        "Backend",
        "Simulated",
        "StopReason",
        "DetectorUnit",
        "PoutUnit",
        "Comment",
    ):
        assert f"# {key}:" in payload.csv_header
    assert "# StartedAtUTC: 2026-10-05T13:20:15.123456Z" in payload.csv_header
    assert "# RequestedPollInterval_ms: 237" in payload.csv_header
    assert '"Comment": "' not in payload.csv_header
    assert normalized in payload.mat_data["comment"]
    assert "# Comment: " in payload.csv_header
    import json

    comment_line = next(line for line in payload.csv_header.splitlines() if line.startswith("# Comment: "))
    assert json.loads(comment_line.removeprefix("# Comment: ")) == normalized
    assert payload.mat_data["sample_count"] == 4
    assert payload.mat_data["active_detectors"] == "DE_3,DE_1"
    assert payload.mat_data["optical_detector_mask_de1_de4"].tolist() == [1, 0, 1, 0]
    assert payload.mat_data["wavelength_nm"] == 1550.5
    assert payload.mat_data["requested_poll_interval_ms"] == 237
    assert payload.mat_data["entered_laser_power"] == "1.25"
    assert payload.mat_data["entered_laser_power_unit"] == "mW"
    assert payload.mat_data["laser_power_mw"] == 1.25
    assert payload.mat_data["laser_input"] == "LI_2"
    assert payload.mat_data["duration_s"] == 2.0
    assert payload.mat_data["simulated"] is False
    assert payload.mat_data["stop_reason"] == "USER_STOPPED"

    mat_path = tmp_path / "roundtrip.mat"
    savemat(mat_path, payload.mat_data, do_compression=True)
    round_trip = loadmat(mat_path, simplify_cells=True)
    assert round_trip["comment"] == normalized
    assert round_trip["detector_unit"] == "dBm"

    np.testing.assert_array_equal(recording.elapsed_s, elapsed_before)
    np.testing.assert_array_equal(recording.pout_data, pout_before)
    np.testing.assert_array_equal(recording.detector_data, detector_before)
    assert recording.settings.detectors == (Detector.DE_3, Detector.DE_1)


def test_export_rejects_zero_samples():
    recording = _recording(
        elapsed=[],
        detector_data=np.empty((2, 0)),
        pout=[],
        duration_s=0.0,
    )
    with pytest.raises(ValueError, match="no captured samples"):
        build_power_monitor_export_v1(recording)


def test_selected_detector_nonfinite_values_are_copied_exactly():
    values = np.asarray([[np.nan, np.inf, -np.inf, 0.0]])
    recording = _recording(
        detectors=(Detector.DE_2,),
        detector_data=values,
    )
    payload = build_power_monitor_export_v1(recording)
    np.testing.assert_array_equal(payload.csv_data[:, 3], values[0])
    assert np.isnan(payload.csv_data[:, 2]).all()
    np.testing.assert_array_equal(payload.mat_data["power_det_2_dBm"], values[0])


@pytest.mark.parametrize(
    ("selected", "include_csv", "include_mat", "expected"),
    [
        ("recording", True, False, {"CSV": "recording.csv"}),
        ("recording.csv", True, True, {"CSV": "recording.csv", "MAT": "recording.mat"}),
        ("recording.mat", False, True, {"MAT": "recording.mat"}),
        ("recording.CSV", True, True, {"CSV": "recording.csv", "MAT": "recording.mat"}),
        ("recording.MAT", True, True, {"CSV": "recording.csv", "MAT": "recording.mat"}),
    ],
)
def test_export_target_derivation_is_format_specific(selected, include_csv, include_mat, expected):
    targets = derive_power_monitor_export_targets(
        selected,
        include_csv=include_csv,
        include_mat=include_mat,
    )
    assert {key: value.name for key, value in targets.items()} == expected
    assert all(path.suffix.lower() in {".csv", ".mat"} for path in targets.values())


def test_default_filename_uses_recording_start_utc_not_save_time():
    recording = _recording(started_at_utc=datetime(2026, 10, 5, 13, 20, 15, tzinfo=UTC))
    assert default_power_monitor_export_name(recording) == "power_monitor_20261005T132015Z"
