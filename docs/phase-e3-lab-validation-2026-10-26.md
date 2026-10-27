# Phase E3: FIG retirement and MATLAB Engine ownership

Base: validated E2 `138d22c7ab3ba40751fa84a7ced2f3f47487b00a`.
Branch: `fix/phase-e3-matlab-lifecycle`, in `.phase-e-worktree`.
Status: hardware-independent validation passed; native MATLAB validation pending.

## Ownership and shutdown dependencies

| Boundary | Owner before | Owner afterwards | Completion evidence |
| --- | --- | --- | --- |
| Startup requested | Manager owns startup future | Manager polls future on Qt | `done()` followed by `result()` yields Engine or startup failure |
| FIG starts | Manager owns ready Engine | Manager and FIG worker share it; GUI records a lease for that QThread | Logical save result is insufficient to release the lease |
| FIG returns | Worker closes its figure in finally | Existing retirement helper joins native thread and returns affinity to GUI | Queued FIG thread-retirement handler clears wrappers and releases lease |
| First close request | MainWindow may still own CT400/other active work | PlotWidget immediately latches shutdown and suppresses new exports/completion dialogs | Engine shutdown waits for all native FIG leases, independently of CT400 cancellation |
| Ready Engine shutdown | Manager owns Engine, no leases remain | `_EngineDisposal` owns Engine in a tracked non-daemon Python thread | Native quit returns, reference is released, thread ends, Qt poll joins with timeout 0 |
| Pending startup shutdown | Manager owns future | Disposal job owns cancel/result and any late Engine | Accepted cancel releases future on disposer; rejected cancel obtains and quits late Engine |
| Successful disposal | Disposal job | No required native owner remains | Manager sets `SHUT_DOWN`, emits `shutdown_complete`; PlotWidget permits final close |
| Disposal failure | Disposal job | Manager retains failed job/Engine | `SHUTDOWN_FAILED` and error signal; final close remains pending |

Disposal threads never call QObjects or emit Qt signals. GUI timers consume
completion after the Python thread ends. This preserves the Qt worker-retirement
helper for FIG QThreads and avoids native QObject destruction in background
threads. Engine-unhealthy retries wait for old Engine retirement before starting
a replacement. Manager shutdown and repeated panel cleanup are idempotent.

The combined regression overlaps a blocked fake CT400 read, streaming
SimulatedCamera and blocked fake `savefig`. It exposed an actual ordering bug:
waiting for monitor ownership could postpone PlotWidget's close latch, allowing
a FIG success dialog to block shutdown. The close latch now occurs on the first
MainWindow close request, while final resource cleanup still respects all owners.

## Installed API inspection and limits

Read-only source inspection used the existing lab environment's MATLAB Engine
24.2.2 files `matlab/engine/futureresult.py`, `matlabfuture.py`, and
`matlabengine.py`, without importing MATLAB or launching it. The startup future
delegates to native cancel/get/destroy operations. Accepted cancellation sets
the binding's cancelled flag; result retrieval after rejection can wait for
startup. Future destruction also calls the native API, so the disposal job
releases its future reference on its own thread. Engine quit delegates to the
binding's native close routine.

Current [MathWorks startup/stop documentation](https://www.mathworks.com/help/matlab/matlab_external/start-the-matlab-engine-for-python.html)
and [future result documentation](https://www.mathworks.com/help/matlab/apiref/matlab.engine.futureresult.result.html)
describe API behavior; the online pages describe a newer release and do not
establish all R2024b cancellation/process-lifetime guarantees.

`SHUT_DOWN` confirms this application's API cleanup protocol, not independent
observation that a MATLAB OS process disappeared. Accepted cancellation is not
a PID-exit observation. No fake can prove vendor cancellation effectiveness,
license release, FIG reopening compatibility or native process absence.

A permanently blocked cancel, result, quit or FIG native operation keeps close
pending. Non-daemon cleanup prevents Python from silently abandoning required
work; it deliberately cannot guarantee bounded Python exit for an uninterruptible
native call. GUI responsiveness also depends on the native API releasing the
GIL. Bounded exit despite such hangs requires a dedicated supervisor process
owning Engine startup, export commands and cleanup from the beginning, with a
supported command protocol and verified process identities. A FutureResult or
Engine must not be assumed transferable to a late-created helper. That larger
architecture is not part of E3.

## Regression and process evidence

- Before correction: ready-state quit executed on the GUI Python thread,
  deterministic regression failed (1 failed, exit 1).
- Manager/native-worker focused run: 20 passed, exit 0. Initial old tests assumed
  daemon ownership and synchronous completion; their fakes now have bounded,
  asserting waits. A failed old assertion stranded a fake non-daemon future;
  only that exact, identified pytest process tree was terminated. No MATLAB or
  instrument process was targeted.
- Expanded FIG, manager, global-status and save-worker tests: 43 passed,
  3 existing deprecation warnings, exit 0.
- Startup subprocess probes: 2 passed, exit 0. One proves Python cannot exit
  while required fake cancellation is blocked, then exits after parent release
  and confirmed late Engine quit. Each child has bounded observation and cleanup
  limited to its own process tree.
- GUI startup/quit/export tests and their fresh interpreter: 10 passed,
  3 warnings, exit 0. MainWindow retains owners until completion; repeated close
  and late ready/failure signals cannot start new MATLAB operations.
- MATLAB, scan-file export and deferred initialization: 41 passed,
  19 warnings, exit 0 after the first-close latch correction.
- Pyright now explicitly includes the changed manager in its configured scope.
  Ruff check, Ruff format check and Pyright all pass after final source/test edits.

Campaign 1 passed stress (10 cycles, both exits 0), then failed core on a
native teardown test which invoked pytest-qt deletion before accepted close
(1,079 passed / 1 failed / 1 skipped; both exits 1). The regression was a late
close callback on an already-deleted MainWindow. The test now waits for accepted
close before exercising pytest-qt deletion, and queued close retries have explicit
QObject contexts so deletion cancels them. Its affected 53-test run passed with
90 existing deprecation warnings and exit 0. Process regressions across E1?E3
also now terminate only their own subprocess tree on timeout; all three fresh
process tests passed (exit 0).

Final campaign: `tools.qt_reliability_campaign --output output/e3-campaign-2
--core-runs 1 --camera-runs 1 --plot-runs 2 --stress-cycles 10`, using the locked,
driver-free Python 3.12.8 / Qt 6.12.0 environment and native Windows platform:

| Process | Result | Warnings | Child / supervisor exits |
| --- | --- | --- | --- |
| Stress | 10 cycles passed | 0 | 0 / 0 |
| Core | 1,080 passed, 1 opt-in stress skipped | 197 | 0 / 0 |
| Camera | 215 passed | 24 | 0 / 0 |
| Plot 1 | 318 passed | 22 | 0 / 0 |
| Plot 2 | 318 passed | 22 | 0 / 0 |

Exact collection: 1,614 items = core 1,081 + camera 215 + plot 318.
Campaign complete=true, collection exit 0, campaign exit 0. Accepted process
stderr contains no Qt thread-destruction/mutex or interpreter-finalization
errors. Warnings are existing NumPy/pyqtgraph shape-assignment deprecations.
Failed evidence is retained, never relabeled as a successful retry.

## Final cross-phase audit and publication

The final suite contains all E1 blocked-read/selected-input/recording regressions,
E2 blocked-conversion/producer-order/TaskRunner regressions, and E3 leases,
startup/quit/failure/process tests. The combined test keeps CT400 monitoring,
simulated camera frames and a FIG native call active together during close.
Final close, native handle release and camera system release follow retirement;
no targeted cleanup path uses forced termination or an unbounded GUI wait.
The original lab config hashes match the initial inventory, and its branch and
operational environment were preserved.

`main` advanced during validation from `502bb2b` to
`63c0c4b4bdaf1a2c931e63dce13e20f88da5c9f6` (PR #265). A fresh fetch showed
only README/documentation/navigation changes, with no Phase E source overlap.
The stack retains its original verified source baseline; no main merge or
force-push was performed.

Git HTTPS authentication is unavailable, but the authenticated GitHub connector
can publish Git blobs/trees/commits and draft PRs. Every uploaded blob SHA and
complete tree SHA was compared with the validated local commit. Connector-created
commit metadata differs, so local and remote SHAs are explicitly distinguished:

| Stage | Local validated commit | Remote commit | Exact tree | Draft PR / base |
| --- | --- | --- | --- | --- |
| E1 | `32d30ae1e4e13c7eac6e6670577c031a2e9f1aeb` | `87791a2bfdce30e630b35986930854a97ad389a0` | `f63426e0825571ae26c246009b9f50e484738d88` | [#266](https://github.com/simoneferraresi/IOPanel/pull/266) / main |
| E2 | `138d22c7ab3ba40751fa84a7ced2f3f47487b00a` | `04cf3f64dbaf4f46402fc3c0fa92cb2d29e5218c` | `d8285f7168c348e7379452c9c0d693abdc5cd5a8` | [#267](https://github.com/simoneferraresi/IOPanel/pull/267) / E1 |

E3's final local/remote SHA and tree comparison are recorded in its draft PR and
mission report after this evidence file is committed. No PR was merged or
marked ready. No credentials were copied into Git configuration.

## Native acceptance (separate explicit operator approval required)

No real MATLAB run has occurred. Before approval, review the final source SHA,
the synthetic test evidence and this procedure. Use a separate locked native
environment in the worktree with Engine 24.2.2 and MATLAB R2024b Update 4;
preserve the operational `.venv` and lab configuration. The approved runner
must construct only a PlotWidget/MainWindow with hardware entry points
suppressed, synthetic read-only wavelength/trace arrays and isolated QSettings.
Do not launch the operational lab configuration.

For each case, record Python PID, source SHA, package/MATLAB versions, timestamps,
manager states, FIG QThread retirement, cancellation result, quit entry/return,
accepted window close, event-loop return and final Python exit code. Inventory
process PID, parent PID, executable path, owner and creation time before start,
through shutdown and after Python exit. Attribute a MATLAB process only through
the test's launch/ancestry plus creation time; preserve unrelated baseline
sessions. An API return or machine-wide process count is insufficient evidence.

1. **Before startup:** construct the isolated window, close before prewarm,
   require no Engine startup and accepted close.
2. **Normal FIG:** explicitly prewarm, wait READY, export synthetic two-detector
   traces through the production MatlabSaveWorker. Reopen the resulting FIG in
   the same test-owned Engine using a separately leased/background operation;
   inspect trace values, labels, detector colors and wavelength extent. Close
   the reopened figure before releasing its lease. Close IOPanel after export;
   require worker retirement before quit, API completion and owned PID absence.
3. **Close during startup:** request background startup and close while STARTING.
   Require responsive pending-close status. Record cancellation return and
   disposal completion. If startup is already READY, label that attempt as the
   ready case instead of claiming the pending-startup case was exercised.
4. **Late startup after rejected cancellation:** use an operator-reviewed future
   wrapper that delegates to real `result()` while deliberately returning False
   from cancel, with no second real startup. Close, allow the test-owned startup
   to resolve, require exactly one quit attempt and observe the attributed PID.
   This tests ownership after rejection; separately record actual native
   cancellation behavior without the wrapper.
5. **Close during FIG:** use a reviewed wrapper around the same test-owned Engine
   to signal entry and insert a finite MATLAB `pause` before the delegated
   savefig. Close while that operation is active. Require a live GUI heartbeat,
   no concurrent quit, successful native return/figure close, thread retirement,
   then Engine disposal. Reopen/inspect the FIG in another explicitly approved
   test-owned session if needed after its exporting Engine has exited.
6. **Process observation/cleanup:** give each case an independent supervisor
   observation deadline. If native work remains blocked, report pending/failed
   acceptance and retain the owner. Stop automated work and let the operator
   decide how to recover the specifically attributed test session. Never issue
   image-name-wide termination or kill a baseline MATLAB session. Preserve logs
   and outputs; do not publicly upload process dumps.

The historical `normal_exit_child` diagnostic explicitly expects a daemon and
is not an E3 acceptance runner. Existing interrupted-startup diagnostics may
help inventory evidence but do not test the new GUI/FIG lease gate or independently
prove API cleanup completion. No native acceptance command should be run merely
because this procedure exists; the operator must first approve the exact runner
and cases. Frozen package/OpenGL and physical instrument qualification remain
not performed under this mission.
