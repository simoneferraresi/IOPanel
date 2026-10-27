# Phase E2: camera conversion and TaskRunner shutdown

Base: validated E1 `32d30ae1e4e13c7eac6e6670577c031a2e9f1aeb`.
Branch: `fix/phase-e2-camera-taskrunner-shutdown`, in `.phase-e-worktree`.
Environment and safety constraints are unchanged from the E1 validation note.

## Ownership transitions

CameraPanel stops accepting new work and disconnects the producer's frame
signal. Its thread-safe converter stop clears the latest-frame mailbox;
`quit()` requests event-loop exit without waiting for a native conversion.
The panel retains the converter and QThread until the established retirement
helper has returned affinity to the GUI and joined native thread cleanup.
Only then does `conversion_shutdown_complete` permit accepted panel close.
Closed placeholders reject late camera assignment.

MainWindow prepares every panel, stops all camera producers, requests converter
retirement, then retains its camera/panel collections while any converter is
pending. It closes/deletes each panel and releases the camera system after
retirement. Repeated close does not close producers repeatedly. A blocked
native conversion keeps shutdown pending and the GUI event loop available.

TaskRunner.stop has no direct ordinary production callers in the inspected
call graph. Its unconditional wait was nevertheless unsafe for blocked native
workers. Stop now requests interruption/quit and returns immediately; the
existing application-owned runner retains its worker until retirement.
`stopped` is delivered on the GUI thread after the retirement helper. Workers
must cooperate with interruption or finish naturally, and services must request
their hardware cleanup before stop. Stop before start completes once and
prevents a late start. No force termination was added.

## Regression evidence

Before the fix, the controlled OpenCV conversion test accepted panel close
after the three-second timeout while its fake native call remained blocked;
the TaskRunner probe caught the unconditional wait. Both failed in one bounded
run (2 failed, process exit 1), and released their own fake work in finally.

After implementation:

- Focused conversion, display, view-mode, MainWindow and TaskRunner tests:
  41 passed, 3 existing NumPy/pyqtgraph warnings, exit 0.
- Expanded blocked-conversion and TaskRunner cases: 8 passed, 3 warnings,
  exit 0, including a fresh Windows interpreter. Subsequent late-assignment
  and pre-start stop tests are included in the full gate below.
- Deferred initialization, piezo ownership and camera recovery: 52 passed,
  90 existing warnings, exit 0 after converting cleanup mocks into spies that
  perform real cleanup. The new completion gate is never bypassed.
- First full camera partition: 212 passed, 1 failed, 2 teardown errors, child
  and supervisor exit 1. Failure exposed a mocked cleanup that never marked
  itself complete; the corrected spy retains the real lifecycle behavior.
- Ruff, formatting and Pyright pass after the final source/test edits.

The bounded process regression validates actual Python exit after unblocking
conversion and worker operations. It asserts return code 0 and rejects native
QThread destruction diagnostics. Native SDK/hardware access remains forbidden;
only SimulatedCamera and fake OpenCV waits were used.

## Full gate

`tools.qt_reliability_campaign --output output/e2-campaign-2 --core-runs 1
--camera-runs 2 --plot-runs 1 --stress-cycles 10` runs against the same locked
environment, with native Windows Qt and isolated pytest temp/cache settings.
Campaign 2 passed stress (10 cycles, both exits 0) and failed core on one
remaining synchronous simulated-camera test (1,069 passed / 1 failed /
1 skipped; both exits 1). Campaign 3 passed full core after its explicit
retirement wait (1,070 passed / 1 opt-in stress skipped / 194 warnings, both
exits 0), then failed camera on a diagnostic test's immediate-stop expectation
(214 passed / 1 failed / 24 warnings, both exits 1). Its correction waits on
`_conversion_shutdown_complete` and retains the actual thread-stop assertion.

Campaign 4 retains that full-core/stress evidence and repeats camera twice
plus plot once (`--core-runs 0 --camera-runs 2 --plot-runs 1 --stress-cycles 0`).
Both camera runs passed 215 tests / 24 warnings each, and plot passed
318 tests / 22 warnings. Every child/supervisor pair exited 0, campaign exit 0,
collection exit 0, complete=true. E2 hardware-independent gates passed. Exact collection: 1,604 items = core 1,071 + camera 215 +
plot 318, no loss/overlap. Failed evidence is preserved under `output/e2-*`.

Physical camera, frozen executable and OpenGL qualification were not performed.
The source smoke regression remains part of the full core gate. Git HTTPS
credentials remain unavailable; local validated commits and draft PR bodies are
the fallback deliverable. No merge is authorized.
