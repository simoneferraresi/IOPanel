"""Hardware-free Power Monitor display characterization at the nominal 4 Hz cadence."""

from __future__ import annotations

import sys
import time

import numpy as np

from logic.power_monitor_display import DEFAULT_DISPLAY_POINT_LIMIT, PowerMonitorDisplayHistory


def characterize(seconds: int) -> tuple[int, int, float, int]:
    count = seconds * 4
    raw = [[], [], [], []]
    display = PowerMonitorDisplayHistory(len(raw))
    display.bind_raw_values(raw)
    started = time.perf_counter()
    for index in range(count):
        values = tuple(float(np.sin(index * 0.007 + channel)) for channel in range(4))
        for channel, value in enumerate(values):
            raw[channel].append(value)
        display.append(values)
    elapsed = time.perf_counter() - started
    displayed = max(len(display.indices(channel)) for channel in range(4))
    raw_memory = sum(sys.getsizeof(channel) + sum(sys.getsizeof(value) for value in channel) for channel in raw)
    return count, displayed, elapsed, raw_memory


def main() -> None:
    print(f"Display cap: {DEFAULT_DISPLAY_POINT_LIMIT} points per detector; cadence: 4 Hz")
    print("Duration | Raw/detector | Old plotted | New plotted | Reduction | Build/rebuild s | Raw-list MiB")
    for label, seconds in (("5 min", 300), ("1 h", 3600), ("4 h", 14400), ("8 h", 28800)):
        count, displayed, elapsed, memory = characterize(seconds)
        print(
            f"{label:8} | {count:12,} | {count:11,} | {displayed:11,} | "
            f"{count / displayed:8.1f}x | {elapsed:15.3f} | {memory / (1024**2):11.2f}"
        )


if __name__ == "__main__":
    main()
