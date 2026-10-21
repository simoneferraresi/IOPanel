"""Offline CT400 wavelength-sync / PM5020 burst alignment and diagnostics.

No hardware APIs, CT400 DLL, TLPMX, PyVISA, laser control or Qt imports.
An aligned *index* is only a hypothesis until cable/edge/timing semantics
have been measured on the physical CT400 and PM5020.
"""

from __future__ import annotations

import bisect
import math
from itertools import pairwise
from dataclasses import dataclass
from typing import Literal

from hardware.pm5020_types import BurstPowerData


class TriggerSyncError(ValueError):
    """The given sync log and burst cannot be safely paired."""


@dataclass(frozen=True, slots=True)
class SyncPulse:
    """One literal CT400 text-file row. Index base is NOT assumed here."""

    pulse_index: int
    wavelength_nm: float

    def __post_init__(self) -> None:
        if type(self.pulse_index) is not int or self.pulse_index < 0:
            raise TriggerSyncError("CT400 pulse index must be a nonnegative integer")
        if not math.isfinite(self.wavelength_nm) or self.wavelength_nm <= 0:
            raise TriggerSyncError("CT400 wavelength must be positive and finite")


@dataclass(frozen=True, slots=True)
class SynchronizedBurst:
    """Provisional per-pulse mapping; raw PM timestamps remain unsigned ticks."""

    pulse_indices: tuple[int, ...]
    buffer_indices: tuple[int, ...]
    wavelengths_nm: tuple[float, ...]
    timestamps_ticks: tuple[int, ...]
    power_ch1_w: tuple[float, ...]
    power_ch2_w: tuple[float, ...]
    total_pm_samples: int
    skipped_buffer_samples: int
    first_tick_step: int | None
    min_tick_step: int | None
    max_tick_step: int | None
    timing_qualified: bool = False

    @property
    def sample_count(self) -> int:
        return len(self.buffer_indices)


@dataclass(frozen=True, slots=True)
class ProjectedBurst:
    """Linear wavelength-domain projection strictly within synchronized bounds."""

    wavelengths_nm: tuple[float, ...]
    power_ch1_w: tuple[float, ...]
    power_ch2_w: tuple[float, ...]
    timing_qualified: bool = False


def _check_order(indices: tuple[int, ...], wavelengths: tuple[float, ...]) -> int:
    if len(indices) < 2:
        raise TriggerSyncError("At least two CT400 synchronization pulses are needed for wavelength alignment")
    if any(next_index <= index for index, next_index in pairwise(indices)):
        raise TriggerSyncError("CT400 pulse indices must be strictly increasing without duplicates")
    changes = tuple(b - a for a, b in pairwise(wavelengths))
    if all(change > 0 for change in changes):
        return 1
    if all(change < 0 for change in changes):
        return -1
    raise TriggerSyncError("CT400 wavelengths must be strictly monotonic in one scan direction")


def parse_ct400_sync_text(text: str) -> tuple[SyncPulse, ...]:
    """Parse the notebook's two-column `pulse_index wavelength_nm` text format.

    Blank lines and full-line # comments are permitted; malformed rows fail
    instead of being silently dropped. No file-format variant is guessed.
    """
    if not isinstance(text, str):
        raise TypeError("CT400 synchronization text must be a string")
    rows: list[SyncPulse] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) != 2:
            raise TriggerSyncError(f"Invalid CT400 sync row at line {line_number}: expected exactly two fields")
        try:
            index = int(fields[0], 10)
            wavelength = float(fields[1])
            row = SyncPulse(index, wavelength)
        except (TypeError, ValueError) as exc:
            raise TriggerSyncError(f"Invalid CT400 sync row at line {line_number}: {exc}") from exc
        rows.append(row)
    if len(rows) < 2:
        raise TriggerSyncError("CT400 sync file contains fewer than two usable pulse rows")
    _check_order(tuple(row.pulse_index for row in rows), tuple(row.wavelength_nm for row in rows))
    return tuple(rows)


def align_ct400_burst(
    pulses: tuple[SyncPulse, ...],
    burst: BurstPowerData,
    *,
    index_base: Literal[0, 1],
    allow_sparse: bool = False,
) -> SynchronizedBurst:
    """Apply an EXPLICIT candidate index-base assumption, never auto-detect it.

    A selected CT400 pulse index is mapped to `pulse_index - index_base`.
    This relationship was assumed by a lab notebook, not yet physically
    calibrated. Sparse pulse indices are rejected unless explicitly allowed.
    """
    if type(index_base) is not int or index_base not in (0, 1):
        raise TriggerSyncError("index_base must be explicitly set to 0 or 1 after reviewing CT400 sync semantics")
    if not isinstance(pulses, tuple) or any(not isinstance(row, SyncPulse) for row in pulses):
        raise TriggerSyncError("pulses must be a tuple of CT400 SyncPulse records")
    indices = tuple(row.pulse_index for row in pulses)
    wavelengths = tuple(row.wavelength_nm for row in pulses)
    _check_order(indices, wavelengths)
    mapped = tuple(index - index_base for index in indices)
    if mapped[0] < 0 or mapped[-1] >= burst.sample_count:
        raise TriggerSyncError(
            f"CT400 pulse indices with base {index_base} extend beyond the PM5020 sample buffer "
            f"of {burst.sample_count} samples"
        )
    skipped = sum(end - start - 1 for start, end in pairwise(mapped))
    if skipped and not allow_sparse:
        raise TriggerSyncError(
            f"CT400 pulse log skips {skipped} intermediate sample indices; "
            "do not assume a contiguous one-to-one trigger mapping"
        )

    ticks = burst.timestamps_ticks
    steps = tuple(next_tick - tick for tick, next_tick in pairwise(ticks))
    if any(step <= 0 for step in steps):
        raise TriggerSyncError(
            "PM5020 raw timestamp ticks are repeated/decreasing/wrapped; "
            "time scale and uint32 rollover handling are not physically qualified"
        )

    return SynchronizedBurst(
        pulse_indices=indices,
        buffer_indices=mapped,
        wavelengths_nm=wavelengths,
        timestamps_ticks=tuple(ticks[index] for index in mapped),
        power_ch1_w=tuple(burst.power_ch1_w[index] for index in mapped),
        power_ch2_w=tuple(burst.power_ch2_w[index] for index in mapped),
        total_pm_samples=burst.sample_count,
        skipped_buffer_samples=skipped,
        first_tick_step=steps[0] if steps else None,
        min_tick_step=min(steps) if steps else None,
        max_tick_step=max(steps) if steps else None,
    )


def interpolate_within_sync_bounds(
    synced: SynchronizedBurst, target_wavelengths_nm: tuple[float, ...]
) -> ProjectedBurst:
    """Interpolate measured powers without extrapolation or invented time units."""
    if not isinstance(target_wavelengths_nm, tuple) or not target_wavelengths_nm:
        raise TriggerSyncError("Target wavelengths must be a nonempty tuple")
    for value in target_wavelengths_nm:
        if not math.isfinite(value) or value <= 0:
            raise TriggerSyncError("Target wavelengths must be positive and finite")

    index_order = tuple(range(synced.sample_count))
    direction = _check_order(index_order, synced.wavelengths_nm)
    if direction < 0:
        source_x = synced.wavelengths_nm[::-1]
        source_ch1 = synced.power_ch1_w[::-1]
        source_ch2 = synced.power_ch2_w[::-1]
    else:
        source_x = synced.wavelengths_nm
        source_ch1 = synced.power_ch1_w
        source_ch2 = synced.power_ch2_w

    def interpolate_one(x: float, values: tuple[float, ...]) -> float:
        if x < source_x[0] or x > source_x[-1]:
            raise TriggerSyncError(
                f"Requested wavelength {x} nm exceeds actual synchronized domain "
                f"[{source_x[0]}, {source_x[-1]}] nm; extrapolation is forbidden"
            )
        right = bisect.bisect_left(source_x, x)
        if right == 0:
            return values[0]
        if source_x[right] == x:
            return values[right]
        left = right - 1
        fraction = (x - source_x[left]) / (source_x[right] - source_x[left])
        return values[left] + fraction * (values[right] - values[left])

    return ProjectedBurst(
        wavelengths_nm=target_wavelengths_nm,
        power_ch1_w=tuple(interpolate_one(x, source_ch1) for x in target_wavelengths_nm),
        power_ch2_w=tuple(interpolate_one(x, source_ch2) for x in target_wavelengths_nm),
    )
