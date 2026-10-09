"""Pure helpers for nominal wavelength sweep time estimates."""

import math


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


def format_duration(seconds: float) -> str:
    """Format a nonnegative duration as MM:SS, rounding up to whole seconds."""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("duration must be finite and nonnegative")
    total_seconds = math.ceil(seconds)
    minutes, remainder = divmod(total_seconds, 60)
    return f"{minutes:02d}:{remainder:02d}"
