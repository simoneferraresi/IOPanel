# Power Monitor recordings

Power Monitor recordings retain every acquired sample, timestamp, Pout value,
and selected detector value in the recording's full-resolution arrays. CSV and
MAT exports use those arrays and remain full-resolution.

Only the on-screen completed/live recording trace is summarized. Until the
trace reaches 4,000 points per detector, it is plotted unchanged. Longer traces
use a contiguous-bucket min/max envelope: each detector independently retains
bucket boundaries, finite minima and maxima, and one marker for any NaN or
infinite sample. This preserves extrema without losing a representative gap
that should break the rendered line. A recent 256-sample window remains at full
resolution. Bucket widths grow geometrically, so ordinary
sample updates touch only the current bucket; a full display-index rebuild
happens only when the bucket width needs to double. This preserves brief peaks
and dips while keeping PyQtGraph input bounded and recent motion smooth. As with
any bounded visualization, older intervals are represented by their extrema
rather than every acquisition point.

If the trace is hidden, acquisition and recording continue while curve updates
are skipped. Showing it again renders from the current raw trace state. The
separate live current/max histogram remains unchanged.

Run `uv run --no-sync python -m tools.benchmark_power_monitor_display` for a
hardware-free synthetic 4 Hz characterization at 5 minutes, 1 hour, 4 hours,
and 8 hours. It reports raw and plotted point counts, reduction, construction
and rebuild duration, and approximate raw Python-list memory.
