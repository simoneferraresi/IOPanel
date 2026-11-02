# Python Qt worker retirement audit — 2026-11-02

Scope: tracked Python sources in `logic`, `hardware`, `ui`, `tools`, `tests`,
and application entry points, based on repaired B `1cd12c15108f9ddfbaf56808685157c9fba3ffa7`.
Searches cover worker class definitions, QThread construction, `moveToThread`,
`finished.connect`, `deleteLater`, TaskRunner construction, QRunnable and Python
threads. This is a source ownership audit, not qualification of physical SDKs.
The primary lifecycle guidelines and executable helper take precedence over
historical Qt-version wording in the supplementary standards document.

## QThread execution owners

| Execution path | Classification / action |
| --- | --- |
| `ui/shg_synchronized_panel.py`, SimulatedCT400SyncWorker | Noncompliant direct `finished → worker.deleteLater`; migrate to `retire_worker_on_thread_finish`. Preserve acknowledgment/lease protocol. Keep unstarted construction GUI-owned until wiring completes. |
| `logic/task_runner.py`, BaseWorker and TaskRunner | Shared contract already used; worker initially parented to runner, detach/move on start, strong references and application-owned runner retained through native retirement. |
| CT400InitWorker, PiezoInitWorker, VimbaSystemInitWorker, CameraInitWorker, CameraRecoveryWorker (`hardware`) | TaskRunner clients; inherit the shared retirement. Successful camera handoff pushes the camera from its creating thread to the application; unsuccessful cameras remain worker children. These handoffs are not competing worker-retirement implementations. |
| PM5020MonitorWorker, EDFAMonitorWorker (`logic`; panels in `ui`) | Persistent TaskRunner services; cooperative device shutdown before thread quit. Shared contract already used. |
| ScanImportWorker, ScanExportWorker, PowerMonitorExportWorker | TaskRunner clients; export/import registries retain tasks through native thread destruction and consume queued results. Already use shared contract. |
| `ui/alignment_panel.py`, AlignmentWorker | Shared contract registered before start. Panel owns QThread; thread deletion is distinct from worker deletion. Compliant. |
| `ui/control_panel.py`, ScanWorker | Shared contract with documented `delete=False`; GUI completion consumes terminal worker state before `deleteLater`. This is a supported parameter of the same contract, not a second retirement implementation. |
| `ui/control_panel.py`, PowerFetchWorker | Shared contract registered before starting persistent monitor thread. Compliant. |
| `ui/camera_widgets.py`, ImageConversionWorker | Shared contract; producers disconnected/stopped before mailbox/consumer stop. Mailbox holds NumPy array references and emitted QImage is copied. Compliant retirement; physical SDK paths remain independently unqualified. |
| `ui/plot_widgets.py`, MatlabSaveWorker | Shared contract registered before native start; GUI signal retains thread/worker until completion processing. Real MATLAB Engine is outside this task. |
| `tests/test_matlab_engine_manager.py`, FinishedWorker in `test_plotwidget_clears_finished_thread_refs_and_starts_later_fig_thread` | Test-only noncompliant direct worker deletion; migrate to the shared helper in this change. |
| Other test-created QThreads in MATLAB/camera integration | One active moved-worker fixture already uses the helper. Deleted-wrapper fixtures create a GUI-affine QObject that is never moved; their explicitly joined bare thread has no executing Python QObject worker. No worker migration is needed. |
| `tests/test_task_runner_threading.py`, `tests/test_qt_camera_lifecycle.py` | Exercise TaskRunner and existing native destruction, timer, pending-MetaCall contracts. Reuse session qapp and explicit native teardown. |

All remaining production `QThread.finished → *.deleteLater` occurrences target
the GUI-affine **QThread object**, after registering worker retirement. They do
not destroy a Python worker in its execution thread. The helper's internal
GUI-side worker `deleteLater` is the mandated final stage, not an exception.

## Pool tasks and Python threads

| Path | Classification / disposition |
| --- | --- |
| CT400ConnectionWorker, PiezoConnectionWorker, ViewModeWorker | QRunnable tasks, not moved QObject workers. Their separate GUI-created signal QObjects are parented to the live GUI owner and scheduled for GUI deferred deletion; existing owner gates retain active work. Applying the QThread helper to a pool thread would be incorrect. |
| AutoOpWorker (`ui/camera_widgets.py`) | QRunnable with camera/panel references and GUI callback delivery; no separate QObject execution worker to retire. Camera auto-operation shutdown gates are still required. No physical auto-operation was run. |
| `_CameraDiscoveryTask` / `_DiscoverySignals` (`ui/discovery_dialog.py`) | **Separate noncompliance/follow-up:** parentless GUI-affine signal QObject can lose its last wrapper reference when a pool task is released; dialog close only sets cancellation and accepts close. Migrate this separately with GUI ownership, terminal callback tracking, refresh/repeated-close admission guards, and destruction-after-completion tests. Simply parenting signals or adding `finished → deleteLater` does not prove producer retirement; do not bundle an unqualified camera-discovery shutdown redesign into SHG. No discovery is invoked by the isolated SHG scenario. |
| SimulatedCamera frame producer (`hardware/simulated_camera.py`) | Python thread executes a method on a GUI-owned camera, not a QObject moved to QThread. Stop/join producer before consumer cleanup; per-frame array is retained/copied. Existing bounded join logs failure if a producer remains alive; this is a residual guarantee to audit separately, not a reason to change worker retirement. |
| MATLAB deferred startup/disposal (`logic/matlab_deferred_start.py`, `logic/matlab_engine_manager.py`) | Non-daemon Python threads carry startup/disposal jobs, not moved Qt workers. Existing manager/owner boundaries apply; real Engine lifetime cannot be qualified here. |
| Diagnostic writer/lock probes and test-only Python threads | No moved QObject execution owner; joined/gated independently. Tests directly calling ScanWorker methods retain the worker on the calling test scope and do not transfer native QObject ownership to the Python thread. |

## Regression scope

SHG tests must observe real shared-helper behavior: child affinity transfer,
timer stop, pending MetaCall removal, native worker/child/thread destruction on
the GUI thread, success/error/cancellation, repeated deferred close and dispatch
setup failure before affinity transfer. Existing SHG tests preserve stale PM
callback rejection, PM handoff cancellation, quarantine, release acknowledgments,
simulation-only admission and lazy-view shutdown repair coverage.

Convention compliance and these deterministic invariants do **not** establish
the initiating allocation/write responsible for historical `0xC0000374`.
