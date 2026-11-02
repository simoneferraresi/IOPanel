# Simulated camera allocation-tracer cleanup: independent main qualification — 2026-11-02

## Provenance and scope

Production base fetched from origin/main: `659c0d876ca4db8861c5cc114cc4af19d0a26ba3`.
Tested code commit: `005ef54008983db3e7a78a13f313be830cce48ae`; its sole parent is that production base.
Source cherry-picked with `-x`: `d8a1c5305050229ee617c69d2a2e977730128aa9`. Stable patch IDs match the source
exactly; no adaptation was necessary. The source commit's unmerged ancestors
were not imported. Neither startup optimization branch, the other reliability
fix, nor lazy-view shutdown repair `1cd12c1` is an ancestor of this fix.

The simulation display characterizer joins converter QThreads after producer shutdown and consumer close, before stopping its owned tracemalloc hooks. The real-thread regression holds a native finished callback after isRunning() is false, proves the unsafe old boundary, then verifies native completion before tracer stop. Caller-owned tracers remain untouched.

No operational dependency, lockfile, lab configuration, Qt/OpenGL policy,
hardware ownership or resource-lease protocol was changed. No physical equipment
was opened/discovered, no real CT400 scan or MATLAB Engine was run, and no merge,
deployment or change to PRs #287/#288 is authorized by this evidence. The original
`0xC0000374` and standalone SHG access violation remain unattributed.

## Environment and commands

Windows 10 x64 build 19045; Python 3.12.8 x64; uv 0.13.4; PySide6/Qt/Shiboken
6.12.0; pytest 9.1.1, pytest-qt 4.5.0, NumPy 2.5.3, pyqtgraph 0.14.0.
New driver-free environment under `output/ri1102/env`, separate from the lab
`.venv` and investigation environments. No VmbPy or MATLAB Engine distribution.
The locked sync selected test/lint/typecheck/dev extras, excluding default groups.
Native GUI children ran serially on `QT_QPA_PLATFORM=windows`, QT_OPENGL unset.
Short workspace TEMP/TMP, per-branch basetemp, per-run cache and isolated QSettings
avoid MAX_PATH/shared-state failures. Source SHA/status and expanded argv are
retained in local manifests under `output/ri1102/trace-*`.

With UV_PROJECT_ENVIRONMENT and UV_CACHE_DIR set to isolated workspace paths:

```powershell
uv sync --locked --no-default-groups --extra test --extra lint --extra typecheck --extra dev
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pyright --pythonpath $isolatedPython
& $isolatedPython -m tools.qt_reliability_campaign --output $newEvidence --core-runs 1 --camera-runs 3 --plot-runs 1 --stress-cycles 20
```

The campaign first runs `python -m tools.ci_pytest_partition --verify`, proving
exact node-ID union and no overlap, then uses separate supervised native children.
Every child has a 300-second deadline; a nonzero native exit after a green pytest
summary remains failure. This focused gate keeps shared helper/fixtures unchanged
and uses three camera processes plus 20 simulated lifecycle cycles; it is smaller
than the default full 3-core/30-camera/20-plot/100-cycle campaign.

## Before/after and checks

The branch's regression file was imported against unchanged production main using
`--noconftest -p tests.conftest --import-mode=importlib`, with its ordinary
repository fixtures and unchanged production modules. No baseline source was
modified. Red: **1 failed, 41 deselected; tracer stop precedes native QThread completion**, child/outer exit **1/1**.
The failure is a deterministic invariant assertion, not a claimed reproduction
of October's heap exception. Focused branch result: **42 passed, no warnings**, child/outer
exit **0/0**, no timeout.

Ruff check and format check passed; Pyright exited 0 with 0 errors/warnings/infos.
The existing Pyright `.venv` configuration emits a missing local-venv path notice
in an isolated worktree despite explicit --pythonpath; retained logs record it.
Tests use session qapp/qtbot, registered native widgets, accepted shutdown and
native-validity-aware teardown; no new application instance or arbitrary sleep.

| Independent native entry | Pytest outcome | Child / supervisor | Timeout |
| --- | --- | --- | --- |
| stress 1 | 1 passed in 13.25s | 0 / 0 | false |
| core 1 | 1150 passed, 1 skipped, 200 warnings in 187.94s (0:03:07) | 0 / 0 | false |
| camera 1 | 224 passed, 24 warnings in 53.32s | 0 / 0 | false |
| plot 1 | 336 passed, 22 warnings in 53.58s | 0 / 0 | false |
| camera 2 | 224 passed, 24 warnings in 47.85s | 0 / 0 | false |
| camera 3 | 224 passed, 24 warnings in 47.03s | 0 / 0 | false |

Collection: `Exact pytest collection preserved: 1711 items; per process: {'core': 1151, 'camera': 224, 'plot': 336}`. Collection, campaign and outer supervisor all exited
**0**. The core skip is the existing opt-in stress case, separately exercised in
the 20-cycle process. Existing pyqtgraph/NumPy deprecation warnings remain visible;
no skips, xfails, retries or warning suppression were added. No failures occurred
in this completed branch campaign. Original investigation failures/dumps remain
local and unchanged; no raw process-memory dump is published.

## Packaged scope and residual risk

This changes a source-only simulation diagnostic and its test. Neither changed
module is imported by the packaged application entry point; no packaged runtime,
spec, resource, backend or dependency changed. A new frozen build is not an
applicable gate for this branch. The required repository package CI still applies.

This fixes a demonstrated native-join/tracer-stop boundary supported by the
November 2 captured tracing-allocator AV and deterministic regression. It does
not prove that every tracing crash, SDK path or original heap-corruption incident
is fixed. The join does not terminate workers; a stall remains a bounded-process
failure. Native thread-start resource exhaustion and physical SDK shutdown are
not qualified. The independent SHG convention violation remains on this branch's
production base and is handled by a separate PR, not silently bundled here.

Recommendation: after required remote CI and review, this is the smaller first
merge candidate because it changes only the simulation diagnostic. Requalify the
actual combined main before accepting the separately proposed SHG runtime change.
