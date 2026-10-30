# Startup import boundaries — 2026-10-30

Baseline: `972dcfba9b796a9ab0cf7e2ae73614cdfa3b6322`, branch
`perf/lazy-wavelength-scan-shared-matlab-20261030`. Implementation:
`306db73c4eaf75514ae79ccbcbe6825f41f0a923`, branch
`perf/startup-import-boundaries-20261030`.

Eight inactive panel/dialog imports now follow their existing activation
boundaries. This removes 26 modules from `import app` on this driver-free host,
including 21 first-party modules. It preserves the immediately usable Power
Monitor, public MainWindow properties, tab indices, existing failure/retry
handling and SHG's dependency on the PM5020 panel. Widget constructors and their
signals still run synchronously on the GUI thread. No worker, device, MATLAB
ownership, scan command, OpenGL policy, configuration or dependency version changes.

## Import graph and classification

The investigation combined fresh-interpreter `-X importtime`, `sys.modules`,
and AST inspection of the 75 startup-loaded first-party modules, including all
21 startup-loaded UI modules. Optional SDK branches were inspected in source;
neither vendor SDK is installed in the measurement environment. Importing
`ui.main_window` directly reaches the same eager UI graph as importing `app`.
The candidate loads 13 UI modules before panel activation.

| Required phase | Modules / transitive imports | Decision |
| --- | --- | --- |
| Before configuration validation | stdlib CLI, paths, logging, INI parsing; `config_model`, Pydantic; Qt application/message-box support for configuration errors | Retain entry point and its public helpers. No hardware access. UI imports still precede validation in `app.py`; this pass removes unnecessary transitive imports rather than moving the whole entry point. |
| First functional window | `ui.main_window`, `ui.control_panel` (active HistogramControlPanel), `ui.plot_widgets` (active HistogramWidget), PyQtGraph, NumPy, `ui.constants`, `ui.global_status`, `ui.log_console`, `ui.typography`, `ui.application_icons`, compiled resources; settings, shared scan settings, device lease and MATLAB manager definitions | Retain. The histogram is constructed immediately, so PyQtGraph remains on the first-window critical path even though PlotWidget is lazy. |
| Deferred hardware initialization | `hardware.ct400_init_worker`, CT400/DummyCT400/types/interfaces; camera/init/recovery/Vimba-system workers, `hardware.simulated_camera`, `ui.camera_widgets`, OpenCV; piezo controller/init/serial metadata helpers; `logic.task_runner`, worker retirement | Retain imports for this small patch. Initialization itself remains deferred. CameraPanel creation still happens in the GUI result handler, VmbSystem initialization retains its existing worker/owner, and enabled cameras retain concurrent startup. Piezo discovery still requires configured ports. |
| Inactive Auto Alignment | `ui.alignment_panel` → `hardware.alignment_worker`; `ui.plot_3d_widget` → PyQtGraph OpenGL only on existing viewer activation | Import panel only when tab/property requested; keep existing lazy 3D viewer. |
| Inactive Thorlabs PM5020 | `ui.pm5020_panel` → `logic.pm5020_monitor`, `logic.pm5020_trace_export`, physical adapter, simulator | Import only on tab/property/menu access. Preserve explicit physical/simulation selection and connection. TLPMX wrapper remains lazy within the existing adapter. |
| Inactive IPG EDFA | `ui.edfa_panel` → `logic.edfa_monitor`, simulator, IPG/GPIB adapters | Import only on tab/property/menu access. PyVISA and instrument opening retain their existing boundaries. |
| Inactive SHG simulation | `ui.shg_synchronized_panel` → synchronized acquisition/archive/alignment utilities | Import on access, after its existing PM5020 panel dependency. No new automatic acquisition. |
| Appearance and calibration dialogs | `ui.appearance_dialog`, `ui.ct400_calibration_dialog`, `ui.ct400_calibration_campaign` → calibration planning/utilities | Import at their existing menu handlers. Supervised campaign still reuses an existing modeless session; planning remains passive. |
| Camera discovery dialog | `ui.discovery_dialog` → existing camera adapter | Import inside the existing guarded error-handling block, after the existing VmbSystem-ready check. Import alone never starts discovery. |
| Specialized plotting/export utilities still loaded | `ui.alignment_colorbar` compatibility export; `ui.export_dialog_base`, `ui.power_monitor_export_dialog`, `ui.scan_export_dialog`; scan/monitor import/export workers and payload helpers through `ui.plot_widgets` | Retain to avoid expanding the public plotting API refactor. SciPy MAT IO, MATLAB Engine, TLPMX and OpenGL remain deferred by existing code. |

Registration dispatch uses the same tab index as construction. Checking every
possible panel class with `isinstance` would require importing inactive siblings.
Postponed annotations and string-based casts keep type checking without runtime
imports. Canonical panel/dialog module APIs and the existing `app.MainWindow`
export remain intact; incidental panel class imports in `ui.main_window` are no
longer eager module globals.

## Measurements

Measured locally on Windows, CPython **3.12.8 x64**, uv **0.13.4**, locked
PySide6/Qt **6.12.0**, PyQtGraph **0.14.0**, NumPy **2.5.3**. Dependencies live
in `output/startup-imports/dev-env`; operational `.venv` and configuration were
not modified. No physical devices or vendor SDKs were used.

Each case uses seven fresh child interpreters after one discarded warmup;
these are warm-filesystem measurements, not cold OS boots. Before/after use
the same interpreter and instrumentation, run serially. An initial overlapping
measurement was discarded in favor of the serial capture. Values are medians;
positive savings mean before minus after. Raw numeric samples and loaded UI
module lists are [versioned alongside this note](performance/startup-import-boundaries-2026-10-30.json).

| Measured metric | Before (s) | After (s) | Saving (ms) |
| --- | ---: | ---: | ---: |
| `app` cumulative import (`-X importtime`) | 1.853345 | 1.734612 | 118.7 |
| `ui.main_window` cumulative import, nested within `app` | 1.116906 | 1.047718 | 69.2 |
| `ui.plot_widgets` cumulative import | 0.590369 | 0.604518 | -14.1 |
| `pyqtgraph` cumulative import | 0.545607 | 0.561303 | -15.7 |
| Unprofiled `app` import, hardware-suppressed startup probe | 1.645171 | 1.597437 | 47.7 |
| MainWindow construction, same probe | 0.762437 | 0.766469 | -4.0 |
| Process request → first Qt paint dispatch, same probe | 2.808097 | 2.802611 | 5.5 |
| Process request → queued callback after paint, same probe | 2.835872 | 2.834481 | 1.4 |
| Process request → suppressed deferred-init callback | 2.893213 | 2.892879 | 0.3 |
| Simulation smoke: entry → `app` imported | 1.717261 | 1.650447 | 66.8 |
| Simulation smoke: entry → first MainWindow paint dispatch | 2.672100 | 2.622318 | 49.8 |
| Simulation smoke: entry → camera initialization readiness marker | 2.907444 | 2.798756 | 108.7 |
| Simulation smoke: entry → displayed non-null camera pixmap saved | 2.909622 | 2.811187 | 98.4 |
| Simulation smoke: entry → shutdown complete | 2.935973 | 2.865602 | 70.4 |

**Do not sum nested import timings.** Reordering imports also changes which
parent receives cumulative cost. `-X importtime` adds instrumentation overhead;
its 119 ms import difference is not a 119 ms first-paint saving. The ordinary
startup probe's paint ranges overlap substantially (before 2.753–3.269 s;
after 2.749–3.048 s). It does not establish a repeatable critical-path paint gain.
The simulation smoke's roughly 50 ms paint and 98 ms frame improvements are
observations from these samples, not guaranteed effects on physical equipment.
Camera readiness is sampled by the existing smoke timer and is not an exact
sensor-stream-start timestamp. Qt paint dispatch and a queued callback are not
display-compositor presentation or a measured operator interaction.

The supplied laboratory results (app 6.84 s, MainWindow import 4.83 s,
plot_widgets 2.42 s, PyQtGraph 2.23 s, paint 7.11 s, construction 135 ms,
MATLAB ready 48.47 s, both cameras approximately 15.51 s) are historical
measurements at the baseline SHA, not reproduced here. There is **no measured
or estimated laboratory after value**. MATLAB readiness and physical camera
streaming were not measured. CT400 fallback/ownership logic was not changed.

### Reproduction

Local evidence retains `measure_imports.py`, `measure_simulation.py`, raw
`importtime-*.txt`, startup reports and the AST graph under
`output/startup-imports/` (ignored; never upload native dumps). The import
harness launches `python -X importtime -c "import app"` in fresh processes and
records wall time, self/cumulative times and `sys.modules`. The simulation
harness calls real `app.main()` with `packaging/config.simulation.ini`,
`--smoke-test`, temporary INI settings and logs; it timestamps the MainWindow
Paint event and the existing smoke/shutdown logging markers without replacing
initialization workers. It closes log handlers before deleting temporary logs.

The existing startup instrumentation is unchanged:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD/output/startup-imports/dev-env"
$env:UV_CACHE_DIR = "$PWD/output/uv-cache"
uv sync --locked --python 3.12 --no-default-groups --extra test --extra lint --extra typecheck
uv run --no-sync python -m tools.performance_baseline capture-startup --output output/startup-imports/<stage>-startup.json --repetitions 7 --warmups 1 --qt-platform windows
```

## Functional validation

The new cold-window import test was run before the production change and
failed (exit 1), listing all twelve prohibited inactive/supporting modules.
Afterward, it passes through real histogram construction, paint and accepted
asynchronous close. Four further cold-process cases verify selective imports,
first/repeated property access, unchanged shared MATLAB manager identity, and
absence of eager Engine/OpenGL imports. Real pytest-qt tests inject import
failures in each inactive panel, verify placeholder/guard recovery and
successful subsequent reuse, and forbid panel imports after shutdown begins.
Existing appearance/calibration/discovery, configuration, camera, scan,
acquisition, cancellation and MATLAB lifetime tests remain in the full suite.

Focused runs: 144 passed / 105 warnings (exit 0), then 34 passed / 72 warnings
(exit 0) after adding cold per-panel cases. Warnings are the existing PyQtGraph
NumPy 2.5 array-shape deprecations. Ruff check, Ruff format (314 files), isolated
Pyright (0 errors/warnings) and `git diff --check` passed.

Pyright's repository configuration names `.venv`, so `--pythonpath` alone still
resolved the lab packages and reported an optional VmbSystem typing error also
present in baseline source. The successful check uses a temporary config at
`output/startup-imports/pyrightconfig.json`, extending `../../pyproject.toml`
with `venvPath: "."`, `venv: "dev-env"`, and `extraPaths: ["../.."]`. It retains
the repository's include list and all diagnostics.

The first campaign attempt under the sandbox failed before executing a test:
CIM process identity inspection was denied, and pytest could not use the host's
default temporary directory. Its supervisor exited 1 and failed to write a
manifest; it is not counted as passing. That evidence remains in
`output/startup-imports/reliability`. Native qualification uses a new evidence
directory outside the sandbox with TEMP/TMP redirected into the workspace.

Exact collection parity passed: **1,723 items**, partitioned core 1,164,
camera 223 and plot 336, with no missing or overlapping node IDs. The native
campaign completed, overall exit **0**, with every child and supervisor exit
**0**, no timeouts and no native failures after pytest summaries:

| Native process | Passed | Skipped | Warnings | Pytest seconds | Child / supervisor exits |
| --- | ---: | ---: | ---: | ---: | --- |
| Stress: ten simulation startup/shutdown cycles | 1 | 0 | 1 | 7.67 | 0 / 0 |
| Core 1 | 1,163 | 1 | 225 | 182.24 | 0 / 0 |
| Camera 1 | 223 | 0 | 24 | 43.29 | 0 / 0 |
| Plot 1 | 336 | 0 | 14 | 42.28 | 0 / 0 |
| Core 2 | 1,163 | 1 | 225 | 165.73 | 0 / 0 |
| Camera 2 | 223 | 0 | 24 | 42.69 | 0 / 0 |
| Plot 2 | 336 | 0 | 14 | 40.60 | 0 / 0 |

Each full-suite round therefore passed 1,722 tests with one existing opt-in
stress skip; that skipped test ran separately through the ten-cycle supervisor.
Warnings comprise existing PyQtGraph/NumPy deprecations and one per-process
pytest-cache warning because this host's existing `.pytest_cache` is not
writable from the native run. Tests and native ownership cleanup still passed;
no warning was suppressed. Evidence: `output/startup-imports/reliability-native`.

Frozen simulation smoke passed (exit 0): simulated CT400 and camera startup,
displayed frame, resources, isolated settings/logging, accepted shutdown,
different working directory and rejection of physical smoke configuration.
Frozen OpenGL rendered successfully with both automatic selection (QT_OPENGL
unset) and a separate explicit `QT_OPENGL=desktop` case, each exit 0. Both
verified a saved framebuffer, map/axes/labels/colorbar updates, viewer reuse
and rejection of physical smoke configuration. The context was Intel HD
Graphics 4600, OpenGL 4.3 compatibility profile. Frozen `--help` and `--version`
also exited 0. The qualification wrapper exited 0. Logs, image evidence and
per-case exits remain under `output/startup-imports/frozen-qualification`.

Commands for native and frozen qualification (workspace paths only):

```powershell
$env:TEMP = "$PWD/output/startup-imports/campaign-temp"
$env:TMP = $env:TEMP
uv run --no-sync python -m tools.qt_reliability_campaign --output output/startup-imports/reliability-native --core-runs 2 --camera-runs 2 --plot-runs 2 --stress-cycles 10
uv run --no-sync python -m PyInstaller --noconfirm --distpath output/startup-imports/frozen/dist --workpath output/startup-imports/frozen/build packaging/IOPanel.spec
Copy-Item packaging/config.simulation.ini output/startup-imports/frozen/dist/IOPanel/config.ini
uv run --no-sync python -m tools.smoke_frozen output/startup-imports/frozen/dist/IOPanel/IOPanel.exe --timeout 45
uv run --no-sync python -m tools.smoke_frozen_opengl output/startup-imports/frozen/dist/IOPanel/IOPanel.exe --platform windows --timeout 30 --evidence-dir output/startup-imports/frozen-qualification/opengl-auto
# Run a separate, explicitly labelled case with QT_OPENGL=desktop.
```

Packaging tools were added with another locked, no-default-groups sync including
`--extra dev`; runtime package versions did not change. The initial PowerShell
build wrapper reported exit 1 despite a complete build log. A separate bounded
subprocess invocation records the actual PyInstaller return code as 0 in
`frozen-build-exit.json`; the earlier wrapper result is not counted as success.
Build warnings include optional Jupyter/tzdata/SciPy hidden modules, macOS
serial framework names and legacy OpenGL GLUT MSVCR90 dependencies. All eight
deferred modules are present in PyInstaller's Analysis TOC.

## Remaining costs and follow-up

PyQtGraph remains mandatory for the active histogram. NumPy, Qt widgets,
OpenCV/camera panel imports, font installation and native window construction
also remain. Shared control/plot modules still import scan/export definitions
that the initial monitor does not use. The patch moves cost to first access;
the first panel/dialog opening may therefore take longer while later access
uses Python's module cache and the existing widget cache.

Recommended next step: repeat the existing instrumentation on the lab PC,
separating process/import cost, font/configuration cost, histogram construction
and native painting. Investigate PyQtGraph's measured import graph and the
still-eager scan/export helpers before attempting a histogram replacement or
a shared plotting API split. Separately moving UI imports behind successful
configuration validation would benefit CLI/error paths, but would not by
itself remove cost before a functional window. Physical VmbPy, MATLAB Engine,
CT400 DLL and lab-PC/frozen-driver behavior require explicit operator-authorized
qualification; none was exercised here. No automatic merge or lab deployment.
