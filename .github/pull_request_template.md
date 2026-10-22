## Summary and scope

<!-- Describe the defect/feature, affected backends, and changed behavior. Link relevant issues. -->

## Validation evidence

- Exact base/head SHA:
- Native Windows / Python / Qt version (if relevant):
- Ruff, format, Pyright:
- Exact full-suite pytest collection vs core/camera/plot:
- Supervised child and supervisor exit codes (not only pytest assertion summary):
- Frozen smoke and OpenGL qualification, or why not applicable:
- Known skips, failures and unresolved issues:

## Qt lifecycle and hardware safety review

For PRs that touch GUI, Qt workers, plotting, test fixtures, test tools or instrumentation, complete the relevant items:

- [ ] No physical instruments were operated; **or** exact operator-authorized hardware actions and evidence are documented.
- [ ] No additional QApplication/QCoreApplication was constructed inside pytest; GUI tests reuse `qapp`/`qtbot` and isolate settings, temp files and processes.
- [ ] Native widgets and Python QObject workers have durable owners, correct thread affinity, accepted close and deferred destruction; no running worker survives teardown.
- [ ] Async startup, cancellation, errors, late results and repeated close are covered where relevant; no new work starts once shutdown begins.
- [ ] Tests retain real Qt/instrument-simulation coverage without new unjustified sleeps, xfails, skips, retries or weakened assertions.
- [ ] A successful pytest summary followed by abnormal process exit is treated as FAILURE.
- [ ] Native Windows repeated-process/packaging checks were performed for lifecycle changes, **or** missing evidence is explicitly identified.
- [ ] No raw native dumps or private hardware identifiers were committed/uploaded.
- [ ] New tests are discovered and included exactly once by the CI collection-parity gate.

See [AGENTS.md](../AGENTS.md) and [Qt test lifecycle standards](../docs/qt-test-lifecycle-guidelines.md). Do not mark conditional hardware, OpenGL or unqualified Qt 6.12 compatibility as fully validated.
