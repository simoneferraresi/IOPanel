# Repository agent instructions — IOPanel

These instructions apply to the entire repository, including future tests and PRs.

## Non-negotiable safety and validation

- IOPanel operates lasers, cameras, motion hardware, and power meters. **Never run, open, discover, initialize, or modify physical equipment** without explicit operator authorization for that action. An ordinary GUI launch using the lab `config.ini` can open physical cameras. Prefer `packaging/config.simulation.ini` and DummyCT400; do not copy private device identifiers into tests or evidence.
- Use Python 3.12 x64 and the pinned uv version. On developer/CI machines without vendor SDKs, use `uv sync --locked --no-default-groups --extra test` (add `--extra lint --extra typecheck` for static checks). Do not modify the operational lab `.venv` or hardware configuration.
- Preserve hardware ownership, selected-input laser safe-state behavior, cancellation and worker-shutdown guarantees. Preserve the PySide6/Qt 6.12.0 baseline established by merged PR #232. Future Qt upgrades and OpenGL backend changes require independent compatibility qualification.
- Never regard passing pytest assertions followed by a nonzero native process exit (e.g. `0xC0000409`, `0xC0000005`, `0xC0000374`) as success. Do not hide stalls with retries, skips, xfails, longer timeouts, swallowed exceptions, or forced termination.

## Qt ownership and lifecycle rules

- Create and manipulate QWidgets, plots and Qt graphics on the GUI thread. All window startup, async result handling and shutdown must have explicit, testable ownership/lifecycle boundaries.
- Python-implemented QObject workers must not be destroyed unpredictably in a native background thread: consult `logic/qt_worker_lifecycle.py` and use the existing `retire_worker_on_thread_finish` pattern when adding worker QThreads. Preserve strong references through native thread completion and deferred destruction. Do not introduce `QThread.finished -> worker.deleteLater` casually for Python QObject workers.
- A worker's `finished` signal is not native `destroyed`; `deleteLater` is asynchronous; closing a widget is not necessarily accepted while operations are active. Verify Qt thread affinity, child-QObject affinity, queued callbacks, pending MetaCalls, thread joins, and accepted close before deletion.
- A closing MainWindow cannot admit new initialization/recovery/scan operations. Construct the GUI graph before launching asynchronous initialization workers; guard late results and callbacks; make cleanup idempotent.
- For memory tracing around camera/native callbacks, do not disable tracer hooks while foreign threads can still acquire Python thread state. Maintain caller-owned tracers and join producers/converters before stopping a locally owned tracer.
- Never silently change OpenGL backend policy to conceal an RDP/driver mismatch. Explicit `QT_OPENGL=desktop` successes and automatic software-fallback failures are different validation results.

## Adding or modifying tests

- **Use pytest-qt's session `qapp` and `qtbot`**, plus `tests/conftest.py`'s automatic settings and native-lifecycle fixtures. Do not construct `QApplication`, `QGuiApplication` or `QCoreApplication` in the pytest interpreter. Truly independent application lifecycle tests must launch a bounded subprocess; the automated test-policy gate enforces common direct constructions.
- Register every test-owned QWidget with `qtbot.addWidget` when practical. Clean up other native windows explicitly; do not leave QObjects, QThreads, QRunnables, active timers, deferred deletion, pending callbacks, or global state for interpreter finalization. Preserve an accepted asynchronous close; never destroy windows with active workers merely to make teardown fast.
- Prefer deterministic signals, state transitions and `qtbot.waitUntil` with justified bounds; don't introduce timing sleeps where a completion signal/invariant is available. For keyboard shortcuts involving native focus, activate the window and wait for activation before sending input.
- Tests about CT400, MATLAB or plotting alone must not bring up simulated cameras gratuitously. Retain meaningful *real Qt + simulated hardware* tests wherever production GUI/thread lifetime matters. Never weaken assertions or remove coverage just to make CI green.
- Keep QSettings, temp directories, logs, native diagnostics, and processes isolated. Memory dumps contain process memory: never upload raw dumps to public GitHub Actions artifacts.
- New test modules must be discoverable as `test_*.py` or `*_test.py`; `tools/ci_pytest_partition.py --verify` must prove exact union/no overlap. Existing camera and plot group assignments should be preserved; newly added modules default to core unless partition rules are explicitly updated.

## Required checks before proposing a merge

1. `uv run --no-sync ruff check .`, `uv run --no-sync ruff format --check .`, and `uv run --no-sync pyright` (after locked dev environment sync).
2. `uv run --no-sync python -m tools.ci_pytest_partition --verify`, then the complete hardware-independent core, camera and plot partitions in separate supervised processes.
3. For GUI/QThread/fixture/Qt runtime/plot lifecycle changes, repeat native Windows processes using `tools.qt_reliability_campaign` at a proportionate scale, with **every** process and supervisor exit code zero; run frozen smoke/OpenGL qualifications as appropriate and document limitations.
4. Compare before/after evidence and provide exact SHAs, commands, test counts, warnings, skips, native-exit results and unresolved problems. Do not operate real hardware or deploy to the lab PC without authorization.

Read `docs/qt-lifecycle-corrections-2026-10-22.md` and `docs/qt-test-lifecycle-guidelines.md` before changing Qt worker lifetimes or the test framework. The October investigation is a source of validated invariants, **not** a claim that every historical plot crash or hardware SDK path has been fixed.
