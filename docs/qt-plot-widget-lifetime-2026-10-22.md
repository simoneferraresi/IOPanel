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

Final campaign and packaging results will be filled from the preserved process
records, with source SHAs, exact exits, skips, warnings and capability outcomes.
The reference gate is 3 core, 30 camera, 20 plot processes and 100 full simulated
MainWindow cycles **per Qt version**, executed sequentially on the shared desktop.
An incomplete campaign cannot satisfy it.

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
