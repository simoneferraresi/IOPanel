# Qt/PySide6 lifecycle and test reliability standards

**Status:** repository development policy following [PR #236](https://github.com/simoneferraresi/IOPanel/pull/236) (2026-10-22). **Scope:** all new or changed pytest, Qt widget/worker, diagnostic, simulated camera/plotting, packaging, and native-GL code. This is a prevention policy, not a claim that historical issue [#89](https://github.com/simoneferraresi/IOPanel/issues/89), [#216](https://github.com/simoneferraresi/IOPanel/issues/216), or every physical-device exit has been definitively solved.

The [engineering investigation](qt-lifecycle-corrections-2026-10-22.md) is the evidence and implementation reference. It captured an actual Qt connection-mutex / Python GIL inversion, unsafe native-widget finalization, and a distinct Python 3.12.8 tracemalloc/thread-state race. Its conclusions are narrower than "all GUI crashes are fixed."

## Test fixture and widget ownership

1. Use pytest-qt's provided `qapp` and `qtbot`. The repository's `tests/conftest.py` owns one QApplication per test session, isolates `QSettings` to temporary INI paths per test and checks for outstanding app-owned QThreads/native window roots before session end. Do not construct another QApplication in ordinary in-process tests. Self-contained process diagnostics may construct one inside their isolated child, with explicit bounded cleanup.
2. Register test-created top-level widgets with `qtbot.addWidget(widget)`. If a helper constructs widgets, that helper must close and natively destroy its own graphs before returning, or transfer documented ownership to the test. The autouse `qt_test_scope` is a safety net for unregistered Python-owned top-level roots, **not** permission to omit explicit ownership.
3. Respect a close that is deferred or refused while an operation owns resources. A closed/hidden QWidget may still have a live C++ graph. The `pytest_runtest_teardown` hook waits for accepted close, checks `shiboken6.isValid`, arranges `deleteLater`, and flushes `DeferredDelete` before pytest-qt's unconditional teardown; do not bypass or weaken it. Keep Python wrappers valid as references without mistaking them for live native objects.
4. New tests that create native windows, helper panels or QThreads should assert observable shutdown/destruction where relevant, including exception, cancellation, partial initialization, and post-test boundaries. A test must not rely on CPython/Qt interpreter finalization to clean up resources.

## Worker and application lifetime

1. UI, QWidget/scene construction, and OpenGL work remain on the GUI/application thread.
2. Review `logic/qt_worker_lifecycle.py` before adding any new Python QObject worker, child timer, `QThread`, QRunnable signal object, or callback. Model ownership, affinity, connected signals, cancellation, and disposal explicitly. Preserve the established handoff: after worker completion, stop its timer/event activity and pending MetaCalls in its current affinity thread, move the stopped Python QObject tree to the **durable application thread**, then in the GUI join the finished native thread, parent as needed, and schedule/deplete deferred destruction. Do not make a GUI thread "pull" an object from the wrong affinity or use a temporary borrowed QThread wrapper that becomes invalid.
3. Distinguish `QThread.finished`, `QThread.destroyed` and the worker QObject's `destroyed` signal. Holding a Python reference and holding native C++ ownership are different. `deleteLater` is asynchronous and requires an event loop that will process it. No native QObject destructor should depend on an arbitrary background thread acquiring the GIL while holding Qt's connection mutex.
4. Camera initialization must build placeholders before workers start, retain unadmitted/failed cameras with their worker until safe disposal, and ignore late results after shutdown begins. Shutdown remains idempotent, honors exclusive CT400 input/operation ownership, does not bypass cancellation or active-work gates, and stops camera producers before conversion consumers. The same guarantees apply to scan, histogram, alignment and MATLAB-save workers.
5. For memory measurements involving native Python worker callbacks, begin allocation tracing before the workers and stop/reset only owned tracing after **all** relevant producers/consumers have joined; never stop a caller's tracer.

## Synchronization and regression design

- Use bounded event-driven waits (`qtbot.waitUntil`, `waitSignal`, `waitActive` where appropriate), barriers or signals. For a native keyboard shortcut, explicitly activate the receiving window and wait for the resulting state. Avoid timing-only sleeps, race-dependent immediate assertions and a shared desktop for concurrent native activation tests.
- When a bug was reproducible, preserve its before/after regression and original assertions. No generic `try/except: pass`, unconditional mock of the behavior under test, skipped/xfail substitutions, timeout inflation in place of fixing the race, or deleting tests to obtain green CI.
- Distinguish hardware-independent simulation from physical verification. Do not open physical drivers or MATLAB Engine in CI. Use fake devices that preserve realistic asynchronous ownership and cleanup.
- If a new test is capability-dependent (e.g. real OpenGL), a skip must explain the missing capability. Count it as unqualified native coverage, not a rendering pass. The known automatic software-GL / PyOpenGL loader mismatch under RDP is not corrected by `QT_OPENGL=desktop`; explicit desktop rendering is a separate supported qualification.

## Required checks (normal PRs)

From a **separate driver-free environment**, on the exact PR head and without touching lab configuration:

```powershell
uv sync --locked --no-default-groups --extra test --extra lint --extra typecheck
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pyright
uv run --no-sync python -m tools.ci_pytest_partition --verify
```

The `tests.yml` workflow runs all three independently supervised Windows groups (`core`, `camera`, `plot`) after exact node-ID collection. Newly added tests are discovered by filename and must be in **exactly one** partition. Core is the fallback; if a new module exercises camera state, native plotting or GUI teardown interactions, extend and test its group mapping in `tools/ci_pytest_partition.py`. Never silently filter a test out of one or all groups.

Do not use the raw pytest success count alone as a pass: the **actual child and supervisor process exit codes**, timeout state, and manifest matter. Native process crashes *after* pytest's summary fail CI. Preserve standalone fresh-process execution and the signed ProcDump / real-interpreter PID identity safety. A failure's manifest and local dumps must not be overwritten by a clean retry; only sanitized text/JSON may enter public CI artifacts.

## Higher-risk Qt lifecycle changes (pre-merge native gate)

For a PR that modifies `tests/conftest.py`, widget/worker ownership, QThread retirement, startup/shutdown, native pyqtgraph creation, camera conversion, tracing around workers, or native GL, require additional evidence beyond a single green CI run. On Windows with a native Qt desktop and a clean driver-free environment, use the repository's `tools.qt_reliability_campaign` plus targeted deterministic before/after regressions. The reference campaign is **3 core / 30 camera / 20 plot independent processes plus 100 full MainWindow simulated startup/shutdown cycles** (defaults in the tool). For targeted changes, justify any reduced selection; a change to shared fixture or worker-retirement infrastructure should use the full gate. Record the exact source commit, `QT_QPA_PLATFORM=windows`, Python/PySide/Qt versions, skips, native process identities, actual exit codes and manifests. Run cross-version Qt 6.12 qualification in a separately isolated environment if affecting its compatibility; keep the production lockfile at Qt 6.11.2.

Example full native campaign:

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run --no-sync python -m tools.qt_reliability_campaign --output output/qt-campaign-unique
```

For packaging/GL changes, separately qualify frozen simulation smoke and **real** native GL framebuffer/map/colorbar output. Record explicit `QT_OPENGL=desktop` selection if used; `PACKAGED_OPENGL_SMOKE_UNAVAILABLE` is not success. Do not edit Windows-wide GL/driver settings merely to manufacture a pass. All physical CT400, Vimba, PM5020, piezo and real MATLAB SDK shutdown validation is independent and operator-authorized; simulator passes never confer approval for unattended laboratory operation.

## Pull-request evidence checklist

Record in the PR:
- [ ] Exact tested commit SHA, Windows/Python/PySide/Qt versions, Qt platform, driver-free profile and changed scope
- [ ] Ruff, formatting, Pyright, exact collection and all three partition results, including **skips and supervisor/child exit codes**
- [ ] Lifecycle ownership/affinity and accepted close/native destruction boundaries for new GUI objects
- [ ] Deterministic regressions of success, cancellation, errors, late callbacks and shutdown where applicable
- [ ] Repeated native process evidence and frozen GL qualification **when applicable**
- [ ] Unresolved historical/capability/hardware limitations, with separate issues and no inappropriate pass claims

Repository branch protection should require the full `tests.yml` check set and `windows-package.yml` before merging code PRs. Changing a local test or contributor instruction is not a substitute for configuring GitHub's server-side merge gate; repository administrators must enable that protection/ruleset explicitly.
