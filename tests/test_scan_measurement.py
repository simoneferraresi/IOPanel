from datetime import UTC, datetime

import numpy as np
import pytest

from hardware.ct400_types import CT400ScanResultKind, Detector, LaserInput
from logic.scan_measurement import ScanAcquisitionSettings, ScanMeasurement


def _settings(detectors=(Detector.DE_1,)):
    return ScanAcquisitionSettings(
        requested_start_wavelength_nm=1500.0,
        requested_end_wavelength_nm=1501.0,
        requested_resolution_pm=100,
        requested_speed_nm_s="5",
        entered_laser_power="3",
        entered_laser_power_unit="dBm",
        laser_power_mw=1.995,
        laser_input=LaserInput.LI_2,
        detectors=detectors,
    )


def _measurement(settings, wavelengths, detector_data, detectors=None, completed_at_utc=None):
    return ScanMeasurement(
        settings=settings,
        wavelengths_nm=wavelengths,
        detector_data=detector_data,
        detectors=settings.detectors if detectors is None else detectors,
        final_pout=None,
        result_kind=CT400ScanResultKind.SUCCESS,
        raw_result_code=0,
        result_message="",
        backend="test.Backend",
        simulated=True,
        completed_at_utc=completed_at_utc or datetime.now(UTC),
    )


def test_ordered_noncontiguous_detector_rows_are_preserved():
    detectors = (Detector.DE_3, Detector.DE_1)
    settings = _settings(detectors)
    detector_data = np.array([[-30.0, -31.0], [-10.0, -11.0]])

    measurement = _measurement(settings, np.array([1500.0, 1501.0]), detector_data)

    assert measurement.detectors == (Detector.DE_3, Detector.DE_1)
    assert measurement.detectors[0] is Detector.DE_3
    assert measurement.detectors[1] is Detector.DE_1
    assert measurement.detector_data.shape == (2, 2)
    np.testing.assert_array_equal(measurement.detector_data[0], [-30.0, -31.0])
    np.testing.assert_array_equal(measurement.detector_data[1], [-10.0, -11.0])


def test_detector_iterables_are_normalized_to_immutable_ordered_tuples():
    detector_list = [Detector.DE_3, Detector.DE_1]
    settings = _settings(detector_list)
    assert settings.detectors == (Detector.DE_3, Detector.DE_1)
    assert isinstance(settings.detectors, tuple)

    measurement = _measurement(
        settings,
        np.array([1500.0]),
        np.array([[-30.0], [-10.0]]),
        detectors=detector_list,
    )
    detector_list.reverse()
    assert measurement.detectors == (Detector.DE_3, Detector.DE_1)
    assert isinstance(measurement.detectors, tuple)


@pytest.mark.parametrize("detectors", [(Detector.DE_1, Detector.DE_1), [Detector.DE_2, Detector.DE_2]])
def test_duplicate_detectors_are_rejected(detectors):
    with pytest.raises(ValueError, match="duplicates"):
        _settings(detectors)


@pytest.mark.parametrize("invalid", [1, "DE_1", None])
def test_non_detector_values_are_rejected(invalid):
    with pytest.raises(TypeError, match="Detector"):
        _settings((Detector.DE_1, invalid))


def test_pout_cannot_be_used_as_a_wavelength_resolved_detector():
    with pytest.raises(ValueError, match="final_pout"):
        _settings((Detector.POUT,))


def test_detector_five_is_not_filtered_by_a_fixed_channel_mask():
    settings = _settings((Detector.DE_5,))
    measurement = _measurement(settings, np.array([1500.0]), np.array([[-5.0]]))
    assert measurement.detectors == (Detector.DE_5,)


@pytest.mark.parametrize("wavelengths", [np.array(1.0), np.array([[1.0, 2.0]])])
def test_wavelengths_must_be_one_dimensional(wavelengths):
    settings = _settings()
    with pytest.raises(ValueError, match="wavelengths_nm must be 1D"):
        _measurement(settings, wavelengths, np.array([[-1.0, -2.0]]))


@pytest.mark.parametrize("detector_data", [np.array(-1.0), np.array([-1.0, -2.0]), np.zeros((1, 1, 2))])
def test_detector_data_must_be_two_dimensional(detector_data):
    settings = _settings()
    with pytest.raises(ValueError, match="detector_data must be 2D"):
        _measurement(settings, np.array([1.0, 2.0]), detector_data)


@pytest.mark.parametrize("detector_data", [np.zeros((1, 2)), np.zeros((3, 2))])
def test_detector_row_count_must_match_detector_tuple(detector_data):
    settings = _settings((Detector.DE_3, Detector.DE_1))
    with pytest.raises(ValueError, match=r"shape \(2, 2\)"):
        _measurement(settings, np.array([1.0, 2.0]), detector_data)


def test_detector_row_count_rejects_zero_rows_for_nonempty_detector_tuple():
    settings = _settings((Detector.DE_1,))

    with pytest.raises(ValueError, match=r"shape \(1, 3\)"):
        _measurement(
            settings,
            np.array([1.0, 2.0, 3.0]),
            np.empty((0, 3)),
        )


@pytest.mark.parametrize("detector_data", [np.zeros((1, 2)), np.zeros((1, 4))])
def test_detector_column_count_must_match_wavelength_count(detector_data):
    settings = _settings()
    with pytest.raises(ValueError, match=r"shape \(1, 3\)"):
        _measurement(settings, np.array([1.0, 2.0, 3.0]), detector_data)


@pytest.mark.parametrize(
    "measurement_detectors",
    [(Detector.DE_2,), (Detector.DE_2, Detector.DE_1)],
)
def test_measurement_detectors_must_match_settings_identity_and_order(measurement_detectors):
    settings = _settings((Detector.DE_1, Detector.DE_2))
    with pytest.raises(ValueError, match="identity and order"):
        _measurement(
            settings,
            np.array([1.0]),
            np.zeros((len(measurement_detectors), 1)),
            detectors=measurement_detectors,
        )


def test_empty_wavelength_scan_keeps_canonical_empty_column_shape():
    settings = _settings((Detector.DE_3, Detector.DE_1))
    measurement = _measurement(settings, np.array([]), np.empty((2, 0)))
    assert measurement.wavelengths_nm.shape == (0,)
    assert measurement.detector_data.shape == (2, 0)


def test_measurement_owns_defensive_read_only_array_copies():
    settings = _settings((Detector.DE_3, Detector.DE_1))
    wavelengths = np.array([1500.0, 1501.0])
    detector_data = np.array([[-30.0, -31.0], [-10.0, -11.0]])

    measurement = _measurement(settings, wavelengths, detector_data)

    wavelengths[0] = 999.0
    detector_data[0, 0] = 999.0
    np.testing.assert_array_equal(measurement.wavelengths_nm, [1500.0, 1501.0])
    np.testing.assert_array_equal(measurement.detector_data, [[-30.0, -31.0], [-10.0, -11.0]])
    with pytest.raises(ValueError):
        measurement.wavelengths_nm[0] = 0
    with pytest.raises(ValueError):
        measurement.detector_data[0, 0] = 0


def test_naive_completion_timestamp_is_rejected():
    settings = _settings()
    with pytest.raises(ValueError, match="timezone-aware"):
        _measurement(
            settings,
            np.array([1.0]),
            np.array([[-1.0]]),
            completed_at_utc=datetime(2026, 1, 1),  # noqa: DTZ001 — This test verifies naive timestamps are rejected.
        )
