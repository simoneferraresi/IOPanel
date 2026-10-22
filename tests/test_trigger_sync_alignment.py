"""M4-A offline trigger/wavelength checks: no DLL, VISA or physical instrument."""

from __future__ import annotations

import pytest

from hardware.pm5020_types import BurstPowerData
from logic.trigger_sync_alignment import (
    SyncPulse,
    TriggerSyncError,
    align_ct400_burst,
    interpolate_within_sync_bounds,
    parse_ct400_sync_text,
)


def _burst(n: int = 5) -> BurstPowerData:
    return BurstPowerData(
        timestamps_ticks=tuple(1000 + 100 * index for index in range(n)),
        power_ch1_w=tuple(float(index) for index in range(n)),
        power_ch2_w=tuple(2.0 * index for index in range(n)),
    )


def test_strict_ct400_sync_parser_keeps_explicit_indices_and_direction():
    rows = parse_ct400_sync_text("# pulse wavelength\n\n2 1550.0\n3 1550.5\n4 1551.0\n")
    assert rows == (SyncPulse(2, 1550), SyncPulse(3, 1550.5), SyncPulse(4, 1551))
    assert parse_ct400_sync_text("1 1551\n2 1550\n") == (SyncPulse(1, 1551), SyncPulse(2, 1550))


@pytest.mark.parametrize(
    "source",
    [
        "",
        "0 1550",
        "0 1550\n0 1551",
        "1 1550\n0 1551",
        "0 1550\n1 1550",
        "0 1550\n1 1551\n2 1550.5",
        "0 1550\nbroken row",
        "0 1550 4\n1 1551",
        "0 nan\n1 1551",
        "0 inf\n1 1551",
        "-1 1550\n0 1551",
        "0 0\n1 1551",
        "head index\n0 1550\n1 1551",
    ],
)
def test_reject_ambiguous_or_malformed_ct400_rows(source: str):
    with pytest.raises(TriggerSyncError):
        parse_ct400_sync_text(source)


def test_zero_based_contiguous_alignment_retains_raw_ticks_and_watts():
    pulses = parse_ct400_sync_text("1 1550\n2 1551\n3 1552")
    synced = align_ct400_burst(pulses, _burst(), index_base=0)
    assert synced.buffer_indices == (1, 2, 3)
    assert synced.pulse_indices == (1, 2, 3)
    assert synced.wavelengths_nm == (1550, 1551, 1552)
    assert synced.timestamps_ticks == (1100, 1200, 1300)
    assert synced.power_ch1_w == (1, 2, 3)
    assert synced.power_ch2_w == (2, 4, 6)
    assert synced.total_pm_samples == 5
    assert synced.skipped_buffer_samples == 0
    assert synced.first_tick_step == 100
    assert synced.min_tick_step == synced.max_tick_step == 100
    assert not synced.timing_qualified


def test_explicit_one_based_index_is_not_guessed():
    pulses = parse_ct400_sync_text("1 1540\n2 1541\n3 1542")
    synced = align_ct400_burst(pulses, _burst(), index_base=1)
    assert synced.buffer_indices == (0, 1, 2)
    with pytest.raises(TypeError):
        align_ct400_burst(pulses, _burst())
    with pytest.raises(TriggerSyncError, match="index_base"):
        align_ct400_burst(pulses, _burst(), index_base=True)
    with pytest.raises(TriggerSyncError, match="index_base"):
        align_ct400_burst(pulses, _burst(), index_base=2)


@pytest.mark.parametrize(
    "text,base",
    [
        ("0 1550\n1 1551\n5 1552", 0),
        ("0 1550\n1 1551", 1),
        ("7 1550\n8 1551", 0),
    ],
)
def test_sync_indices_outside_burst_fail_closed(text: str, base: int):
    with pytest.raises(TriggerSyncError, match="beyond"):
        align_ct400_burst(parse_ct400_sync_text(text), _burst(), index_base=base)


def test_missing_pulse_indices_must_be_acknowledged_explicitly():
    pulses = parse_ct400_sync_text("0 1550\n2 1551\n4 1552")
    with pytest.raises(TriggerSyncError, match="skips"):
        align_ct400_burst(pulses, _burst(), index_base=0)
    provisional = align_ct400_burst(pulses, _burst(), index_base=0, allow_sparse=True)
    assert provisional.buffer_indices == (0, 2, 4)
    assert provisional.skipped_buffer_samples == 2
    assert provisional.power_ch2_w == (0, 4, 8)


@pytest.mark.parametrize("timestamps", [(1, 1, 4), (100, 99, 101), (0xFFFFFFFE, 0, 1)])
def test_timestamp_duplicates_reverse_and_rollover_not_interpreted(timestamps: tuple[int, ...]):
    burst = BurstPowerData(timestamps, (1.0, 2.0, 3.0), (2.0, 4.0, 6.0))
    pulses = parse_ct400_sync_text("0 1550\n1 1551\n2 1552")
    with pytest.raises(TriggerSyncError, match="timestamp"):
        align_ct400_burst(pulses, burst, index_base=0)


def test_project_both_channels_without_extrapolation():
    pulses = parse_ct400_sync_text("0 1550\n1 1551\n2 1552")
    aligned = align_ct400_burst(pulses, _burst(3), index_base=0)
    result = interpolate_within_sync_bounds(aligned, (1550, 1550.25, 1551.5, 1552))
    assert result.power_ch1_w == (0, 0.25, 1.5, 2)
    assert result.power_ch2_w == (0, 0.5, 3, 4)
    assert not result.timing_qualified
    with pytest.raises(TriggerSyncError, match="extrapolation"):
        interpolate_within_sync_bounds(aligned, (1550, 1552.00001))
    with pytest.raises(TriggerSyncError, match="extrapolation"):
        interpolate_within_sync_bounds(aligned, (1549.99, 1551))


def test_descending_scan_interpolation_is_independent_of_wavelength_direction():
    pulses = parse_ct400_sync_text("0 1552\n1 1551\n2 1550")
    aligned = align_ct400_burst(pulses, _burst(3), index_base=0)
    result = interpolate_within_sync_bounds(aligned, (1552, 1551.5, 1550))
    assert result.power_ch1_w == (0, 0.5, 2)
    assert result.power_ch2_w == (0, 1, 4)


@pytest.mark.parametrize("points", [(), (float("nan"),), (-1,), (1551, float("inf"))])
def test_invalid_target_wavelengths_are_rejected(points: tuple[float, ...]):
    aligned = align_ct400_burst(parse_ct400_sync_text("0 1550\n1 1552"), _burst(2), index_base=0)
    with pytest.raises(TriggerSyncError):
        interpolate_within_sync_bounds(aligned, points)


def test_no_hardware_objects_needed_for_analysis():
    aligned = align_ct400_burst(parse_ct400_sync_text("0 1550\n1 1551"), _burst(2), index_base=0)
    assert aligned.sample_count == 2
    assert aligned.min_tick_step == 100
    assert aligned.max_tick_step == 100
