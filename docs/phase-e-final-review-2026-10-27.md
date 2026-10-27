# Phase E final independent review — 27 October 2026

## Review target

- Repository: `simoneferraresi/IOPanel`.
- Reviewed Phase E source candidate: `4de5211351caa46fd3cfa0079866db35858c8348`.
- Parent: local E3 commit `f4edfa993e8e085164d95d90a8655e12d8fcd7b2`.
- E3 remote tree: `c24c26100f928f62970b737bd916e25fea52114f`; local E3 and remote E3 trees match.
- Current origin/main observed during review: `63c0c4b4bdaf1a2c931e63dce13e20f88da5c9f6`.
- Original checkout `feat/edfa-readonly-gui-m3` was left untouched, including its modified `config.ini` and untracked `config.lab.ini`.

The review compared Phase E behavior with the pre-Phase-E lifecycle implementation, read the repository's lifecycle/worker guidance and phase-validation notes, traced the shared worker-retirement helper and all changed ownership call sites, inspected regression tests and stacked PR patches, and checked the current main delta plus available CI evidence. A fresh independent reviewer examined the cross-phase paths before source changes; its finding was reproduced, fixed, and re-reviewed against the final source candidate.

## Finding and resolution

| ID | Severity | Component | Finding | Evidence | Recommended action / merge status |
| --- | --- | --- | --- | --- | --- |
| E-REV-01 | P1 | `logic/matlab_engine_manager.py::_poll_startup()` | Completed startup FutureResult could be finally released by the GUI polling callback, entering the MATLAB native API from the GUI thread. | Installed MATLAB Engine 24.2.2 `FutureResult.__del__()` drops `MatlabFuture`; its finalizer invokes the vendor native API. New deterministic tests verify future destruction on a non-GUI thread for both success/error and verify Engine quit waits for a blocked fake destructor. | Fixed in `4de5211`; this defect no longer blocks the local tested tree. The follow-up still needs remote review and CI. |

### E-REV-01 — fixed

`MatlabEngineManager._poll_startup()` consumed a completed startup `FutureResult`, but ordinary success and error paths could release its final reference in the Qt polling callback. Inspection of the installed MATLAB Engine 24.2.2 binding showed that `FutureResult.__del__()` releases a native `MatlabFuture`, whose finalizer calls the vendor native API. This violated the ownership boundary already documented by E3 and could stall or destabilize GUI-thread completion.

Commit `4de5211` transfers the final future reference to a tracked non-daemon disposal worker for both successful and failed startup. A handoff event prevents the worker from releasing the future until the GUI callback has dropped its local reference. Shutdown waits for this disposal before quitting the Engine; a ready Engine remains usable while its startup future is being released. Failure traceback references are cleared before logging so traceback frames cannot retain the future. Regression tests check finalizer thread identity on success and failure and verify that Engine quit waits for future finalization.

The focused manager and cooperative-shutdown run passed **32 tests** with 3 existing PySide/NumPy deprecation warnings. Ruff and Pyright passed. The same focused run also passed on the merge-rehearsal trees.

## Review conclusion

No additional correctness issue was identified in the reviewed lifecycle paths at this source SHA. E1's CT400 monitor/laser/recording ownership, E2's camera producer/converter retirement and TaskRunner cooperative-stop callers, E3's FIG lease/worker retirement, manager state transitions/disposal ownership, and the shared QObject-context worker-retirement helper were reviewed. Existing lifecycle regressions cover blocked operations, close ordering, repeated shutdown, late signals, and worker retirement; the newly added manager tests cover the identified finalizer gap.

This is a code review conclusion, not evidence of native MATLAB process behavior or physical instrument acceptance. The final local qualification commit has not been published because the GitHub connector's automatic review rejected uploading the two source blobs; see the qualification and merge-readiness reports for the exact block and next step.

## Comparison with remote phases

| Phase | Remote commit | Tree | Draft PR | Base |
| --- | --- | --- | --- | --- |
| E1 | `87791a2bfdce30e630b35986930854a97ad389a0` | `f63426e0825571ae26c246009b9f50e484738d88` | [#266](https://github.com/simoneferraresi/IOPanel/pull/266) | `main` |
| E2 | `04cf3f64dbaf4f46402fc3c0fa92cb2d29e5218c` | `d8285f7168c348e7379452c9c0d693abdc5cd5a8` | [#267](https://github.com/simoneferraresi/IOPanel/pull/267) | E1 |
| E3 | `fae56c9d1fca574c089e0dacccf9d23e0195a6bb` | `c24c26100f928f62970b737bd916e25fea52114f` | [#268](https://github.com/simoneferraresi/IOPanel/pull/268) | E2 |

The E1, E2, and E3 source trees matched their local validated counterparts. The main update observed during qualification was documentation-only and did not overlap Phase E source. Historical GitHub Actions runs attached to the three PR heads passed their reported jobs. No new remote CI exists for the local follow-up because the source publication was rejected. No PR review or inline thread was present in the inspected snapshot.
