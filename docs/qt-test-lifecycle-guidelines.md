# Qt test lifecycle and native-Windows reliability standards

These standards are mandatory for new and modified IOPanel GUI tests. They complement the automatic `tests/conftest.py` lifecycle fixture and the agent rules in [AGENTS.md](../AGENTS.md). Evidence and validated design decisions: [Qt lifecycle corrections (2026-10-22)](qt-lifecycle-corrections-2026-10-22.md).

## Why these rules exist

Native Windows camera-panel stalls were traced to a real Qt connection-mutex/GIL inversion when Python QObject workers were destroyed in background threads. Separate tests leaked top-level native widget graphs until Python interpreter finalization and produced post-summary `0xC0000409`. A camera measurement tool stopped tracemalloc while native callback threads could allocate Python thread state, producing `0xC0000005`. A test that reports all assertions passed can therefore still fail at process exit. Plotting issue #216 remains independently unconfirmed as definitively fixed.

## Fixture pattern

Use `qapp` and `qtbot` for native Qt tests; register widgets, and complete worker activity before returning. Never instantiate a second QApplication in the pytest process. Process-entry-point and QApplication-finalization tests belong in **bounded, isolated subprocesses** with the inherited driver-free simulation environment, distinct logs and explicit exit-code checks.

```python
def test_dialog_closes_without_workers(qtbot):
    dialog = MyDialog(simulated_backend)
    qtbot.addWidget(dialog)
    dialog.show()
    # Exercise the real GUI behavior and wait on an observable condition.
    qtbot.waitUntil(lambda: dialog.operation_finished, timeout=5000)
    # The autouse native-lifecycle fixture confirms accepted close/destruction.
```

Avoid unregistered top-level widgets when possible. If a specialized test intentionally owns an unregistered window, explicitly release its native graph (see `tools.qt_test_lifecycle`) and assert no workers or timers remain. Calling `close()` without checking acceptance and using `deleteLater()` after a closing event loop is not sufficient. Do not directly destroy a busy window.

## Background workers and callbacks

- For Python QObject workers in QThreads, use the established `retire_worker_on_thread_finish` ownership contract from `logic/qt_worker_lifecycle.py`. Account for the worker, child timers, pending MetaCalls, QThread join, GUI affinity and deferred deletion.
- The thread object and Python worker wrapper need a durable lifetime. `finished` and `destroyed` are distinct. Verify the actual thread affinity and native `shiboken6.isValid` boundary when relevant.
- Make all shutdown paths idempotent. Once `MainWindow` begins closing, deferred initialization, recovery and stale signals cannot admit new work; test late results and cancellation.
- Prefer QObject-context QTimers so callbacks cannot access a destroyed widget. Ensure QRunnable signal QObjects belong to a live GUI owner until queued results have been consumed.
- Preserve physical camera ownership and CT400 safe shutdown; never replace hardware integration tests with mocks. For routine CI use fake/simulated backends and avoid vendor SDK imports/side effects.

## Assertions and native failure evidence

- Synchronize on real signals, state or event-loop conditions, not arbitrary sleeps. For native keyboard focus, `raise_`, `activateWindow`, and `qtbot.waitActive` before shortcut input; wait on the resulting visible state.
- Test the real failure and cleanup paths. Add a regression that fails before a lifecycle correction and passes afterward when practical. Include exception, cancellation, partial initialization and repeated close.
- For subprocesses, assert both *test outcome* and **process exit code 0**. `0xC0000409`, `0xC0000005`, heap corruption, 300-second stalls and supervisor errors are failures even when all assertions passed.
- Keep process stdout/stderr, supervisor manifest, runtime/Qt versions and exact source SHA. Never publicly upload process-memory dumps. No blanket retries or longer deadlines as substitutes for a fix.
- `QT_QPA_PLATFORM=windows` is required for principal native reliability tests. Keep explicit offscreen smoke as separate coverage. A true OpenGL render under `QT_OPENGL=desktop` must not be conflated with automatic software fallback under RDP.

## How CI enforces the contract

Every `tests/test_*.py` / `tests/*_test.py` module is eligible for collection. CI verifies **exact test node-ID equality** between the original pytest suite and disjoint core/camera/plot processes, then fails on any failing partition or nonzero native child exit. `tests/conftest.py` applies QSettings isolation, accepted window close and deferred deletion to **every test**, and checks outstanding QThreads/windows at session exit.

The `test_qt_test_policy_guards.py` tests prevent common direct Qt application creation in the pytest interpreter, including ordinary import aliases. They deliberately permit isolated subprocess programs stored as strings. More complex lifecycle correctness cannot be proven statically and must be asserted by focused regression tests and code review.

## PR checklist

For any test/Qt change:

1. Explain fixtures, native ownership, thread affinity, shutdown boundary and why the test cannot inadvertently operate hardware.
2. Run Ruff, formatting, Pyright, exact collection parity, all mandatory partitions and packaged smoke where impacted.
3. If changing QObject/QThread/QTimer/GUI/plot lifetime, run repeated independent **native Windows** processes with `python -m tools.qt_reliability_campaign --output <new-folder>`. Never overwrite failed evidence.
4. Record all process/supervisor exits, skip reasons, Qt versions, commands and exact SHAs. If hardware or real vendor SDK testing is required, mark it **not performed** until explicitly authorized.

The reliability campaign is an engineering qualification tool, not a per-PR 100-run CI mandate for unrelated documentation or isolated pure-Python changes. Choose scope proportionately; always keep standard CI mandatory.
