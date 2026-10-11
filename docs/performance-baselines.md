# Reproducible IOPanel performance baselines (Issue #151)

## Scope and evidence limits

The new tools/performance_baseline.py script is a **driver-free capture/comparison wrapper** around the existing tools/benchmark_scan_overlays.py. It reuses that benchmark's deterministic synthetic schema-v2 CSV/MAT scan fixtures, 10k/50k/100k samples, 1/4 detectors, 0–8 overlays, PyQtGraph rendering timers, Qt event-loop callback timing, process CPU and Windows working-set measurements. It makes no new device calls and never starts instrument acquisition.

The existing [scan-overlay performance report](scan-overlay-performance.md) explains the workload and its previous measurements. The power monitor benchmark remains available separately in tools/benchmark_power_monitor_display.py. The *operator-supervised physical-camera* script tools/camera_display_characterize.py is intentionally **not** run by this harness. Physical CT400 timing calibration remains under Issue #140. Cold startup, camera controls, conversion/painting, file I/O responsiveness and piezo mock measurements are separate investigations #152–#157: this first tool does **not** claim to profile them yet.

## Hardware-free commands

Use the supported Python 3.12 locked environment, preferably the same Windows machine and session throughout.

    uv sync --locked --extra test --extra lint --extra typecheck

    # Run from a clean, identified main revision:
    uv run --no-sync python -m tools.performance_baseline capture --qt-platform offscreen --output baseline-main.json

    # Run again after switching to the candidate revision, same environment:
    uv run --no-sync python -m tools.performance_baseline capture --qt-platform offscreen --output baseline-candidate.json

    # Compare, without overwriting source reports:
    uv run --no-sync python -m tools.performance_baseline compare baseline-main.json baseline-candidate.json --output comparison.json

The default complete matrix covers 10,000/50,000/100,000 points × 1/4 detectors × 0/1/2/4/8 overlays with mixed nonfinite samples. There are three **fresh subprocess repetitions** and one discarded warmup subprocess. Each subprocess starts a fresh Qt application and creates its own temporary CSV/MAT fixtures. Therefore warmup here does **not** mean maintaining a single continuously running warm GUI. Choose more repetitions when tail latency or system noise matters.

For a quick sanity check, use:

    uv run --no-sync python -m tools.performance_baseline capture --points 10000 --detectors 1 --overlays 0 4 --repetitions 3 --warmups 0 --output quick-baseline.json

The optional --mouse-events flag adds mouse-event bursts. Use **identical** options for both runs. Use --qt-platform windows only in a suitable interactive Windows desktop; the default offscreen Qt plugin is a synthetic operation-level profiler. Remote desktop exposure and compositor conditions may change native results. Never describe its Qt callback, widget grab, or range-change proxies as a monitor refresh-rate measurement or as measured CT400 scan duration.

## JSON schema and interpretation

A baseline report is a versioned JSON object: schema_version = 1, kind = iopanel-performance-baseline. The report preserves:

- Git commit (where available), uv.lock SHA-256 if present, Python/dependency versions, OS/CPU details, requested Qt platform and timestamp.
- Workload parameters, warmup and repetition counts, per-subprocess wall time, and **every raw case result for every repetition**.
- Per-case distributions of available numeric measurements: raw samples, median, nearest-rank p95, minimum and maximum. With just three repetitions, the nearest-rank p95 is the observed maximum. It is **not** a statistically stable long-tail estimate.
- Explicit absence of metrics that cannot be reported. A missing metric is not converted to zero.

The comparator reports each metric's baseline/candidate medians, p95, absolute difference and signed percentage. A negative change means a smaller value, **not universally an improvement**, since throughput and latency have different meanings. When baseline is zero, the percentage is unavailable (JSON null).

Comparison **refuses** changes to measurement matrix, options, schema, metric set or environment. Only the Git commit is permitted to differ automatically. An explicit --allow-environment-mismatch produces a warned **contextual** comparison; it does not establish that the code caused the difference. Raw-run statistics are verified when loading files. Report output uses create-new semantics and will not overwrite an existing path.

The existing profiler's working_set_mb reflects a working-set observation **not peak process memory**. It may be unavailable outside supported Windows environments. Its CPU measurement is process CPU time, not machine-wide usage. Synthetic scan numerical content is deterministic, although the fixtures include metadata timestamps that are not byte-for-byte fixed.

## Repeatability / decision gate

1. Record both revisions, Python and lock/environment identities; run baseline and candidate on the same host and display platform. Alternate run order where practical and do not compare under materially different system load.
2. Keep detector, point, overlay and nonfinite fixtures identical; never discard raw samples or interpret offscreen operation timings as lab evidence.
3. Report median, p95, spread, CPU and available memory measurements. Do not impose hard wall-clock speed thresholds on shared CI runners without characterizing variance.
4. Reuse separate tools for power monitor, real cameras and physical CT400 acquisition. **Do not run physical Vimba, CT400, piezo or MATLAB diagnostics from CI or unattended coding environments.**
5. Only propose an implementation PR when a reproducible bottleneck exists, the expected improvement is worth the complexity, and correctness, lifecycle and hardware safety can be preserved. A measured no-change decision is a valid investigation outcome.

This harness establishes a common *scan-overlay* report protocol; it does not by itself finish all measurement families in Issue #151.
