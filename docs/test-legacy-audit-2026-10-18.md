# Test and legacy-code audit — 2026-10-18

## Scope and baseline

- Baseline `main`: `8c950a0fd30fb28dc1e88d115932d5717775a340` (2026-10-16).
- Repository inventory: 85 `tests/test_*.py` modules. Each was reviewed for test definitions, skips, explicit legacy/compatibility markers, and obvious placeholder assertions. Selected production implementations and their callers were inspected directly.
- Windows hardware-independent CI run [38911370877](https://github.com/simoneferraresi/IOPanel/actions/runs/38911370877): **1,185 passed, 3 skipped, 195 warnings**; Ruff check, Ruff format check and Pyright passed. The companion Windows packaging run [38911371260](https://github.com/simoneferraresi/IOPanel/actions/runs/38911371260) passed.
- The baseline is not a coverage report, and passing tests cannot establish that every test exercises a currently-used feature. No physical hardware, native MATLAB, or SDK-specific compatibility check was performed in this audit.

## Classification and evidence

| Candidate | Repository evidence | Decision |
| --- | --- | --- |
| `cameras_info.py` | Standalone top-level script calls `VmbSystem.get_instance().get_all_cameras()` at import time. An exact repository code search found no references to `cameras_info.py`. The maintained GUI discovery route is `ui/discovery_dialog.py`, backed by `hardware/camera_discovery.py`; there are also explicit camera validation tools. | **Potential orphan, not proven obsolete. Preserve.** A manually invoked script may still serve lab workflows, including raw listing without selecting an ID. Confirm actual operator usage and CLI replacement before deletion. |
| `find_piezos.py` | Standalone hardware-specific discovery utility; no in-repository import relationship was found in a name search. It directly calls the vendor DLL to enumerate piezo identifiers. | **Unreferenced by application, not proven obsolete. Preserve.** It could remain a manual setup/diagnostic command; do not delete based only on static call graphs. |
| `app_settings.py` legacy tab-index/log-level compatibility | `active_tab()` explicitly translates the old `MainWindow/active_tab` index into a stable tab identity. `tests/test_app_settings.py` and `tests/test_mainwindow_navigation.py` exercise migration from old settings. | **Live backward compatibility. Preserve implementation and tests.** |
| `hardware/pm5020.py` importable SDK fallback | When both explicit SDK paths are absent, the module supports the vendor-installed importable wrapper. `tests/test_pm5020.py::test_legacy_importable_sdk_fallback_when_both_paths_absent` verifies this behavior, and the partial-path regression rejects an unsafe implicit fallback. | **Supported compatibility behavior. Preserve implementation and tests.** |
| `tests/test_qt_stall_stress.py` skip | Deliberately gated by `IOPANEL_QT_STRESS_CONFIG`; the stress test belongs to an externally supervised opt-in workflow. | **Intentional conditional coverage. Preserve.** |
| Two `tests/test_lazy_alignment_viewer.py` skips | Real OpenGL rendering tests skip when a usable context is unavailable, rather than claiming headless rendering succeeded. | **Environment-dependent coverage. Preserve and validate separately on capable systems.** |
| `tests/test_camera_discovery.py::test_cancellation_during_poll_exits_before_next_discovery_query` | Uses a `threading.Timer(0.01, event.set)` that can fire *before* discovery reaches its first poll under an unlucky Windows scheduling delay, causing the `system.polls == 1` assertion to fail for reasons unrelated to the implementation. | **Genuine nondeterministic test, not dead coverage. Fix synchronization; retain assertions and actual cancellation.** |

## Changes made in the audit branch

The discovery-cancellation regression no longer depends on a 10 ms timer. A worker thread announces when it enters the real interruptible `Event.wait()` call. The test then cancels from the coordinating thread and verifies that: (1) the worker exits, (2) the observed exception is `CameraDiscoveryCancelled`, and (3) the camera system was queried **exactly once**. Bounded waits and a `finally` cleanup ensure that failures remain visible and test threads are released. No production or hardware-control code was changed.

## No unsupported deletions

**No test module or production script has been deleted.** No inspected legacy migration test was proven redundant, and an unreferenced standalone script may still be used manually outside repository import graphs. Deleting it without operator confirmation would violate the audit's evidence standard. Existing skips were not added or widened; no failing assertions were weakened to create green CI.

## Remaining verification and blockers

1. Confirm current laboratory use of the two standalone scripts before considering removal or moving their functionality into maintained CLI tools.
2. A dedicated coverage run (including branch coverage) plus per-feature call-site tracing is needed before declaring individual code branches dead. The existing CI runs tests but does not produce a coverage report.
3. Preserve separate native Windows/SDK and physical-hardware acceptance gates; CI mocks and simulator tests cannot substitute for them.
4. Track the existing third-party NumPy/pyqtgraph deprecation warnings rather than suppressing them without a dependency-level resolution.
5. Review the audit branch's own Windows CI results before merge; the baseline numbers above refer only to `main`.
