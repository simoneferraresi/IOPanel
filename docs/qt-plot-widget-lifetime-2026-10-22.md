# Python window ownership during native plot painting

## Status and scope

Investigation of draft [PR #232](https://github.com/simoneferraresi/IOPanel/pull/232)
and reopened [issue #216](https://github.com/simoneferraresi/IOPanel/issues/216).
The correction is a separate branch from stable main because the demonstrated
ownership defect exists under both Qt versions. No production plotting or
worker code, dependency policy, laboratory configuration, or operational
environment is changed. No hardware or real MATLAB Engine was started.

Qualification results are recorded separately below and in the accompanying
sanitized process ledger. Incomplete or failed runs never qualify a campaign.
The upgrade remains draft pending its independent acceptance requirements.

## Initial state

Live GitHub inspection confirmed:

| Reference | SHA / state |
| --- | --- |
| Stable main | `37cabddc9c5a88cd2d70352379446349693b642a` |
| PR #232 | `bf30141b2fe99336e946d339d5595c13ffdc1a40`, draft, base main above |
| PR #232 changed files | Only `pyproject.toml` and `uv.lock` |
| Issue #216 | Open, reopened; eight comments at initial inspection |
| Existing laboratory checkout | Existing configuration edits and prior worktrees; left untouched |

Active ruleset `25659130`, "Protect main — CI required", requires a PR,
resolved review threads, an up-to-date base, `checks-and-collection`, all three
`pytest / core`, `pytest / camera`, `pytest / plot` checks and `windows-x64`.
Deletion and non-fast-forward updates are prohibited; there are no bypass actors.
The legacy branch-protection endpoint returns 404; the active ruleset is the
server-side protection.

Read AGENTS.md, both Qt testing standards, the October lifecycle report and its
process ledger, conftest, the native test-lifecycle helper, partition verifier,
supervisor, reliability campaign and worker-retirement implementation before
changing source. Reviewed PR #236 and the issue discussion. Prior connection
mutex/GIL inversion, finalization and tracemalloc corrections remain invariants;
they do not establish the cause of this separate paint failure.

## Original CI evidence

Downloaded and inspected both original artifacts, including stdout, stderr,
progress records, commands and manifests:

| Run 39821810012 artifact | Python | Last active test | Child exit | Timeout |
| --- | --- | --- | ---: | --- |
| [12444917009](https://github.com/simoneferraresi/IOPanel/actions/runs/39821810012/artifacts/12444917009) | 3.12.10 x64 | `test_mainwindow_completion_respects_manual_trace_visibility[False]` | 3 | false |
| [12445970273](https://github.com/simoneferraresi/IOPanel/actions/runs/39821810012/artifacts/12445970273) | 3.12.10 x64 | Same | 3 | false |

Both enumerate the same nine plot modules, collect 282 tests and stop after
`test_completion_preserves_manual_hide_then_next_start_reopens` passes. Both
report this first exception:

```text
ViewBox.prepareForPaint -> updateAutoRange -> childrenBounds
 -> mapFromItemToView -> updateMatrix -> childGroup.setTransform
RuntimeError: libshiboken: Internal C++ object (ChildGroup) already deleted.
```

Then `GraphicsView.paintEvent -> super().paintEvent` reports an already-deleted
PlotWidget, followed by `Fatal Python error: Aborted`. The active Python frame
is pytest-qt's post-call `app.processEvents()`. Stack capture is `not_needed`
because these were exits, not watchdog timeouts: neither artifact contains a
native dump. Core, camera, collection/static checks and packaging passed.

The historical construction AV in #216 has no demonstrated initiating write.
This report does not assign that earlier AV, or every historical fatal exit,
to the mechanism established here.

## Reproduction and controls

New worktrees and locked driver-free environments use Python 3.12.8 x64,
uv 0.13.4, pytest 9.1.1, pytest-qt 4.5.0, pyqtgraph 0.14.0 and NumPy 2.5.3.
The matched Qt/PySide/Shiboken distributions are 6.11.2 and 6.12.0. Principal
experiments request `QT_QPA_PLATFORM=windows`; no offscreen substitution is used.
CI's Python 3.12.10 and Windows runner differ from this Windows 10 build 19045
host. No VmbPy or MATLAB Engine distribution is installed in these environments.

Initial complete plot runs: 282 passed on each version, child/supervisor 0,
no timeout. This does not cancel the CI failures. Fresh-process reductions:

| Workload | Qt 6.11.2 before | Qt 6.12.0 before |
| --- | --- | --- |
| Visible-completion test alone | Native exit 3221226525 | 1 passed, exit 0 |
| Entire two-parameter completion family | Native exit 3221226525 | 2 passed, exit 0 |
| Preceding hide/restart test alone | 1 passed, exit 0 | 1 passed, exit 0 |
| Original ordered pair | 2 passed, exit 0 | 2 passed, exit 0 |
| Reversed pair | Native exit 3221226525 | 2 passed, exit 0 |
| Full trace module | 21 passed, exit 0 | 21 passed, exit 0 |
| Plot-widget, trace, async export/import modules | 216 passed, exit 0 | 216 passed, exit 0 |

Each cell is one fresh process, not an estimate of long-term failure probability.
Qt 6.11 native stderr records an AV at `GraphicsView.paintEvent` during the same
post-call event processing. This alone does not identify its initiating operation.
The preceding test is **not required** for the controlled failure below.

## Confirmed ownership mechanism

`PowerMonitorTraceWidget` contains a normal Python reference cycle:
`widget -> ExportTaskLifecycle -> _owner -> widget`. Its parentless native root
is Python owned. Its plot, scene, PlotItem, ViewBox and ChildGroup are Python
created but Qt owned, confirmed with Shiboken ownership observations. The plot
is parented to the plot container; the scene is parented to the plot; the scene
owns graphics items and ChildGroup has the ViewBox as its graphics parent.

pytest-qt's `addWidget` registers a **weak reference**. After a test function
returns, registration alone does not retain the root. The repository's former
teardown hook starts too late: pytest-qt processes events in its **call hook**,
before `pytest_runtest_teardown` obtains strong temporary references.

A small independent process constructs one populated trace, releases its local
root reference, and enters native paint. A one-shot Python line trace calls
`gc.collect()` immediately before the installed ViewBox's
`self.childGroup.setTransform(m)`. It records weak references, ownership and
native `destroyed` notifications without retaining the root. Collection timing
is deliberately controlled; no native object is explicitly deleted by the probe.

Observed sequence on **both** Qt versions:

1. All six native wrappers are valid before paint and before the transform.
2. Collection finds the unreachable root cycle while the plot's Python paint
   override and ViewBox's Python update are still active.
3. Destruction notifications: root, PlotWidget, ChildGroup, ViewBox, PlotItem,
   scene. QObject's notification timing is not a claim about which C++ destructor
   instruction executes first; it establishes parent-initiated graph destruction.
4. The root weakref is dead. The active paint/update frames still retain child
   Python wrappers, whose native objects are now invalid.
5. `childGroup.setTransform` raises the exact CI ChildGroup exception; the
   continuing paint override raises the exact CI PlotWidget exception and aborts,
   child exit **3**, with no timeout.
6. With **only** a strong root reference added, the same collection point keeps
   the whole graph valid; accepted close and DeferredDelete then destroy it and
   the process exits **0**. This control passes on both versions.

The same one-shot collection inside the **original pytest test's** post-call
paint reproduces the exact CI signature and exit 3 before correction. After the
fixture correction it observes all wrappers valid, the original assertion passes,
then normal teardown destroys the graph and exits 0.

This is a confirmed missing test-owner lifetime, exposed through the interaction
of cyclic Python ownership, native child callback dispatch, pyqtgraph and
pytest-qt. It is **not demonstrated to originate in Qt 6.12**. The original CI
artifacts lack a GC trace, so their exact collection generation/instruction is
an inference from the matching controlled signature, rather than a recovered
native stack from those runs. No Qt-version-specific binding defect is required
to reproduce the failure.

### Explicit lifetime questions

| Question | Established answer |
| --- | --- |
| Which object goes first? | The owning root begins destruction; PlotWidget and the graphics graph are then invalidated. ChildGroup's notification precedes ViewBox's. |
| Subsequent access? | The already-running ViewBox transform callback, then the same GraphicsView paint override. |
| Python wrapper owner? | Root's only durable references were its unreachable cycle. Child wrappers are retained by attributes and active Python frames, without native ownership. |
| Already scheduled for deletion? | No deleteLater or explicit destruction is issued before the forced collection. |
| Close accepted? | The failing standalone/original visible-completion scenario has no preceding close request. Retained controls close successfully before deferred deletion. |
| Was a paint already queued? | Native show/update leads to paint after test return; the fatal deletion occurs **during** paint, not by a later event addressed to an already-dead receiver. The probe does not recover the original CI queue. |
| Invalid receiver or child? | Both are invalid after root collection: ChildGroup at the first access and PlotWidget at the second. A live Python frame does not make either C++ object live. |
| Python references survive C++ deletion? | Yes, directly observed for the plot, scene, PlotItem, ViewBox and ChildGroup wrappers. |
| Multiple tests required? | No. One populated visible trace with its root released is sufficient. |
| Responsible layer? | Test infrastructure ownership. Production MainWindow has a durable trace owner; no production change is justified by this reproduction. |

## Correction and regressions

`tests/conftest.py` supplies a small QtBot subclass using the existing session
`qapp`. It delegates registration and before-close callbacks to pytest-qt and
adds a test-scoped strong root reference. The inherited `add_widget` alias binds
to the override too. References are released after the existing teardown hook
accepts close and drains DeferredDelete. No drawing, GC, exception capture,
worker retirement, close gate or native destruction is disabled or bypassed.

`tests/test_plot_widget_lifetime.py` tests actual validity/destruction, not calls
to the implementation: both registration spellings, standalone/parented traces,
visible/hidden/closed windows, collection, actual rendering and autorange,
show/hide, explicit deletion/fixture deletion, and a helper exception after
registration. All graphs must die exactly once before fixture finalizers.
The ownership assertion fails before the correction under both Qt versions.

`tests/test_plot_widget_lifetime_process.py` runs the original visible-completion
test in a bounded fresh interpreter and triggers collection in ViewBox update
after its test body returns. It requires that boundary to execute, assertions to
pass and the **native child exit to be zero**. Timeout cleanup reaps the owned
process tree. It constructs no second QApplication in the parent pytest process.
Existing tests still cover data reset, completion visibility and refused/async
close. No existing assertion, skip, parametrization or partition is removed.

## Rejected or unsuccessful experiments

- An initial sandboxed supervisor could not query CIM or reap its child, and its
  tests encountered inaccessible system temp directories. Its supervisor exit 1
  and unknown child exit are an infrastructure failure, not a plotting verdict.
  Subsequent authorized runs isolate temporary paths and retain the old evidence.
- Collecting at paint entry produced a deleted-PlotWidget exception/native fatal
  exit, a useful but less specific reduction. Moving collection to the transform
  instruction reproduced **both** exact CI exceptions.
- An early retained control independently disposed a snapshot of every floating
  native window and aborted during cleanup. It did not test a valid disposal
  control. Closing/deleting only the owning root fixes the diagnostic's disposal
  ordering and provides clean retained controls. This is not a production fix.
- An initial diagnostic plugin import failed, exit 1; its subsequent corrected
  import reproduces the native failure. The failed evidence is retained.
- First static pass found an import-order error in the new process test; corrected.
- No upstream patch, backend selection, sleep, timeout change, skip, exception
  suppression or GC disable was adopted. Keeping a reference only at teardown
  would miss the already-demonstrated post-call paint boundary.

## Qualification commands and results

Environments are created with explicit `UV_PROJECT_ENVIRONMENT` and pinned uv:

```powershell
uv sync --locked --python <Python-3.12-x64> --no-default-groups --extra test --extra lint --extra typecheck --extra dev
$env:QT_QPA_PLATFORM = 'windows'
python -m ruff check .
python -m ruff format --check .
python -m pyright
python -m tools.ci_pytest_partition --verify
python -m tools.qt_stall_stress_supervisor --suite --suite-group plot --cycles 1 --timeout-seconds 300 --output <new-folder>
python -m tools.qt_reliability_campaign --output <new-folder>
```

Both complete reference campaigns passed, sequentially on the shared desktop.
The [sanitized process ledger](performance-evidence/qt-plot-widget-lifetime-2026-10-22.json)
records every principal child/supervisor exit, per-process summary, skip,
warning category, timeout, source state, before/after matrix and frozen result.
Raw local artifacts, diagnostic scripts, stdout/stderr, images and all failed
observations remain in the isolated worktrees' ignored `output/` directories.
No native dumps were collected or uploaded during this investigation.

| Qt | Exact qualified source | Core processes | Camera processes | Plot processes | MainWindow cycles | Child / supervisor exits |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 6.11.2 | `9e049ba6bfac267e6593f1c718d20dc861e686dc` | 3 | 30 | 20 | 100 | All 54 / 54 exit 0 |
| 6.12.0 | `00dd1cf757ad76380f1a1d29841a8b13dcc3101a` | 3 | 30 | 20 | 100 | All 54 / 54 exit 0 |

Both campaign drivers and both exact-collection checks exit 0. There are no
timeouts, failed entries, supervisor errors or unexpected native/Qt exception
signatures in their stdout/stderr. Each stress log contains exactly 100 completed
cycles. No partial campaign or pre-correction retry is counted toward these gates.

Each native core process reports **1,013 passed / one existing opt-in stress
skip**, each camera process **209 passed**, and each plot process **308 passed**.
The skipped stress entry is separately exercised for 100 full cycles above.
The two real GL tests run successfully in each local core process. No xfails or
new skips were introduced. Existing pyqtgraph/NumPy 2.5 array-shape deprecations
produce 188 core, 21 camera and 19 plot warnings under Qt 6.11.2. Qt 6.12 has
one additional local pytest-cache permission warning per process (189 / 22 / 20;
one warning in its stress process). These are preserved, not hidden; none is
a deleted-object exception. The cache warning concerns the isolated checkout's
generated `.pytest_cache`, not test assertions, settings or native cleanup.

Ruff, formatting and Pyright pass in both corrected environments. Exact collection
is **1,531 = core 1,014 + camera 209 + plot 308**. A separate direct comparison
against pristine stable-main collection confirms **all 1,505 original node IDs
are preserved**, with exactly 26 additions and zero removals. Both post-fix
fresh-process test-order matrices pass every cell: alone, parametrized family,
preceding test, original pair, reversed pair, full module and nearby GUI modules.
All 14 matrix children and their supervisors exit 0 without timeout.

The test-ownership regression fails before correction on both versions. The
bounded original-test regression also fails against pristine Qt 6.11 teardown:
its child aborts with exit 3 and its parent reports the failed exit assertion.
Corrected targeted tests pass, and the full campaigns exercise the bounded
post-call collection child in every plotting repetition (20 per Qt version).

The qualified worktrees were clean at campaign startup and differ only in
`pyproject.toml` and `uv.lock`. The Qt 6.12 source is published on the separate
`fix/qt612-plot-lifetime-control` branch; it does not alter PR #232's branch.
Subsequent report/ledger commits change documentation only.

### Independent Windows CI

| Corrected source | Tests workflow | Packaging workflow | Result |
| --- | --- | --- | --- |
| Stable correction `9e049ba` | [39827540157](https://github.com/simoneferraresi/IOPanel/actions/runs/39827540157) | [39827540289](https://github.com/simoneferraresi/IOPanel/actions/runs/39827540289) | All required checks pass |
| Qt 6.12 control `00dd1cf` | [39829733714](https://github.com/simoneferraresi/IOPanel/actions/runs/39829733714) | Independently built and smoked locally below | Static/collection and all three partitions pass |

Downloaded corrected CI manifests confirm native children exit 0, no timeout,
**1,011 core passed / three existing skips**, **209 camera passed**, **308 plot
passed** on both versions. These runners use Python 3.12.10 x64. CI's skips are
the manual stress entry and `test_real_gl_render_requires_a_usable_context` /
`test_real_viewer_restored_map_title_survives_failure_then_retry`, whose context
probes report GL error 1282. Those skips are capability results, not rendered GL
qualification. Local native and frozen rendering provide separate evidence.

### Frozen Windows application and actual graphics

Each version was built from its exact qualified source with PyInstaller 6.22.3,
using an independent build/cache/output directory:

```powershell
python -m PyInstaller --clean --noconfirm --distpath <new-dist> --workpath <new-build> packaging/IOPanel.spec
Copy-Item packaging/config.simulation.ini <new-dist>/IOPanel/config.ini
python tools/smoke_frozen.py <new-dist>/IOPanel/IOPanel.exe --timeout 45
python tools/smoke_frozen_opengl.py <new-dist>/IOPanel/IOPanel.exe --platform windows --timeout 45 --evidence-dir <new-folder>
```

The diagnostic driver also runs the ordinary smoke with its child platform set
to `windows`, and repeats GL with child-only `QT_OPENGL=desktop`. It records
actual frozen child exits and captures their stdout/stderr; no global setting or
production renderer policy changes. No failed smoke is silently retried.

| Qualification | Qt 6.11.2 | Qt 6.12.0 |
| --- | --- | --- |
| One-folder build | Exit 0 | Exit 0 |
| Ordinary frozen smoke, offscreen | GUI / wrapper 0 / 0 | GUI / wrapper 0 / 0 |
| Ordinary frozen smoke, native Windows | GUI / wrapper 0 / 0 | GUI / wrapper 0 / 0 |
| Native GL, automatic selection | Rendered, GUI / qualifier 0 / 0 | Rendered, GUI / qualifier 0 / 0 |
| Native GL, explicit desktop | Rendered, GUI / qualifier 0 / 0 | Rendered, GUI / qualifier 0 / 0 |
| Physical-profile rejection in all smoke modes | Expected exit 2, before startup | Expected exit 2, before startup |

All four GL processes render the synthetic map, grid, axis labels and updated
colorbar using **Intel HD Graphics 4600, OpenGL 4.3.0, driver 20.19.15.4835**.
Map screenshots, framebuffer captures and colorbar images are preserved locally.
The framebuffer contains 9,700 nonblack pixels, fully opaque alpha and a rendered
map with labels. The two versions' deterministic automatic-selection framebuffer
PNGs are byte-for-byte identical, SHA-256
`585e1baf576cc2295eb8e443c6db678f2d8df0d9cff2ef239c8ae3dd04fc55a8`.
Pixel checks and original-resolution visual inspection confirm actual rendering;
an initial resized preview appeared blank and was resolved by decoding and
comparing the preserved files, without changing or rerunning the application.
No `UNAVAILABLE` outcome is counted as rendered success.

These successful current Intel contexts do not demonstrate a correction to the
previous RDP software-GL / system-PyOpenGL loader mismatch. That fallback path
was not selected in this qualification and remains a distinct compatibility limit.

## Limits and independent acceptance

No real VmbPy/Vimba X callbacks, CT400 DLL operations/safe-state transitions,
PM5020/EDFA transport, piezo, MATLAB Engine startup/shutdown or physical laboratory
GUI operation is qualified. Driver-free simulated validation cannot accept them.
Automatic software OpenGL fallback under RDP remains a separate known Qt/PyOpenGL
loader compatibility question. Explicit desktop rendering must be reported as
such and never substituted for automatic selection.

The defect fix must be reviewed separately on stable main. No merge, deployment,
force push or refresh of PR #232 is authorized by a simulation pass. The upgrade
must remain draft until its acceptance gates and separately authorized operator
requirements are satisfied. Issue #216 must not be closed on these observations
alone, particularly its historical unroot-caused construction AV.

## Upstream contracts inspected

- [pytest-qt widget registration source](https://pytest-qt.readthedocs.io/en/stable/_modules/pytestqt/qtbot.html): weak registration; installed 4.5.0 source also inspected.
- [Qt QObject lifetime documentation](https://doc.qt.io/qt-6/qobject.html): ownership, deferred deletion and destructor behavior.
- [Shiboken ownership documentation](https://doc.qt.io/qtforpython-6.8/shiboken6/typesystem_ownership.html): Python references and native parent ownership are distinct.
- Installed pyqtgraph 0.14.0 GraphicsView, PlotWidget, PlotItem, GraphicsScene,
  ViewBox and ChildGroup sources: native parenting and prepare-for-paint dispatch.
