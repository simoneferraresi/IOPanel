"""Pure nominal and lab-calibrated CT400 scan ETA helpers.

The empirical coefficients come from 51 physical single-detector runs across
four operator-supervised campaigns (October 2026). They represent *GUI total
duration*, not a speed readback, and are only used within observed settings.
"""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ScanTimeEstimate:
    seconds: float
    calibrated: bool


def estimate_sweep_seconds(start_nm: object, end_nm: object, speed_nm_s: object) -> float | None:
    """Return nominal wavelength sweep seconds, or ``None`` for invalid inputs."""
    try:
        start = float(start_nm)
        end = float(end_nm)
        speed = float(speed_nm_s)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) for value in (start, end, speed)) or speed <= 0:
        return None
    return abs(end - start) / speed


def estimate_scan_duration(
    start_nm: object,
    end_nm: object,
    speed_nm_s: object,
    resolution_pm: object,
    detector_count: object,
) -> ScanTimeEstimate | None:
    """Estimate full CT400 GUI scan time for supported lab conditions.

    Supported conditions: one detector, wavelengths within 1530-1565 nm,
    a 5-35 nm span at (10 nm/s, 1 pm), or a 35 nm span at
    (10 nm/s, 10 pm) or (5 nm/s, 1 pm). The 5 nm/s correction is
    provisional (only one campaign). All other settings retain the nominal
    sweep estimate, flagged as not calibrated.

    The model is a descriptive laboratory fit, not instrument telemetry.
    """
    sweep = estimate_sweep_seconds(start_nm, end_nm, speed_nm_s)
    if sweep is None:
        return None
    try:
        span = abs(float(end_nm) - float(start_nm))
        speed = float(speed_nm_s)
        resolution = float(resolution_pm)
        count = int(detector_count)
    except (TypeError, ValueError, OverflowError):
        return ScanTimeEstimate(sweep, calibrated=False)

    if (
        not math.isfinite(resolution)
        or not 1530.0 <= float(start_nm) <= 1565.0
        or not 1530.0 <= float(end_nm) <= 1565.0
        or not 5.0 <= span <= 35.0
        or (speed, resolution) not in ((10.0, 1.0), (10.0, 10.0), (5.0, 1.0))
        or (resolution == 10.0 and abs(span - 35.0) > 1e-6)
        or (speed == 5.0 and abs(span - 35.0) > 1e-6)
        or count != 1
    ):
        return ScanTimeEstimate(sweep, calibrated=False)

    # Least-squares fit to real total GUI durations (success + timing-valid
    # vendor warnings 101/117). Rounded coefficients yield 0.046 s in-sample
    # MAE; leave-one-campaign-out MAE for the 10 nm/s block is 0.033-0.080 s.
    # The 5 nm/s offset is not independently campaign-validated.
    sample_intervals = math.ceil(span * 1000.0 / resolution)
    overhead = 5.45 + 0.0000824 * sample_intervals
    if speed == 5.0:
        overhead += 2.04
    if resolution == 10.0:
        overhead += 0.42
    return ScanTimeEstimate(sweep + overhead, calibrated=True)


def format_duration(seconds: float) -> str:
    """Format a nonnegative duration as MM:SS, rounding up to whole seconds."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("duration must be finite and nonnegative")
    total_seconds = math.ceil(seconds)
    minutes, remainder = divmod(total_seconds, 60)
    return f"{minutes:02d}:{remainder:02d}"
