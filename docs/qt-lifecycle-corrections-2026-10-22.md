# Qt lifecycle corrections — 2026-10-22

## Source and scope

Development uses `fix/qt-lifecycle-ownership` in an isolated worktree. The
laboratory checkout, configuration files, virtual environment, other validation
worktrees and physical instruments are untouched. Earlier frozen smoke runs
could write presentation preferences through the original shared QSettings
backend; the separate settings correction and this limitation are described
below. Draft PR: [#236](https://github.com/simoneferraresi/IOPanel/pull/236).

Refs were checked remotely before development and again during validation:

| Ref | SHA |
| --- | --- |
| Initial stable main / native reproduction | `8562ac0db83a58695c27ff540c491b3966e39b8a` |
| Updated stable main, merged without overwriting its new work | `5c2cadc2e4222f0376e7f4efb74ee1931d64a154` |
| Experimental PR #232, unchanged | `597953089b6094fd8071e3171da46591528f093a` |
| Worker/window correction, before the newly captured tracer fault | `9bd2bb892575c6ed16afa8b2e92409f470b8bc43` |
| Production correction / completed Qt 6.11 native gate | `8888c23590d576a39024784c7222748b15b20a05` |
| Keyboard-test synchronization / Qt 6.12 native gate | `95d21c5741379283f70260ce35e17920d887668c` |
| Correct diagnostic platform metadata | `a1f2d68a5c7e538bd78e397267f9d135430d6f20` |
| Isolate smoke preferences | `5b2bcaeaf3ad2ed9a930a0260b52a9fa7f60baa2` |
| Smoke error-path shutdown; final executable implementation | `759a40d19d369b5404581a3909391ac5d5dcbf3a` |

The two stable-main SHAs supersede the mission's historical main anchor.
The correction keeps Qt 6.11.2 in the lockfile; no experimental upgrade was merged.

Host: Windows 10 x64, build 19045; CPython 3.12.8 x64, MSC 1942. Driver-free
environments use uv 0.13.4, pytest 9.1.1, pytest-qt 4.5.0, pyqtgraph 0.14.0,
NumPy 2.5.3. The second environment differs in PySide6, matching Addons,
Essentials and Shiboken: 6.12.0 instead of 6.11.2. Principal GUI experiments
use `QT_QPA_PLATFORM=windows`. Existing explicitly offscreen subprocess tests
retain their own intended platform. Neither environment includes the default
`lab` dependency group, VmbPy or MATLAB Engine.

## History reviewed and what it established

Descriptions, discussions, changes and available CI evidence were reviewed,
including failed attempts and passing retries. Historical observations do not
become root-cause findings merely because a stack contains the same function.

| Incident / change | Observation or guarantee | Limit of that evidence |
| --- | --- | --- |
| [#89](https://github.com/simoneferraresi/IOPanel/issues/89), [#88](https://github.com/simoneferraresi/IOPanel/pull/88) | Stalls during camera controls; camera startup removed from unrelated CT400 tests | Reduced exposure, did not establish the native lock mechanism |
| [#149](https://github.com/simoneferraresi/IOPanel/pull/149) | Initialization/shutdown and timer regressions | Individual green runs did not prove interpreter-finalization safety |
| [#181](https://github.com/simoneferraresi/IOPanel/pull/181) | Native-platform stress stalled at cycle 14/20; Python stack in ParameterControl; two later batches passed | No dump from the failing interpreter; passing retries did not resolve #89 |
| [#216](https://github.com/simoneferraresi/IOPanel/issues/216) | `0xC0000005` during pyqtgraph object construction on unrelated changes | No demonstrated initiating write or common cause with camera hangs |
| [#167](https://github.com/simoneferraresi/IOPanel/issues/167), [#168](https://github.com/simoneferraresi/IOPanel/pull/168) | Async-import QThread/native destruction boundary mattered; parent and strong registry retained through `destroyed` | A correct thread registry does not determine the worker destructor's execution thread |
| [#215](https://github.com/simoneferraresi/IOPanel/pull/215) | External bounded supervisor; native nonzero exit is a failure | Windows venv launcher PID can differ from the actual interpreter PID |
| [#217](https://github.com/simoneferraresi/IOPanel/pull/217), [#218](https://github.com/simoneferraresi/IOPanel/pull/218) | Camera-free unrelated tests; disjoint core/camera/plot processes with exact node coverage | Process boundaries limit contamination but cannot fix within-partition ownership |
| [#230](https://github.com/simoneferraresi/IOPanel/pull/230), [#232](https://github.com/simoneferraresi/IOPanel/pull/232) | Qt 6.12 camera assertions pass, then process fails `0xC0000409`; independently reported on lab PC | UCRT fatal exit alone does not identify its caller or prove memory corruption |
| [#231](https://github.com/simoneferraresi/IOPanel/pull/231) | Optional CIM lookup timeout must not abort healthy tests | Retained; identity failure still prevents unverified dump capture |
| #50/#53/#54/#56/#67/#118/#203 | Queued invocation, direct quit, stale-owner checks, producer/consumer order, real test cleanup and export native registries | Valid guarantees preserved; none substitutes for explicit worker retirement or test native destruction |

## Confirmed mechanisms and corrections

### 1. Background QObject destruction and the GUI connection mutex

Unchanged initial main stalled at cycle 21 of a 100-cycle native Windows stress
run. The dump was taken from the **actual** interpreter, PID 13224, rather than
its venv launcher, PID 11828. Microsoft CDB all-thread stacks show:

```text
GUI:    Python / PyObject_Str -> Qt connection setup
        -> QObjectPrivate::connectImpl -> QBasicMutex::lockInternal
worker: QObject::~QObject -> generated QtCore wrapper
        -> Sbk_GetPyOverride -> PyGILState_Ensure -> GIL wait
```

This is direct evidence of a native Qt/GIL lock inversion during background
destruction. The Python construction frame is where the GUI blocks, not proof
that a slider calculation itself deadlocked. Exact initiating memory addresses
and native stacks remain in the local dump and debugger log.

A second CDB inspection correlates **the same mutex address** in both recovered
frames: GUI `QObjectPrivate::connectImpl+0x143` and background
`QObject::~QObject+0x4f5` both retain `0x00007ffd89b4d3a8` in R14. Disassembly
locates the destructor's virtual `disconnectNotify` call. Qt 6.11.2 uses a
131-entry connection-mutex pool, so unrelated newly constructed objects can
collide with this lock; sharing a camera instance is not required. This matches
the [versioned Qt source](https://github.com/qt/qtbase/blob/v6.11.2/src/corelib/kernel/qobject.cpp#L992),
which holds the receiver's connection lock while notifying its senders.

`logic/qt_worker_lifecycle.py` now returns stopped Python QObject workers and
their children to the durable application thread before native destruction.
The direct `QThread.finished` callback runs in the finishing thread, stops child
timers, removes pending MetaCall events, and pushes affinity to the GUI. This
prevents stale hardware commands from executing after the move. A queued GUI
callback joins the native thread, establishes parentage and posts deletion.
TaskRunner, camera conversion, histogram monitoring, alignment, scanning and
MATLAB-save QObject workers use the shared retirement contract.

Borrowing the destination thread through a transient QObject initially
invalidated other Python observers' QThread wrappers. Twelve PM5020 affinity
checks caught that regression. The destination is now borrowed from the durable
QCoreApplication owner; both the original Qt-thread check and an independent
Python OS-thread check pass. This failure was corrected, not hidden by changing
the original assertion.

Failed/cancelled physical-camera initialization had a second route to background
destruction: its unparented local camera was dropped without being handed to
MainWindow. The camera now belongs to the initialization worker until successful
handoff; unsuccessful cameras retire with that worker. Four hardware-free tests
using real QObjects fail before this correction and pass after it (open failure,
cancellation before/after open, exception). Exception traceback retention no
longer postpones their native lifetime to interpreter shutdown.

**Certainty:** the captured lock inversion and background destructor affinities
are confirmed. `finished -> worker.deleteLater` is an ordinary documented Qt
pattern; the binding's Python virtual hooks create this additional lock/GIL
interaction. The correction avoids the captured paths. This does not establish
that every historical GUI stall has this exact cause.

### 2. Native window graphs surviving into Python finalization

The latency characterizer closed but did not destroy panels, and its tests
could create a temporary QApplication before pytest-qt's application fixture.
Original tests reproduced post-summary `0xC0000409` in 4/8 Qt 6.12 processes.
Against unchanged baseline source, Qt 6.11 completed 20/20 minimized processes;
Qt 6.12 failed its fourth process after three clean exits. That comparison uses
the same host and matched environments, not the laboratory installation.

A captured termination shows Windows heap validation reporting a block modified
after free, followed by an AV through `QObjectPrivate::deleteChildren`,
`QObject::~QObject`, PySide `destroyQCoreApplication` / BindingManager cleanup
and `Py_FinalizeEx`. This local native evidence establishes a destruction-time
fault; it does **not** identify the earlier invalid write or establish that the
laboratory's UCRT fatal-exit stack is identical.

The helper now destroys its own panels and drains DeferredDelete before returning.
Its tests reuse `qapp`. A retained-wrapper regression proves native panels are
already invalid when the helper returns. Twenty independent runs of the original
six tests, copied outside the new conftest and using only the helper correction,
then exited 0 on Qt 6.12. Another 20 corrected latency runs also exited 0.

A later full-core candidate still aborted after 1,004 passing assertions. Binning
the modules and inspecting the session boundary found 25 MainWindows / 2,223
native top-level widgets retained by a subset containing the CT400 connection
helper. That helper omitted `qtbot.addWidget`; merely closing windows left their
native graphs for application cleanup. A deterministic registration test failed
before correction and passes after it.

`tests/conftest.py` now owns one session application, isolates QSettings to each
test's temporary INI directory, waits for **accepted** closes, consumes the
registered widget list before pytest-qt's unconditional close/delete pass,
destroys unregistered Python-owned native window roots, and explicitly drains
DeferredDelete. It retains Python wrappers safely while their native objects
are deleted. Session finalization asserts that native window roots and running
application-owned QThreads are absent and the global pool is finished.

pytest-qt 4.5's normal teardown calls `close(); deleteLater()` even when close
is refused. It also tests only the weakref, so a surviving Python wrapper to an
already deleted C++ object raises on `close()`. The replacement teardown handles
both real deferred close and invalid wrappers; regressions exercise the actual
hook and fixture-finalizer boundary. Tests that fabricated active operations now
finish that fabricated state, rather than bypassing the real shutdown gate.

**Certainty:** leaked native graphs and the minimized before/after process
failure are confirmed test/helper defects. The native heap's initiating write
is unknown. Multiple unsafe finalization paths existed; fixing the latency
helper alone did not resolve the full core partition.

### 3. Allocation-tracer teardown racing native Python thread-state creation

The `9bd2bb8` Qt 6.11 campaign failed on camera process 3 during
`test_simulated_display_capture_reports_pipeline_and_memory`, exit
`3221225477` / `0xC0000005`. This reproduces the earlier intermediate camera
AV **after** the native-window correction. It is an independent confirmed
diagnostic/runtime failure, not evidence that NumPy percentile itself is faulty.

The local exception monitor captured the actual failing interpreter. CDB
exception and all-thread stacks show:

```text
native Qt thread, no active Python frame:
  PySide::SignalManager::qt_metacall
    -> Shiboken::GilState
    -> PyGILState_Ensure / Python thread-state allocation
    -> CPython tracemalloc allocator path
    -> access violation writing address 0x8 (null pointer)
GUI: NumPy metrics / percentile, waiting to reacquire the GIL
```

With exports alone the fault is labelled `PyTraceMalloc_Start+0x390`; that is
not a claim that the public start function was invoked on the failing thread.
The native caller chain identifies the tracing allocator/thread-state path.
CPython [#128679](https://github.com/python/cpython/issues/128679) records this
stop/GIL-acquisition race; its [3.12 backport](https://github.com/python/cpython/pull/129022)
was merged after the laboratory baseline's 3.12.8 release.

The simulation characterizer previously installed tracing after starting its
native workers, then removed the allocator hooks while the workers were still
servicing queued callbacks. It now starts tracing before constructing those
workers and removes its hooks only after producer and conversion threads have
joined, including exception cleanup. It never stops or resets a caller-owned
tracer. Constructed cameras and panels enter the cleanup registries immediately,
before later attachment/open operations can fail. Memory results retain real
allocation tracing and now report warmup baseline, net change and peak scope
explicitly; this does not disable memory measurement or frame acquisition.

The original ordering fails a deterministic real-worker stop-boundary regression;
the corrected normal and exception paths pass. Separate tests preserve caller
tracing and verify producer shutdown after partial attachment failure. Fresh
acceptance campaigns begin at `8888c23`; no pre-correction clean run is counted
as successful validation of this mechanism.

### 4. Startup and shutdown ordering

MainWindow now owns its startup QTimer and latches shutdown on the first close
request. Close cancels the timer, lazy initialization/refresh/recovery entry
points reject new work, and late camera results are closed and discarded.
Every enabled camera placeholder is constructed before the first initialization
worker starts. Successful cameras become MainWindow children. Shutdown remains
idempotent and preserves camera producer-before-consumer teardown and existing
CT400 selected-input/operation ownership gates.

Deterministic old-code regressions demonstrated startup after close and interleaved
placeholder/worker construction. The new ordering removes that overlap; the
native lock capture supplies a stronger causal finding than ordering alone.

### 5. Diagnostic process identity

Windows CPython venv launchers forward execution to a base interpreter child
whose argv[0] differs. Comparing complete command-line strings selected the
launcher, yielding an irrelevant tiny dump. The supervisor now parses Windows
arguments with CommandLineToArgvW, compares the forwarded arguments, parent,
creation time and base executable, rejects ambiguous identity, and captures the
interpreter. A real venv forwarding probe verifies distinct launcher/interpreter
PIDs. Owned process-tree termination is tested using native process handles for
both child and grandchild. Optional CIM timeouts retain #231's behavior.

### 6. Native keyboard-test synchronization

The first Qt 6.12 camera process at `8888c23` exited 1 with 208 passed and one
failure: the simulation integration test sent F12 and immediately asserted that
the floating log console was visible. This was an assertion failure, without a
native fault. The test did not establish native window activation before using
an application shortcut, or wait for the resulting native visibility transition.
The desktop was shared with the concurrent Qt 6.11 campaign; the original failure
does not record enough focus state to prove exactly which activation event won.

The test now raises/activates its window, waits through pytest-qt's real
`waitActive`, sends the same F12 event, and waits for the resulting visibility.
Every original assertion and all real camera/plotting behavior remain. Twenty
independent native Qt 6.12 target processes exited 0. The replacement full Qt 6.12
campaign runs alone on this desktop. Production code is identical between
`8888c23` and `95d21c5`; only this test's synchronization differs.

### 7. Diagnostic metadata and smoke preference isolation

The simulated display tool always labelled its requested platform `offscreen`,
even when its actual QApplication used `windows`. It now records the request
from the environment and separately records `QApplication.platformName()`.
The existing real-frame regression checks both fields. The campaign manifests
already recorded the native platform correctly; no earlier failure is relabelled.

The ordinary frozen smoke wrapper redirects LOCALAPPDATA for its temporary
logs, but Windows QSettings NativeFormat still uses the operator's registry.
The smoke entry point previously constructed ordinary AppSettings and therefore
could read and write presentation preferences. Smoke mode now receives an
explicit INI backend beside its own log. Normal application preferences keep
their existing backend. A real-QSettings regression rejects replaying the old
backend selection, passes with the correction, and checks that writing smoke
preferences leaves the normal backend unchanged. Its normal backend is itself
redirected by the test fixture, so this regression never touches the registry.

Earlier local invocations of the original frozen smoke may have changed operator
presentation preferences. Their original values were not captured; no speculative
registry rollback was attempted. No hardware configuration file, physical device
or laboratory environment was changed. Subsequent package verification uses the
corrected settings boundary.

The same audit found a concrete smoke error-path defect: failure to save the
camera screenshot called `app.exit(5)` without closing MainWindow. A new
process regression runs the real application and acquisition, forces only
`QPixmap.save` to fail, and observes the window's shutdown state and native
threads when `app.main()` returns. The old path fails the shutdown assertion;
the corrected path closes through the normal hardware-safe sequence before
returning the same error code 5. This regression's child is bounded, reuses the
existing process-tree reaper on timeout and restores the Python exception hook
so assertion reporting cannot open the application's modal exception dialog.
An initial diagnostic attempt hit that modal hook and timed out; the corrected
probe then demonstrated the actual unclosed-window assertion directly.

### Application entry-point audit

Thirty independent native Windows real `app.main()` smoke processes per Qt
version exited 0, without a captured native exception. The observation factory
uses the real MainWindow and retains only a weak reference; settings use isolated
INI storage. At event-loop return, shutdown was complete, application-owned
QThreads were stopped and the global pool was idle. The closed native MainWindow
and its graph still existed for QApplication/PySide finalization. These observations
do not prove that finalization is safe for every optional backend; they did not
establish a failing source-entry-point reproduction.

One exploratory probe blindly deleted every top-level widget, including Qt-owned
internal popups, after `app.main()` returned and then aborted. Its cleanup was
invalid and is excluded as application evidence. The passive probes perform no
manual graph deletion. A further 30-process-per-version pass validates the smoke
settings correction; its result appears in the published ledger.

## Ownership audit and invariants

| Resource | Construction / affinity / native owner | Completion and destruction boundary |
| --- | --- | --- |
| TaskRunner / QThread | GUI; application owns runner, runner initially owns QThread and unstarted worker | Direct worker result quits thread; runner survives through native thread destruction |
| Executing QObject worker | GUI-created, detached and moved on start; Python owner retained | Direct finished returns worker/children; GUI joins, parents, then deferred native deletion |
| Failed initialization camera | Worker constructs and owns it; physical open/close remain there | Moves as worker child and dies on GUI; successful camera detaches before GUI handoff |
| Admitted camera | Application thread; MainWindow native parent and camera list | Existing shutdown cancels panel work, stops/join producers, then consumers |
| Conversion mailbox / QImage | Dedicated QThread; NumPy frame retained under lock; emitted QImage copied | Producers disconnected/stopped before conversion teardown; queued presentation is panel-owned |
| Histogram and alignment | GUI parents QThreads; service workers use shared retirement | Existing operation state completes hardware cleanup before window release; histogram timer explicitly stopped |
| Scan import/export | GUI receiver; existing parent and strong native registries from #168/#203 | Result and thread-finished both required; Python task retained until QThread.destroyed |
| QRunnable signal objects | Constructed on GUI, now parented to owning window/panel | Existing operation gates hold owner; terminal signal schedules native signal-object deletion |
| Graphics / plots / GL | GUI-owned QWidget/scene graph; no background graphics creation added | Test graphs deleted before application finalization; existing timers stopped; frozen native GL tested |
| Test widgets / QApplication | One pytest-qt application; scoped temporary settings | Accepted close precedes deleteLater; explicit DeferredDelete precedes test/session release |
| MATLAB manager | Existing GUI timers and optional startup-disposal thread | Not present in driver-free native reproductions; real SDK termination still requires separate qualification |

`finished` is not `destroyed`, and `deleteLater` is not an immediate destructor.
QThread.finished precedes native thread-local cleanup; the retirement callback
joins before disposing. QObject.destroyed is the native lifetime boundary even
if Python references remain. A queued callback needs an executing receiver event
loop; posting deletion after that loop has stopped cannot be assumed to work.
Only a worker executing in its current affinity thread can push itself to the
GUI; a GUI callback cannot pull a stopped worker. Native parenting retains the
C++ tree; explicit Python references retain wrappers through queued results.

These distinctions follow Qt's [QThread contract](https://doc.qt.io/qt-6/qthread.html),
[QObject deletion contract](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QObject.html)
and [thread-affinity rules](https://doc.qt.io/qtforpython-6/overviews/qtdoc-threads-qobject.html).
Windows command parsing uses the documented
[CommandLineToArgvW contract](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-commandlinetoargvw).

## Reproduction and validation

Create a new environment; do not reuse the laboratory environment:

```powershell
$env:UV_PROJECT_ENVIRONMENT = '.venv'
uv sync --locked --no-default-groups --extra test --extra lint --extra typecheck --extra dev --python 3.12
$env:QT_QPA_PLATFORM = 'windows'
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -m tools.ci_pytest_partition --verify
.venv/Scripts/python.exe -m tools.qt_reliability_campaign --output output/qt-campaign-unique
```

For Qt 6.12, create another environment with the same locked sync, then install
`PySide6==6.12.0` into that environment only; do not modify the project lockfile.
Pass its interpreter using `--python` or invoke the campaign module with it.

Each campaign defaults to 3 core, 30 camera, 20 plot processes and 100 real
simulation startup/shutdown cycles. It verifies exact node collection first,
records source SHA/status, runtime versions, every supervisor/child exit code
and timeout, and stops on the first failure. A passing launcher with a nonzero
manifest is a failure. Evidence directories must be new; no retry erases a result.

Baseline stress, from the unchanged baseline worktree using the matched interpreter:

```powershell
python -m tools.qt_stall_stress_supervisor --cycles 100 --timeout-seconds 300 --output output/baseline-unique
```

Packaging commands:

```powershell
.venv/Scripts/python.exe -m PyInstaller --clean --noconfirm packaging/IOPanel.spec
Copy-Item packaging/config.simulation.ini dist/IOPanel/config.ini
.venv/Scripts/python.exe tools/smoke_frozen.py dist/IOPanel/IOPanel.exe --timeout 45
.venv/Scripts/python.exe tools/smoke_frozen_opengl.py dist/IOPanel/IOPanel.exe --platform windows --timeout 30 --evidence-dir output/opengl-unique
```

Raw local evidence lives under ignored `output/lifecycle/`. Signed Microsoft
ProcDump and CDB captured real processes; no persistent Windows-wide debugger,
registry or security configuration was changed. Dumps are kept local, excluded
from commits and GitHub artifacts. Only text summaries and sanitized result
records belong in reviewable evidence.

## Acceptance results and remaining limits

The complete process ledger is published as
[qt-lifecycle-validation-2026-10-22.json](performance-evidence/qt-lifecycle-validation-2026-10-22.json).
Only complete, zero-failure campaigns qualify; earlier clean entries do not
erase a failed or interrupted campaign.

Both complete native gates passed:

| Qt / PySide6 | Qualified source | Core processes | Camera processes | Plot processes | Real-frame MainWindow cycles | Child / supervisor exits |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 6.11.2 | `8888c23` | 3 | 30 | 20 | 100 | All 54 / 54 exit 0 |
| 6.12.0 | `95d21c5` | 3 | 30 | 20 | 100 | All 54 / 54 exit 0 |

Neither gate has a timeout or captured native exception. Later changes correct
diagnostic platform metadata and isolate smoke preferences; they do not change
the worker, camera, MainWindow or plotting lifecycle algorithms qualified by
these campaigns. Additional complete partition rounds and package checks cover
those final changes and are recorded separately by source SHA.

Final exact collection is **1,502 = core 1,011 + camera 209 + plot 282**. This
preserves all 1,476 cases in updated stable main and adds 26 regressions. The
two large native gates above precede the final smoke error-path regression:
they collect 1,501 and report **1,009 core passed / one existing opt-in stress
skip**, **209 camera passed**, **282 plot passed**. Final frozen-source partition
rounds also run the added real application-error process test and report
**209 camera passed**, **282 plot passed**, and **1,008 core passed / three existing skips**
per version: the opt-in stress test and two GL capability tests. During this
last round the graphics environment selected Qt's software GL library, as
described below; these are existing capability skips, not new coverage changes.
The opt-in test is separately run with 100 complete cycles per Qt version.
No xfails or coverage-reducing skips were added. Ruff, formatter and Pyright pass;
Pyright also passes when explicitly pointed at the Qt 6.12 environment.

At `8888c23`, GitHub [tests](https://github.com/simoneferraresi/IOPanel/actions/runs/39765332327)
and [packaging](https://github.com/simoneferraresi/IOPanel/actions/runs/39765332325)
pass. Those runners use Python **3.12.10**, rather than local **3.12.8**. CI lacks
two real GL contexts, using the tests' existing capability skips, and its frozen
OpenGL smoke reports **UNAVAILABLE**. Local native core executes both real GL
tests; the local Qt 6.11 frozen smoke reports **PACKAGED_OPENGL_SMOKE_OK** and
renders the synthetic map. A green CI package workflow alone does not establish
successful native GL rendering. The isolated Qt 6.12 package also builds and
reports **PACKAGED_SMOKE_OK** and **PACKAGED_OPENGL_SMOKE_OK**. The ordinary
existing frozen smoke uses `offscreen`; the local frozen OpenGL checks explicitly
use `windows` and produce real framebuffer/map/colorbar evidence. Final rebuilds
include the smoke-settings correction and have their own exit-code records.

At final executable source `759a40d`, both one-folder builds and both ordinary
frozen simulation checks (offscreen and a native-Windows variant) exit 0. Both
final OpenGL wrappers exit 0 with **PACKAGED_OPENGL_SMOKE_UNAVAILABLE** and
`glGetString` error 1282. The stricter qualification driver therefore exits 1;
this result is retained as a failed rendering qualification, not counted as a
rendering pass or silently retried.

A minimal native Qt/PyOpenGL process, with no IOPanel imports, reproduces the
same result in both versions. Qt's current context is in `opengl32sw.dll`
(VMware llvmpipe); PyOpenGL calls system `opengl32.dll`, whose WGL current
context is null. Raw system-library GL errors remain 1282, while querying the
software library reports its valid context and llvmpipe renderer. This is an
independent GL-loader interoperability limitation in the current RDP session.
It is separate from QObject destructor ordering and reproduces before any
IOPanel scene or graphics item exists. Qt documents its dynamic software
fallback in [Windows graphics support](https://doc.qt.io/qt-6/windows-graphics.html).

Earlier source `5b2bcae` rendered successfully in both Qt versions using
**Intel HD Graphics 4600 / OpenGL 4.3.0, driver 20.19.15.4835**, with framebuffer,
map and colorbar evidence. The subsequent production change only closes a
smoke screenshot-error branch. That evidence supports the unchanged viewer
implementation, but final-source Intel rendering is **not requalified** in
the current environment. No Windows session, GPU driver or global graphics
setting was changed to obtain a passing result. Resolving software-GL/PyOpenGL
loader interoperability or obtaining a compatible graphics session remains a
separate validation requirement.

Two Qt-only reductions (1,000 destruction cycles each) did not reproduce the
camera construction hang. A simple cyclic-widget background-GC probe also did
not demonstrate background native QWidget destruction. These negative reductions
do not override the real IOPanel dump's matched connection mutex, and do not
justify disabling garbage collection or blaming every Qt object.

Confirmed failures from intermediate implementations remain part of the ledger:

| Evidence directory | Failure / disposition |
| --- | --- |
| `baseline-stress-01` | Baseline cycle 21 hang; actual interpreter dump captured |
| `baseline-exit-comparison` | Qt 6.12 fourth minimized process `0xC0000409`; all 20 Qt 6.11 minimized controls clean |
| `exit-reduction-small` | Original Qt 6.12 latency helper: 4/8 post-summary failures |
| `candidate-core-01` / `candidate-plot-01` | Transient borrowed-QThread-wrapper regression; corrected, original affinity assertions retained |
| `candidate-qt612-camera-01` | All 197 assertions passed then native failure; helper correction followed |
| `acceptance-final-qt611/camera-06` | Intermediate `dc0f35d`: native AV during simulated display characterization; no native dump from this occurrence |
| `acceptance-v4-qt611/camera-03` | `9bd2bb8`: same display test AV with actual exception dump; CPython tracing/GIL-acquisition mechanism identified and corrected in `8888c23` |
| `acceptance-v3-qt612` | Controlled stop after 39 completed clean entries when the same candidate failed on Qt 6.11; all evidence retained, incomplete campaign excluded from final acceptance |
| `acceptance-final-qt612/core-01` | Intermediate `dc0f35d`: 1,004 assertions passed then `0xC0000409`; native-window leak correction followed |
| `window-scope-core-01` | Stricter teardown exposed unfinished synthetic ownership in tests; corrected rather than weakening the gate |
| `acceptance-v3-qt611/camera-03` | Diagnostic plugin used PID alone as a directory name; Windows PID reuse caused FileExistsError before tests, child exit 3. Fixed local monitor to use PID plus UUID; retained failure and restarted the full Qt 6.11 campaign |
| `acceptance-v5-qt612/camera-01` | `8888c23`: F12 visibility assertion, exit 1, no native fault; native activation synchronization added in `95d21c5`, followed by 20 clean independent target processes |
| `qt612-debug-01` | Native heap/destruction diagnostic captured; initiating write still unknown |
| `qt612-core-debug-01` | Debugger-following-child experiment timed out after 300 s following assertions; not a successful run |
| `smoke-save-error-before` / `smoke-save-error-before-v2` | Initial probe hit the modal application exception hook; corrected probe directly fails the unclosed-window assertion. Shutdown correction then passes both Qt versions |
| `packages-759a40d` | Final OpenGL qualification fails in current RDP environment; Qt software GL / system PyOpenGL DLL mismatch reproduces in a minimal process without IOPanel. Both ordinary frozen modes and builds pass |

The intermediate camera AV is not proven to be the same fault as historical
#216 plotting construction crashes. Passing plot repetitions cannot identify an
unobserved historical invalid write. Real hardware and MATLAB Engine were never
opened. Conditional legacy forced-termination fallback paths in MATLAB saving
and histogram cleanup, and unresolvable MATLAB startup-future disposal, are
independent limitations outside the reproduced native trigger; they must not be
represented as qualified safe hardware shutdown. Physical device cleanup, vendor
SDK callback behavior and real MATLAB shutdown require a separately authorized
operator phase. Green simulation results do not authorize Qt 6.12 deployment.

## Engineering assessment

- **Camera initialization stalls:** the captured connection-mutex/GIL deadlock
  has a concrete ownership correction, deterministic destructor-affinity
  regressions, and two clean 100-cycle native real-frame campaigns. Two-phase
  placeholder construction and the closing latch also have old-code regressions.
  Other historical stalls without native evidence cannot all be assigned to
  this mechanism.
- **Plotting access violations:** unsafe worker and test graph lifetimes are
  corrected and 20 complete native plotting partitions per Qt version exit 0.
  The initiating cause of historical #216 remains unconfirmed; this is not a
  demonstrated definitive fix for every plotting AV.
- **Post-pytest termination:** minimized post-summary failures reproduce before
  the helper correction, stop in the controlled helper comparison, and do not
  recur in either complete accepted campaign. The laboratory WER initiating
  caller remains unconfirmed, so its exact signature is not claimed solved.
- **Display-measurement AV:** captured native tracing/thread-state stacks and
  deterministic tracer-boundary tests justify the allocator-hook ordering fix;
  fresh complete native gates pass after that correction on Python 3.12.8.
- **Qt 6.12 adoption:** the corrected code has substantially stronger cross-version
  evidence. The stable lockfile remains at 6.11.2, PR #232 remains unmerged, and
  optional physical SDK/MATLAB shutdown still needs operator-authorized testing.

No merge or production deployment was performed. The PR remains a draft so these
limits, native ownership changes and hardware-dependent paths receive review.
