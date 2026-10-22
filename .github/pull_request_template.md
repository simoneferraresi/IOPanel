## Summary / scope

- Change:
- Motivation / linked issue:
- Out-of-scope or deferred behavior:

## Validation (exact PR head)

- Tested SHA:
- Windows / Python / PySide6 / Qt / `QT_QPA_PLATFORM`:
- Driver-free environment (`--no-default-groups`), no physical devices opened:
- Ruff / formatting / Pyright:
- Exact test collection parity and full core/camera/plot CI:
- Supervisor **and** child exits, timeouts, skips:
- Windows packaging/smoke, if applicable:

## Qt/native lifecycle review (complete when relevant)

See [Qt test lifecycle standards](../docs/qt-test-lifecycle-standards.md).

- [ ] New QObjects, QThreads, widget graphs, async callbacks and timers have explicit owner, thread affinity and destruction boundaries, or this PR introduces none
- [ ] Test windows use the session `qapp`/`qtbot`, registered widgets and accepted-close/deferred-delete cleanup; no leaked native roots or workers at process exit
- [ ] Startup, cancellation, error and late-shutdown paths preserve operation ownership and producer-before-consumer cleanup
- [ ] Tests use bounded synchronization rather than timing-only assumptions, and preserve assertions/coverage
- [ ] For Qt ownership/fixture/GL changes: repeated native Windows process gate and actual exit codes documented, or an explicit reason it is not applicable
- [ ] Real OpenGL rendering (if affected) distinguished from capability skips/UNAVAILABLE, and physical hardware qualification is not inferred from simulation

## Risks and remaining limitations

- Potential impact / rollback approach:
- Unresolved issues, skipped/optional checks and deferred laboratory validation:
- Evidence links (sanitized; never upload process memory dumps):
