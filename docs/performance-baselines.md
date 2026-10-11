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

## Actual MainWindow / PlotWidget scan I/O handler characterization (#155)

`tools/benchmark_scan_gui.py` exercises **the real** `MainWindow._open_scan_file`, `MainWindow._add_scan_to_plot` and `PlotWidget.save_scan_data` methods using deterministic `ScanMeasurement` inputs. The first-run code creates an ordinary MainWindow and PlotWidget, but substitutes only the file chooser, export chooser, modal message boxes and delayed hardware initialization. Every fixture uses the normal schema-v2 CSV/MAT export/import logic. No CT400/Vimba/piezo/MATLAB instance is opened or operated; requesting an unexpected confirmation is an error. Data preparation, dialogs and MainWindow construction are **excluded** from the measured handler time; parsing, plot application, serialization and disk writes remain inside the measured calls.

**Capture and compare:**

```powershell
uv run --no-sync python -m tools.performance_baseline capture-gui --qt-platform offscreen --output scan-gui-main.json
uv run --no-sync python -m tools.performance_baseline compare scan-gui-main.json scan-gui-candidate.json --output scan-gui-comparison.json
```

A separate optional Actions job retains `scan-gui-offscreen-<sha>` JSON (30 days). Its 10k/50k × 1/4-detector workload measures **four GUI handler actions per fixture**: `import_csv_foreground`, `import_mat_overlay`, `export_csv`, `export_mat`, for three fresh-process repetitions after a discarded warmup. The report uses the **unchanged v1 envelope**, `benchmark=scan_gui`, the same case keys as `scan_io` and the same comparability checks, but the comparator **rejects cross-benchmark comparisons**. File size is recorded; current/peak process working-set observations (when present) are not incremental per-operation allocations.

The `qt_pending_callback_delay_s` metric queues a zero-delay Qt callback immediately before each synchronous GUI handler and measures when that pending event finally executes. It is a **single pending-callback starvation proxy** which includes a small amount of Qt post-handler dispatch overhead—not an event-loop heartbeat p95, a paint frame rate, or the complete interactive user experience. File dialogs are mocked, so real dialog time is not included. The diagnostic does **not** establish that workers can safely replace these calls; close/cancel, simultaneous scanning, overwrite/replacement policies, partial writes, native desktop event latency and GUI lifecycle ownership must be covered by separate #155 implementation tests before changing production behavior.

Do not infer a physical-lab-PC performance improvement from Github shared-runner timings. Keep the first implementation decision benchmark-led, and perform any native interactive Windows qualification separately with explicit authorization before declaring a UI-latency gain.

## Nonblocking scan-import validation (#155)

When using `capture-gui` on builds with background scan parsing, the benchmark pumps Qt events until each imported scan has been **applied or rejected**, not merely until the import action has queued a worker. The `wall_s` for an import therefore represents end-to-end handler invocation through finished plotting and includes time spent waiting for background parsing. The `qt_pending_callback_delay_s` records the zero-delay Qt callback that may run **while that import is pending**; unlike synchronous builds, its magnitude need not track total import duration. Do not treat this one callback as p95 latency, and distinguish a worker's thread startup overhead from pure schema-v2 parser time.

The application permits only one background import at a time. File selection, replacement confirmation, plot mutations, user-facing errors, settings persistence and pending-close handling remain on the GUI thread. An import result is discarded when a CT400 scan started during the parse, the visible measurement changed, or application close was requested. MainWindow close waits asynchronously for the import thread to stop, rather than trying to forcibly interrupt file parsing. The importer retains the existing 32 MiB file limit. CSV/MAT export is **still synchronous**.

Physical hardware and MATLAB are never part of these measurements. Before concluding that responsiveness improved, compare synthetic builds on equivalent Windows/Qt configurations and run a separate native interactive desktop characterization.

## Background CSV/MAT scan export (#155)

For candidate builds using the background CSV/MAT export worker, `capture-gui` waits for each save to **complete** while processing Qt events. The recorded operation `wall_s` is the end-to-end time (including worker startup, serialization and file publication), whereas `qt_pending_callback_delay_s` may run early because the GUI is no longer blocked by serialization. This is still a single queued-timer probe, not sampled event-loop latency p95 or a native-desktop measurement.

The GUI owns export-file selection, selected-format preferences, overwrite confirmation, immutable acquisition snapshot and MATLAB FIG scheduling. A one-shot worker writes CSV/MAT to temporary files in the destination directory, then uses atomic replacement only for preexisting, individually confirmed targets. Previously absent files are published with an exclusive hard link, so a destination created after the dialog cannot be silently overwritten. The worker reports per-format success or failure; staged temporary files are cleaned up after an exception. A filesystem that cannot create hard links will produce a reported save error rather than risk replacing an unconfirmed target. The application waits for CSV/MAT worker termination before closing but does not force-stop a write. A FIG-only operation retains its existing MATLAB worker path. A successful data file marks only its original measurement as saved; a FIG file alone does not.

These constraints intentionally favor integrity over maximal throughput. Use matched Windows environments and separate native interactive measurements before interpreting responsiveness changes as representative lab-PC gains. Power Monitor export has not been modified by this increment.


## Power Monitor recording CSV/MAT background export (#155)

The completed Power Monitor recording can be exported in the background without interrupting Qt event dispatch. The GUI owns its immutable `PowerMonitorRecording` snapshot, dialog selection, comment, approved destination/overwrite choices, settings changes and final success/error display. It creates the existing schema-v1 `PowerMonitorExportPayload` before launching a one-shot `PowerMonitorExportWorker`; only `np.savetxt(fmt="%.17g")`, compressed MAT serialization, and filesystem publication run off the GUI thread.

Each selected format is staged as `.iopanel-monitor-*.tmp` in its destination directory, flushed to disk and only then published. **Previously absent** targets use an exclusive hard link, preventing silent overwrites if another process creates that destination after the GUI check. **Explicitly confirmed existing** targets are atomically replaced. A filesystem that does not support links reports a save error rather than falling back to an unsafe overwrite. Per-format failure preserves any old target and cleans up staging files; other requested formats may still succeed independently. These are per-file atomic operations, **not** a cross-file transaction.

The Power Monitor trace widget prevents a second save while a worker is pending, Qt-parents the QThread and retains TaskRunner ownership until the QThread `destroyed` event, and defers a direct widget close or MainWindow shutdown until worker cleanup. A new monitor recording does not change the immutable save payload; it stays selected if an earlier save finishes. All user-facing result dialogs remain on the GUI thread. The monitor's close-trace button still **hides** the trace rather than stopping acquisition.

These changes do **not** modify the scan schema-v2 exporter, MATLAB FIG lifecycle or CT400 recording/acquisition controls. Native-lifetime issues remain under investigation in #167, so Windows CI tests include blocked workers, repeated-save guards, native QThread ownership/deferred deletion, shutdown, staged overwrite races and file-failure checks. Matched native desktop Power Monitor responsiveness statistics are still a separate manual characterization; Github Actions latency alone is not physical lab-PC evidence.

## First-responsive MainWindow startup baseline (Issue #152, measurement-only)

This diagnostic measures **the real UI's startup without executing any
instrument initialization**. A parent process timestamps immediately before
requesting `subprocess.run`; every repetition starts a fresh Python child.
The child first imports `app` in production's module-import order (so the
Qt/MainWindow/optional-adapter imports are part of one `app_module_import_s`
interval), constructs QApplication and the real MainWindow, calls
`window.show()`, and records the first queued Qt callback, MainWindow Paint
event **dispatch**, callback queued by that paint event, and the 100-ms
deferred-init callback. The deferred callback is *intercepted* before window
construction: it only records a timestamp and **does not** initialize CT400,
piezo, cameras, VmbSystem or MATLAB Engine. Additional device initialization
entry points raise if unexpectedly invoked. The normal MainWindow close/cleanup
path executes.

### Reproduce (locked Python 3.12 / Windows)

```powershell
uv sync --locked --extra test --extra typecheck --extra lint
uv run --no-sync python -m tools.performance_baseline capture-startup --qt-platform offscreen --repetitions 5 --warmups 0 --output startup-main.json
# After checking out a separately reviewed candidate revision on the same machine/session:
uv run --no-sync python -m tools.performance_baseline capture-startup --qt-platform offscreen --repetitions 5 --warmups 0 --output startup-candidate.json
uv run --no-sync python -m tools.performance_baseline compare startup-main.json startup-candidate.json --output startup-comparison.json
```

The informational Windows
[`performance-baseline.yml`](../.github/workflows/performance-baseline.yml)
job `startup` runs **five** offscreen child subprocesses (no warmups) and
archives `startup-offscreen-<commit>.json` for 30 days. It does not gate CI on
wall-clock performance. Read the report's commit/lock/package/platform data,
raw runs and summaries before comparing two commits. To assess actual native
Windows widget event dispatch in an authorized interactive desktop session,
use `--qt-platform windows` for **both** reports; never treat Qt `offscreen`
and native GUI measurements as interchangeable.

### Timing contract and caveats

- `request_to_child_probe_s` is elapsed from the **parent-side call/request
  timestamp before Popen** to the child's first recorded statement inside
  `characterize`; it includes OS process creation, interpreter bootstrap,
  the benchmark module's imports and its CLI parsing. It is **not** a kernel
  process-created timestamp. Parent/child `perf_counter()` readings are
  comparable on the supported CPython 3.12 Windows runner; the tool checks
  their numeric agreement and event chronology.
- `request_to_app_import_complete_s`, `request_to_mainwindow_constructed_s`,
  `request_to_show_return_s`, `request_to_first_qt_callback_s`,
  `request_to_first_paint_event_s`, `request_to_post_paint_callback_s`,
  `request_to_deferred_init_callback_s` are elapsed since that same parent
  request. These represent pre-user-I/O **Qt-event milestones**, not actual
  input-response measurements.
- `app_module_import_s` measures `import app` after the probe's first
  statement. Its internal dependency import order follows production
  `app.py`; separately labelled `probe_post_app_import_s` measures
  benchmark-only imports after app import. Together with
  `qapplication_create_s`, `qt_configure_s`, `mainwindow_construct_s`,
  `show_call_s` these isolate high-level critical-path stages. Import
  overhead incurred by the probe **before** the first child statement is
  included in parent-request measurements but cannot be attributed to
  individual application imports; use `-X importtime` separately if needed.
- `entry_to_*_s` are from the child's first statement; the
  `event_loop_enter_to_*_s` timings measure queued Qt callbacks after entering
  `app.exec()`. The first MainWindow Paint event is observed **before** its
  normal Qt handler; a callback queued from the event filter does *not*
  establish that every child widget finished painting or that a frame reached
  the display compositor.
- `subprocess_wall_s` is the separate parent's wall time through child
  teardown. Each `request_to_*` milestone must precede it. A 12-second Qt
  watchdog, 45-second parent subprocess timeout, explicit required-metric
  schema and strict chronological validation reject incomplete/corrupt cases.
  No missing timings are silently dropped by metric intersection.
- Optional dependency **versions** for `vmbpy` and `matlabengine` are
  captured in the report environment, and their **actual import availability**
  is recorded as case context. Importing bindings can load SDK libraries, but
  no device enumeration, native CT400/piezo DLL instantiation, Vimba system
  entry, MATLAB session startup or `schedule_matlab_prewarm` is authorized.

### Measurement scope

Every fresh child has a fresh Python module cache but **not necessarily** a
cold OS filesystem/DLL/antivirus/font cache. `--warmups N` runs N additional
fresh processes before recording; it is not a reboot or controlled cold cache
reset. Record machine/session conditions for meaningful cold-vs-warmed claims,
and do not infer stable p95 tails from five samples. Offscreen Qt excludes RDP,
GPU/window-compositor latency. This benchmark constructs the actual
MainWindow with isolated QSettings, an explicit simulated CT400 backend and
zero cameras. It **does not** execute production INI loading, logging setup,
camera discovery, deferred instrument work, MATLAB prewarm, packaged EXE
bootstrap or physically qualify any hardware. No app startup or Python
lazy-import optimization is implemented by this PR.
