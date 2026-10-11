# Reproducible IOPanel performance baselines (Issue #151)

## Scope and evidence limits

The tools/performance_baseline.py script is a **driver-free capture/comparison wrapper** around the existing tools/benchmark_scan_overlays.py, with a separate synthetic scan schema-v2 file I/O workload. It reuses that benchmark's deterministic synthetic schema-v2 CSV/MAT scan fixtures, 10k/50k/100k samples, 1/4 detectors, 0–8 overlays, PyQtGraph rendering timers, Qt event-loop callback timing, process CPU and Windows working-set measurements. It makes no new device calls and never starts instrument acquisition.

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

## Archived Windows Actions baseline (separate workflow)

The optional [Synthetic Windows performance baseline](../.github/workflows/performance-baseline.yml) GitHub Actions workflow runs the same hardware-free `capture` CLI in a locked **Python 3.12 / Windows runner / Qt offscreen** environment. It uploads the actual versioned JSON as a 30-day Actions artifact rather than relying solely on a transient test fixture. The default workflow matrix is 10k/50k points × 1/4 detectors × 0/4/8 overlays, with one discarded startup subprocess and three recorded repetitions. For 100k-point and native interactive desktop characterization, use the manual commands above.

The workflow runs on pull requests that change its own definition or the benchmark scripts, **once when the workflow file is merged to `main`**, and through the Actions **Run workflow** control afterward. It is informational only: neither a required status check nor a regression threshold. A failed benchmark is still reported as a failed diagnostic workflow, but it does not establish a production application regression without investigation. CI does not initialize a Vimba camera, CT400, piezo or real MATLAB Engine. Download the run's `scan-overlays-offscreen-<commit>` artifact for the JSON, and record the run URL with any analysis.

**Evidence limit:** GitHub-hosted runner load, operating-system images and CPU hardware can change between runs. Versioned JSON environmental checks reduce accidental apples-to-oranges comparisons but do not make shared-runner time measurements causally attributable to source changes. Prefer comparable native Windows sessions for confident performance claims; never equate offscreen times to real camera FPS or physical acquisition latency.

## Scan CSV/MAT I/O and Qt pending-callback benchmark (Issue #151, next increment)

The driver-free `tools/benchmark_scan_io.py` creates deterministic synthetic schema-v2 `ScanMeasurement` snapshots and measures five **separate** operations: `build_payload`, `csv_export`, `mat_export`, `csv_import`, and `mat_import`. The export operations reproduce the application's **current** synchronous CSV `StringIO` / `np.savetxt(fmt="%.6f")` and compressed `savemat` serialization; imports use the **real** strict `logic.scan_import.load_scan`. Imports verify detector identities, dimensions and round-trip numerical data. Each child process prepares independent temporary files and does not persist scan content or initialize devices.

```powershell
uv run --no-sync python -m tools.performance_baseline capture-io --qt-platform offscreen --output scan-io-main.json
uv run --no-sync python -m tools.performance_baseline capture-io --qt-platform offscreen --output scan-io-candidate.json
uv run --no-sync python -m tools.performance_baseline compare scan-io-main.json scan-io-candidate.json --output scan-io-comparison.json
```

By default this measures 10k/50k/100k points × one/four active optical detectors, with one warmup child and three recorded **fresh subprocess** repetitions. A representative small run is `capture-io --points 10000 --detectors 1 --repetitions 2 --warmups 0 --output quick-io.json`. These are **not** safe file-saving performance predictions for an actual experiment: the benchmark uses local temporary disk and clean, synthetic data. The 32 MiB schema-v2 importer limit still applies.

Each measured synchronous operation queues a **zero-delay Qt timer callback on the same QApplication thread** just before running the operation, then processes the pending callback after the operation completes. `qt_pending_callback_delay_s` measures elapsed time until that pending callback executes; `wall_s` measures the operation's own duration. This is a *single-callback starvation proxy*: it is intentionally correlated with `wall_s`, does **not** produce a statistically representative event-loop heartbeat distribution, does not exercise `MainWindow` menus/dialogs/close paths, and does not show that moving the operation to a worker is safe. A real GUI responsiveness characterization and close/cancellation tests remain for #155.

The report shares the **same v1 JSON envelope** as the scan-overlay benchmark, with a different, explicitly labeled `benchmark: scan_io` and I/O-specific case identity (`points_per_scan`, `detector_count`, `operation`, `qt_platform`, `window_system`). The existing archived overlay JSON files remain loadable/comparable. Do not compare overlay and I/O workload reports against one another. CSV and MAT may have different numeric precision; only schema-correct round trips are compared. The I/O report preserves CPU seconds, wall seconds, the pending callback proxy, and where available **current and peak Windows working-set observations**; peak working set is a process-lifetime high-water mark, not incremental allocation by each operation.

The existing optional Actions baseline workflow now archives **separate** scan-overlay and scan-I/O JSON artifacts. Its I/O run is synthetic, Qt offscreen and **informational**, not a latency regression gate. As with all Actions benchmarks, the host's resource sharing can make direct cross-run speed comparisons misleading.
