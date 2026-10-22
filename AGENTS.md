# Repository agent instructions — IOPanel

IOPanel is a Windows/Python 3.12 PySide6 application that controls laboratory equipment. Read [docs/qt-test-lifecycle-standards.md](docs/qt-test-lifecycle-standards.md) before adding/modifying GUI, worker, native diagnostic, or pytest code. The [2026-10-22 lifecycle investigation](docs/qt-lifecycle-corrections-2026-10-22.md) records causal evidence and explicit limits.

## Non-negotiable development constraints

- Default to the hardware-independent profile: `uv sync --locked --no-default-groups --extra test --extra lint --extra typecheck`. Never operate the CT400, cameras, piezos, laser, EDFA, PM5020 or real MATLAB Engine without a separately authorized, operator-attended hardware-validation procedure. Do not overwrite laboratory configuration, Windows registry preferences or local environments.
- Tests must reuse pytest-qt's session `qapp` and `qtbot`; never create a second in-process QApplication or leave a native widget graph for interpreter finalization. Use `qtbot.addWidget` for test-owned windows, and preserve the global native-window scope cleanup. `close()` is not proof of destruction; deferred deletion must complete after an accepted close.
- All GUI objects and graphics belong on the GUI thread. QObject workers must follow `logic/qt_worker_lifecycle.py`'s retirement protocol: stop callbacks and pending work in the correct affinity thread, return stopped Python worker trees to application-thread affinity, join the native thread, and arrange native destruction on that thread. Never treat `QThread.finished` as `QObject.destroyed`, assume `deleteLater` is immediate, or drop the last wrapper while its native destructor might run on a worker.
- New startup/initialization code must respect the shutdown latch: create needed UI placeholders before starting workers, reject late work/results, preserve cancellation and owner/operation gates, and shut down producers before consumers.
- Prefer signal/event-driven `qtbot.waitUntil` / `waitSignal` and bounded deadlines; explicitly establish window activation before testing native shortcuts. Do not repair flaky tests with sleeps, retries, blanket catches, relaxed assertions, new xfails/skips, or coverage reductions. Real OpenGL `UNAVAILABLE` or a capability skip does not count as rendered-GL qualification.
- Any new `tests/test_*.py` or `*_test.py` module must remain discoverable and included exactly once in the core/camera/plot partitions; camera/plot stateful tests belong in the appropriate isolated partition. The CI collection gate must pass unchanged.
- A green pytest summary followed by a nonzero native process exit is a **failure**. Use the bounded supervisor and preserve a failed/interrupted campaign's evidence; never hide failures by replacing manifests or counting clean retries as the original result. Never upload native memory dumps (potential secrets); use sanitized diagnostics.
- Preserve the locked production Qt 6.11.2 profile. Qt 6.12 is experimental and must not be rolled into routine refactors or deployed from simulation-only results. Physical SDK/MATLAB shutdown and automatic RDP software-OpenGL fallback remain separate qualification limits.

## Before proposing a merge

Run Ruff, format check, Pyright, exact collection parity, and driver-free test partitions on the actual head. Obtain passing Windows CI and packaging where applicable. For changes to Qt ownership, test fixtures, camera/plot native lifecycle, threading, or GL: also use the relevant repeated native Windows and process/simulation gates in the standards document. Include the tested commit SHA, interpreter/Qt version/platform, exit codes, skips and remaining limitations in the PR; distinguish offscreen simulations, real OpenGL rendering and physical instrument qualification.

Prefer a focused PR and explicit follow-up issue to hiding an unqualified dependency or broadening scope.
