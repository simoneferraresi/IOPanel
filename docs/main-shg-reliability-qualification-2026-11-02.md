# SHG worker retirement: independent main qualification — 2026-11-02

## Provenance and scope

Production base fetched from origin/main: `659c0d876ca4db8861c5cc114cc4af19d0a26ba3`.
Tested code commit: `b3d76b1fd660f40d8bdcc526fa0d00c09ddc075f`; its sole parent is that production base.
Source cherry-picked with `-x`: `089f0cc44fbb75803dc2954a7f70481ce82234c1`. Stable patch IDs match the source
exactly; no adaptation was necessary. The source commit's unmerged ancestors
were not imported. Neither startup optimization branch, the other reliability
fix, nor lazy-view shutdown repair `1cd12c1` is an ancestor of this fix.

SHG now registers the mandatory shared retirement helper before queued owner completion and QThread deletion. Unstarted workers stay GUI-owned until dispatch wiring succeeds. Five regression cases cover success, cancellation, repeated deferred shutdown, scan failure and pre-transfer dispatch failure. The source commit's test-only MATLAB retirement migration, camera widget registration and historical worker audit are retained.

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
retained in local manifests under `output/ri1102/shg-*`.

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
modified. Red: **4 failed, 35 deselected; workers/children destroyed in native background threads**, child/outer exit **1/1**.
The failure is a deterministic invariant assertion, not a claimed reproduction
of October's heap exception. Focused branch result: **72 passed, 9 warnings**, child/outer
exit **0/0**, no timeout.

Ruff check and format check passed; Pyright exited 0 with 0 errors/warnings/infos.
The existing Pyright `.venv` configuration emits a missing local-venv path notice
in an isolated worktree despite explicit --pythonpath; retained logs record it.
Tests use session qapp/qtbot, registered native widgets, accepted shutdown and
native-validity-aware teardown; no new application instance or arbitrary sleep.

| Independent native entry | Pytest outcome | Child / supervisor | Timeout |
| --- | --- | --- | --- |
| stress 1 | 1 passed in 12.24s | 0 / 0 | false |
| core 1 | 1150 passed, 1 skipped, 200 warnings in 183.12s (0:03:03) | 0 / 0 | false |
| camera 1 | 228 passed, 24 warnings in 50.17s | 0 / 0 | false |
| plot 1 | 336 passed, 22 warnings in 44.33s | 0 / 0 | false |
| camera 2 | 228 passed, 24 warnings in 49.95s | 0 / 0 | false |
| camera 3 | 228 passed, 24 warnings in 48.82s | 0 / 0 | false |

Collection: `Exact pytest collection preserved: 1715 items; per process: {'core': 1151, 'camera': 228, 'plot': 336}`. Collection, campaign and outer supervisor all exited
**0**. The core skip is the existing opt-in stress case, separately exercised in
the 20-cycle process. Existing pyqtgraph/NumPy deprecation warnings remain visible;
no skips, xfails, retries or warning suppression were added. No failures occurred
in this completed branch campaign. Original investigation failures/dumps remain
local and unchanged; no raw process-memory dump is published.

## Packaged scope and residual risk

The SHG runtime change received a fresh locked PyInstaller one-folder build
and frozen qualification; retained local manifests are under `shg-build` and
`shg-frozen`. Only the simulation sidecar was copied into the package. The lab
configuration was never used. Frozen cases and actual native exits:

| Frozen case | Actual exit | Result |
| --- | ---: | --- |
| simulation-offscreen | 0 | expected result verified |
| simulation-windows | 0 | expected result verified |
| reject-smoke-test | 2 | expected result verified |
| reject-smoke-opengl-test | 2 | expected result verified |
| help | 0 | expected result verified |
| version | 0 | expected result verified |
| opengl-auto | 0 | rendered |

Executable SHA-256: `b269c6bb23d81ca7d582f8c411b5893b188f89beecbfacc43d4c7dc3a8d9138c`.
PyInstaller build, build supervisor and frozen qualification supervisor all
exited **0**, with no timeouts. The build retains optional-module warnings for
Jupyter (`jupyter_rfb`), `tzdata`, `scipy.special._cdflib`, macOS-only ctypes
framework imports and legacy OpenGL helper DLLs requiring MSVCR90. No dependency
was added to silence them; exercised simulation and actual OpenGL rendering pass,
while unused optional integrations are not qualified by these results.
Frozen smoke verifies native/offscreen simulated startup, resources, real simulated
frames and accepted shutdown; it does not start a frozen SHG capture. The real Qt
source regressions exercise that worker. Explicit desktop OpenGL, if listed,
is a separate process-only comparison; it does not qualify unavailable automatic
selection or change backend policy.

The inherited worker audit records the historical investigation base; it does
not import or qualify that unmerged startup code. Camera-discovery signal ownership
and physical SDK/MATLAB shutdown remain separate follow-ups. No claim is made that
this convention improvement fixes the original heap corruption. The simulation
diagnostic tracing issue remains on this branch's base and is handled independently.

Recommendation: merge the smaller tracing diagnostic correction first once its
required CI/review pass, then review/requalify the SHG runtime change against the
resulting actual production main. No PR is marked ready or merged automatically.
