import math

import pytest

from logic.scan_eta import estimate_scan_duration, estimate_sweep_seconds, format_duration


@pytest.mark.parametrize(
    ("start", "end", "speed", "expected"),
    [(1550, 1552, 2, 1), (1552, 1550, 2, 1), (1550, 1550, 1, 0), (1, 2, 0.25, 4)],
)
def test_estimate_sweep_seconds(start, end, speed, expected):
    assert estimate_sweep_seconds(start, end, speed) == expected


@pytest.mark.parametrize(
    ("start", "end", "speed"),
    [("bad", 2, 1), (1, "bad", 1), (1, 2, "bad"), (1, 2, 0), (1, 2, -1), (math.inf, 2, 1), (1, 2, math.nan)],
)
def test_invalid_estimate_is_unavailable(start, end, speed):
    assert estimate_sweep_seconds(start, end, speed) is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (0.001, "00:01"), (59, "00:59"), (59.001, "01:00"), (3600, "60:00"), (86400, "1440:00")],
)
def test_duration_format_rounds_up(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("seconds", [-0.1, math.inf, math.nan])
def test_duration_format_rejects_invalid_values(seconds):
    with pytest.raises(ValueError):
        format_duration(seconds)


@pytest.mark.parametrize(
    ("start", "end", "speed", "resolution", "expected"),
    [
        (1530, 1535, 10, 1, 6.362),
        (1530, 1545, 10, 1, 8.186),
        (1530, 1565, 10, 1, 11.834),
        (1530, 1565, 10, 10, 9.6584),
        (1530, 1565, 5, 1, 17.374),
    ],
)
def test_empirical_ct400_calibration_matches_physical_case_medians(start, end, speed, resolution, expected):
    estimate = estimate_scan_duration(start, end, speed, resolution, 1)
    assert estimate is not None
    assert estimate.calibrated is True
    assert estimate.seconds == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize(
    ("span", "speed", "resolution", "detectors"),
    [
        (4, 10, 1, 1),
        (40, 10, 1, 1),
        (35, 3, 1, 1),
        (35, 5, 10, 1),
        (15, 5, 1, 1),
        (15, 10, 10, 1),
        (35, 10, 5, 1),
        (35, 10, 1, 4),
        (35, 10, "invalid", 1),
        (35, 10, 1, None),
    ],
)
def test_unsupported_calibration_settings_fall_back_to_nominal(span, speed, resolution, detectors):
    estimate = estimate_scan_duration(1530, 1530 + span, speed, resolution, detectors)
    assert estimate is not None
    assert estimate.calibrated is False
    assert estimate.seconds == pytest.approx(span / speed)


def test_invalid_nominal_scan_has_no_empirical_eta():
    assert estimate_scan_duration(1530, 1565, None, 1, 1) is None
    assert estimate_scan_duration(1530, 1565, 0, 1, 1) is None


def test_empirical_eta_does_not_extrapolate_to_untested_absolute_wavelengths():
    estimate = estimate_scan_duration(1570, 1605, 10, 1, 1)
    assert estimate is not None
    assert not estimate.calibrated
    assert estimate.seconds == pytest.approx(3.5)
