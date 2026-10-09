import math

import pytest

from logic.scan_eta import estimate_sweep_seconds, format_duration


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
