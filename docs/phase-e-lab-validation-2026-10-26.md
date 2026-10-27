# Phase E laboratory implementation and validation

## Baseline and isolation

Remote `main` fetched on 26 October 2026 is
`502bb2bdd616c19c2251355958c4bfeeef2160e4`. The original checkout is
`C:/Users/bellanca/Desktop/IOPanel`, branch `feat/edfa-readonly-gui-m3`, HEAD
`3ded509e6c3657865a915f37c6b1f4eeddaa0a95`. It has a modified `config.ini`
and untracked `config.lab.ini`; neither was changed. There were no stashes or
commits absent from all fetched remotes. Its divergence from main was 19 local
and 43 remote commits. Existing stale worktree registrations were preserved.

E1 uses `.phase-e-worktree`, branch `fix/phase-e1-ct400-shutdown`. Git metadata
writes required sandbox escalation. Git fetch succeeded; `gh auth status`
reports no logged-in hosts. Publishing remains subject to available Git or
connector authentication.

Python is 3.12.8 x64. System uv is 0.12.21; a separate workspace-local uv
0.13.4 was downloaded to match the repository pin. Locked synchronization with
`--no-default-groups --extra test --extra lint --extra typecheck` succeeded in
the worktree, without changing the operational `.venv`. This environment has
PySide6/Qt 6.12.0, VmbPy and MATLAB Engine excluded. Read-only metadata in the
original lab environment reports VmbPy 1.2.2 and MATLAB Engine 24.2.2. No vendor
imports, instrument operations, or real MATLAB startups were performed.

## Shutdown dependency map

| Owner | Cancellation request | Work that can remain | Completion boundary | Resource release / indefinite block |
| --- | --- | --- | --- | --- |
| CT400 monitoring | GUI stops timer and sets mutex-protected worker flag; queues acknowledgment | Synchronous `get_all_powers()` using the current CT400 | Queued acknowledgment after all previous reads; then selected-input disable and exactly-once ownership release | Connection/disconnection, scan, alignment and replacement use the existing exclusive operation state. A blocked read retains ownership indefinitely. |
| CT400 worker teardown | Permanent panel latch rejects restart; request normal stop first | Read and stop acknowledgment | Retirement helper moves affinity, removes pending MetaCalls, and joins; panel emits `shutdown_complete` afterwards | MainWindow/panel close defers until retirement. Only then may the CT400 handle and owning widgets be released. |
| Monitor recording | Stop finalizes recording before read acknowledgment | Already captured samples | Existing recording completion/discard signals | Late data is rejected when `monitoring` is false. |
| Camera producer/converter (E2 investigation) | Quiesce panel; close producers before converter stop | NumPy/OpenCV conversion | Must use native converter retirement, not a timeout | Existing teardown can drop worker after bounded wait; correction pending. |
| TaskRunner (E2 investigation) | Existing `stop()` calls quit | Arbitrary worker operation | Native QThread retirement | Existing unbounded wait can block GUI; correction pending. |
| MATLAB FIG (E3 investigation) | Prevent new exports; retain active export | Synchronous Engine calls and `savefig` | FIG native thread retirement | Engine quit must follow export retirement; current forced termination requires correction. |
| MATLAB startup/Engine (E3 investigation) | Stop prewarm/poll, request shutdown | Future cancellation/result; Engine quit | Explicit confirmed cleanup needed | Existing daemon disposal does not guarantee process cleanup. Native calls may block indefinitely. |

No Qt worker retirement helper was replaced. A logical worker result does not
prove native destruction. The design does not make a non-cancellable native
call cancellable or guarantee bounded exit when a driver never returns.

## E1 implementation

The original deterministic blocked-read regression failed at
`assert not panel.power_fetch_worker.is_worker_running()` (1 failed, process
exit 1). Stop was queued behind the native read, allowing preceding queued
fetches to start another native read before stop became visible.

`PowerFetchWorker.request_stop()` now only sets synchronized Python state and
is explicitly safe to call across threads. `acknowledge_stop()` runs on the
worker queue after previous reads. Ordinary stop retains the thread for restart
and disables the selected input before releasing monitoring ownership.
Restart is rejected until acknowledgment. Permanent cleanup has a separate,
idempotent latch and emits completion after the existing retirement helper.
MainWindow snapshots UI preferences and initiates panel cleanup once, then
defers device release and final close until monitor retirement, including an
idle monitor thread created at panel construction.

Existing tests with synchronous close expectations now wait on the deliberate
asynchronous completion boundary. Tests still assert cleanup ordering, selected
laser behavior, and resource release. No shutdown gate is bypassed.

## E1 validation evidence

Commands use `.venv/Scripts/python.exe` in the isolated worktree; this is the
locked environment created by pinned uv. Direct pytest runs need a fresh
workspace `--basetemp` because the sandbox cannot access the normal user temp
root. Native campaigns run with escalation because CIM process identity queries
are denied in the sandbox. They use explicit pytest temp paths and disable
pytest's cache provider to avoid cross-token cache-directory ownership errors.

- Monitor focused tests: 11 passed, process exit 0 before expanded coverage.
- Deferred initialization, concurrent simulated acquisition and piezo ownership:
  67 passed, 84 NumPy/pyqtgraph deprecation warnings, process exit 0.
- Expanded `test_ct400_monitor_shutdown.py`: 7 passed, 3 existing deprecation
  warnings, including a bounded fresh interpreter running six lifecycle cases.
- Ruff check, format check and Pyright passed after implementation; final rerun
  completed after the final source/test edits (0 errors / warnings from Pyright).
- Exact collection gate: 1,598 items, disjoint core 1,069 / camera 211 / plot 318.
- Campaign 1 failed in the supervisor's sandbox-denied CIM query; its stress
  child printed 1 passed, but no observed child exit means it is not acceptance.
- Campaign 2 failed test setup (temp-directory ownership mismatch); child and
  supervisor exit 1. Failed evidence is retained under `output/e1-campaign-*`.
- Campaign 3 passed stress (10 cycles, both exits 0), then failed core:
  1,063 passed / 5 failed / 1 skipped, child and supervisor exit 1. The failures
  exposed synchronous close assumptions in one retry test, the startup probe,
  and smoke error handling. Smoke's prematurely exited event loop produced
  native child exit 3221226505; this was counted as a failure.
- Startup benchmark now requests window close before Qt quit. MainWindow emits
  `shutdown_complete` only after cleanup and accepted close; smoke error exits
  wait for that signal and preserve their error code. Targeted startup success,
  missing-milestone and smoke image-save-failure subprocess cases pass.
- Campaign 4 passed stress (10 cycles) and core (1,068 passed / 1 opt-in stress
  skipped / 194 warnings), every observed exit 0. Camera failed 9 legacy
  expectations (202 passed, child/supervisor exit 1): immediate close and access
  to already-deleted stopped wrappers. The corrected 31 focused camera tests
  passed with 18 deprecation warnings and exit 0. No assertion about producer
  ordering or lifetime was removed.
- Campaign 5: core passed again (1,068 / 1 skipped / 194 warnings), camera
  passed twice (211 / 21 warnings each); plot passed twice (318 / 22 warnings
  each). All five child/supervisor exit pairs were 0, campaign exit 0, exact
  collection exit 0 and campaign complete=true. Together with campaign 4 this
  gives two passing full core processes on the final production implementation.
  Warnings are existing NumPy/pyqtgraph shape-assignment deprecations.
- Git push dry run with terminal prompting disabled fails with
  `could not read Username for 'https://github.com': terminal prompts disabled`.
  Connected GitHub app identity succeeds, but it has no local Git-object push
  operation. The validated commit and draft PR body remain local; no credentials
  were copied or authentication restrictions bypassed.

No physical CT400, camera or MATLAB acceptance has been performed. Source smoke
error handling was tested; no frozen executable was built and no OpenGL
qualification was run for this change. E1 hardware-independent gates passed;
E2 may proceed from its validated commit. Draft PR text is retained in
`output/e1-pr-body.md`.
