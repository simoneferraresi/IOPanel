"""Typed, unit-explicit values shared by the PM5020 implementations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum


class PowerChannel(IntEnum):
    CH1 = 1
    CH2 = 2


@dataclass(frozen=True, slots=True)
class ChannelConfig:
    """Power measurement settings. Power ranges use watts; wavelength uses nm."""

    wavelength_nm: float | None = None
    auto_range: bool = True
    power_range_w: float | None = None
    frequency_mode: int = 0
    input_filter_enabled: bool = False

    def __post_init__(self) -> None:
        if self.wavelength_nm is not None and (not math.isfinite(self.wavelength_nm) or self.wavelength_nm <= 0):
            raise ValueError("wavelength_nm must be a positive finite value")
        if self.power_range_w is not None and (not math.isfinite(self.power_range_w) or self.power_range_w <= 0):
            raise ValueError("power_range_w must be a positive finite value")
        if not self.auto_range and self.power_range_w is None:
            raise ValueError("power_range_w is required when auto_range is disabled")
        if self.frequency_mode != 0:
            raise ValueError("Only the notebook-verified CW frequency mode (0) is supported")


@dataclass(frozen=True, slots=True)
class BurstAcquisitionConfig:
    """External-triggered burst settings verified by the PM5020 reference notebook."""

    channels: tuple[PowerChannel, ...] = (PowerChannel.CH1, PowerChannel.CH2)
    trigger_source: int = 3
    initial_delay_counts: int = 0
    samples_per_trigger: int = 1
    averaging_count: int = 1
    max_samples: int = 15_000
    chunk_size: int = 100

    def __post_init__(self) -> None:
        channels = tuple(PowerChannel(channel) for channel in self.channels)
        if not channels or len(set(channels)) != len(channels):
            raise ValueError("channels must contain one or more distinct PM5020 channels")
        object.__setattr__(self, "channels", channels)
        if not 0 <= self.trigger_source <= 0xFFFFFFFF:
            raise ValueError("trigger_source must fit an unsigned 32-bit value")
        if not 0 <= self.initial_delay_counts <= 0xFFFFFFFF:
            raise ValueError("initial_delay_counts must fit an unsigned 32-bit value")
        if not 1 <= self.samples_per_trigger <= 0xFFFFFFFF:
            raise ValueError("samples_per_trigger must be a positive unsigned 32-bit value")
        if not 1 <= self.averaging_count <= 0xFFFFFFFF:
            raise ValueError("averaging_count must be a positive unsigned 32-bit value")
        if not 1 <= self.max_samples <= 15_000:
            raise ValueError("max_samples must be between 1 and the notebook capacity of 15000")
        if not 1 <= self.chunk_size <= self.max_samples:
            raise ValueError("chunk_size must be between 1 and max_samples")


@dataclass(frozen=True, slots=True)
class PowerReading:
    channel: PowerChannel
    power_w: float
    acquired_at_utc: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", PowerChannel(self.channel))
        if not math.isfinite(self.power_w):
            raise ValueError("power_w must be finite")
        if self.acquired_at_utc.tzinfo is None:
            raise ValueError("acquired_at_utc must be timezone-aware")

    @classmethod
    def now(cls, channel: PowerChannel, power_w: float) -> PowerReading:
        return cls(channel, float(power_w), datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class BurstPowerData:
    """Raw PM5020 burst data. Timestamps are unsigned instrument ticks.

    The notebook exposes timestamps as ``c_uint32`` but does not identify their
    scale or epoch, so this model deliberately does not convert them to seconds.
    """

    timestamps_ticks: tuple[int, ...]
    power_ch1_w: tuple[float, ...]
    power_ch2_w: tuple[float, ...]

    def __post_init__(self) -> None:
        times = tuple(int(value) for value in self.timestamps_ticks)
        ch1 = tuple(float(value) for value in self.power_ch1_w)
        ch2 = tuple(float(value) for value in self.power_ch2_w)
        if not times:
            raise ValueError("burst data must contain at least one sample")
        if len(times) != len(ch1) or len(times) != len(ch2):
            raise ValueError("timestamp and channel sample counts must match")
        if any(value < 0 or value > 0xFFFFFFFF for value in times):
            raise ValueError("timestamps must fit unsigned 32-bit values")
        if any(not math.isfinite(value) for value in (*ch1, *ch2)):
            raise ValueError("burst powers must be finite values in watts")
        object.__setattr__(self, "timestamps_ticks", times)
        object.__setattr__(self, "power_ch1_w", ch1)
        object.__setattr__(self, "power_ch2_w", ch2)

    @property
    def sample_count(self) -> int:
        return len(self.timestamps_ticks)
