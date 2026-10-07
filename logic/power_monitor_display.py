"""Bounded display-only summaries for Power Monitor traces.

Raw acquisition arrays remain the source of truth. This helper tracks extrema
indices in progressively wider contiguous buckets, rebuilding only when the
bucket width must double (amortized, infrequent work).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_DISPLAY_POINT_LIMIT = 4000
_RECENT_FULL_RESOLUTION_POINTS = 256


@dataclass
class _Bucket:
    start: int
    stop: int
    indices: list[list[int]]
    minima: list[int | None]
    maxima: list[int | None]
    nonfinite: list[int | None]


class PowerMonitorDisplayHistory:
    """Incremental min/max envelope indices, independently preserved per detector."""

    def __init__(self, detector_count: int, point_limit: int = DEFAULT_DISPLAY_POINT_LIMIT):
        if detector_count < 0 or point_limit < 5:
            raise ValueError("detector_count must be non-negative and point_limit at least five")
        self.detector_count = detector_count
        self.point_limit = point_limit
        self._recent_points = min(_RECENT_FULL_RESOLUTION_POINTS, max(0, point_limit - 5))
        self._bucket_limit = max(1, (point_limit - self._recent_points) // 5)
        self._bucket_width = 1
        self._count = 0
        self._buckets: list[_Bucket] = []
        self._values: list[list[float]] = [[] for _ in range(detector_count)]

    def bind_raw_values(self, values: list[list[float]]) -> None:
        """Use the authoritative channel lists without copying their samples."""
        if len(values) != self.detector_count:
            raise ValueError("raw channel count does not match display history")
        self._values = values

    def clear(self) -> None:
        self._bucket_width = 1
        self._count = 0
        self._buckets.clear()

    def rebuild_from_raw(self) -> None:
        """Initialize display indices from an existing complete raw snapshot."""
        lengths = {len(channel) for channel in self._values}
        if len(lengths) > 1:
            raise ValueError("all raw detector channels must have matching sample counts")
        self._count = next(iter(lengths), 0)
        self._bucket_width = 1
        while (self._count + self._bucket_width - 1) // self._bucket_width > self._bucket_limit:
            self._bucket_width *= 2
        self._rebuild()

    def append(self, values: tuple[float, ...] | list[float]) -> None:
        if len(values) != self.detector_count:
            raise ValueError("one value is required for every active detector")
        index = self._count
        if any(len(channel_values) != index + 1 for channel_values in self._values):
            raise ValueError("append the sample to authoritative raw lists before updating display history")
        self._count += 1
        if index // self._bucket_width >= self._bucket_limit:
            while (self._count + self._bucket_width - 1) // self._bucket_width > self._bucket_limit:
                self._bucket_width *= 2
            self._rebuild()
            return
        bucket_id = index // self._bucket_width
        if bucket_id == len(self._buckets):
            indices: list[list[int]] = []
            minima: list[int | None] = []
            maxima: list[int | None] = []
            nonfinite: list[int | None] = []
            for value in values:
                if np.isfinite(value):
                    minima.append(index)
                    maxima.append(index)
                    nonfinite.append(None)
                else:
                    minima.append(None)
                    maxima.append(None)
                    nonfinite.append(index)
                indices.append([index])
            self._buckets.append(_Bucket(index, index + 1, indices, minima, maxima, nonfinite))
            return
        bucket = self._buckets[bucket_id]
        bucket.stop = index + 1
        for channel, value in enumerate(values):
            if np.isfinite(value):
                low_index = bucket.minima[channel]
                high_index = bucket.maxima[channel]
                if low_index is None or value < self._value_at(channel, low_index):
                    bucket.minima[channel] = index
                if high_index is None or value > self._value_at(channel, high_index):
                    bucket.maxima[channel] = index
            elif bucket.nonfinite[channel] is None:
                bucket.nonfinite[channel] = index
            bucket.indices[channel] = sorted(
                {
                    bucket.start,
                    bucket.stop - 1,
                    *(
                        candidate
                        for candidate in (bucket.minima[channel], bucket.maxima[channel], bucket.nonfinite[channel])
                        if candidate is not None
                    ),
                }
            )

    def _value_at(self, channel: int, index: int) -> float:
        # Read extrema comparisons directly from the authoritative raw channel.
        return self._values[channel][index]

    def _rebuild(self) -> None:
        self._buckets = []
        for start in range(0, self._count, self._bucket_width):
            stop = min(self._count, start + self._bucket_width)
            channels: list[list[int]] = []
            minima: list[int | None] = []
            maxima: list[int | None] = []
            nonfinite: list[int | None] = []
            for channel in range(self.detector_count):
                data = np.asarray(self._values[channel][start:stop], dtype=float)
                finite_indices = np.flatnonzero(np.isfinite(data))
                if finite_indices.size:
                    finite_data = data[finite_indices]
                    minimum = start + int(finite_indices[int(np.argmin(finite_data))])
                    maximum = start + int(finite_indices[int(np.argmax(finite_data))])
                else:
                    minimum = maximum = None
                nonfinite_mask = np.flatnonzero(~np.isfinite(data))
                gap_index = start + int(nonfinite_mask[0]) if nonfinite_mask.size else None
                minima.append(minimum)
                maxima.append(maximum)
                nonfinite.append(gap_index)
                channels.append(
                    sorted(
                        {
                            start,
                            stop - 1,
                            *(candidate for candidate in (minimum, maximum, gap_index) if candidate is not None),
                        }
                    )
                )
            self._buckets.append(_Bucket(start, stop, channels, minima, maxima, nonfinite))

    def indices(self, channel: int) -> np.ndarray:
        if channel < 0 or channel >= self.detector_count:
            raise IndexError(channel)
        if self._count <= self.point_limit:
            return np.arange(self._count, dtype=np.int64)
        recent_start = max(0, self._count - self._recent_points)
        retained = {index for bucket in self._buckets for index in bucket.indices[channel] if index < recent_start}
        retained.update(range(recent_start, self._count))
        return np.fromiter(sorted(retained), dtype=np.int64)
