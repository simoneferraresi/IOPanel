# Phase D — Pyright coverage evidence (2026-10-26)

This note records the R06 baseline collected after the R08 `ScanSettings` decoupling
(commit `5cda17501b8b75e407d4224abc866db0d9a5e48a`), on Windows Python 3.12 with Pyright 1.1.414,
`typeCheckingMode = "basic"`, and the project's existing override diagnostics.

## Existing checked baseline

The 32 explicitly included files pass the mandatory CI Pyright check with
**0 errors, 0 warnings, 0 informations**.

## Temporary, per-file diagnostic probe

The diagnostic probe ran each target explicitly using
`uv run --no-sync pyright --outputjson <target>`, without enabling it in the
permanent `include` list or installing laboratory vendor SDKs.

| Target | Errors | Warnings |
| --- | ---: | ---: |
| `logic/scan_measurement.py` | 0 | 0 |
| `logic/scan_settings.py` | 0 | 0 |
| `ui/camera_widgets.py` | 14 | 0 |
| `ui/main_window.py` | 30 | 0 |
| `ui/plot_widgets.py` | 52 | 0 |

The 96 GUI-module diagnostics break down by diagnostic rule:

| Rule | Count |
| --- | ---: |
| `reportOptionalMemberAccess` | 39 |
| `reportAttributeAccessIssue` | 23 |
| `reportImplicitOverride` | 18 |
| `reportCallIssue` | 6 |
| `reportArgumentType` | 5 |
| `reportOperatorIssue` | 2 |
| `reportAssignmentType` | 1 |
| `reportMissingImports` | 1 |
| `reportReturnType` | 1 |

Those counts are **diagnostics, not confirmed runtime bugs**. Known optional
dependencies such as MATLAB Engine and VmbPy are intentionally absent from
the driver-free CI profile. PySide6 dynamically typed interfaces also need
careful interpretation before source changes.

Evidence: [dedicated Windows diagnostic run](https://github.com/simoneferraresi/IOPanel/actions/runs/40329007673).
The temporary workflow modification is isolated on
`fix/phase-d-r06-diagnostic-probe` and not part of this pull request.

## Scope of this PR

Add the two zero-diagnostic, hardware-independent scan model modules to
the explicit Pyright `include` list (32 files to 34). No runtime source,
dependency, test fixture, or hardware configuration changes; no suppressions.

The subsequent GUI coverage changes should be independently reviewable:
camera widgets (14 diagnostics), plotting widgets (52), and MainWindow (30),
with attention to actual optional-state risks and vendor/Qt typing limitations.

## Acceptance

Mandatory CI must pass Ruff, formatting, Pyright, exact pytest collection,
all supervised hardware-independent partitions, and Windows packaging where
the PR checks require it. The D1 prerequisite was merged as #258;
this PR was rebased onto the resulting main branch and must satisfy its
required Windows packaging check before merge.

## Full Phase D R06 coverage (proposed completion)

Phase D R08 and the initial R06 steps were merged in PRs #258–#262. The
follow-up plotting and MainWindow coverage is reviewed in PRs #263 and #264:

- `ui/camera_widgets.py`: 14 initial errors, 0 after PR #260 (merged).
- `ui/plot_widgets.py`: 52 initial errors, 38 after the small
  HistogramWidget patch (#261), and 0 on #263's Windows Pyright run.
- `ui/main_window.py`: 30 initial errors, 29 after the status-bar
  regression fix (#262), and 0 on the combined #263 + #264 Windows run.

Once #263 and #264 are merged, the explicit Pyright `include` list will cover
**37 modules** (up from 32 at the initial audit). These counts refer to
`typeCheckingMode = "basic"` with Python 3.12; they do not imply strict-mode
coverage or physical vendor SDK validation. The absent optional MATLAB Engine
and VmbPy re-export symbols require only two narrowly scoped, documented
vendor-environment suppressions. No general exclusion or diagnostic-wide
suppression was added.

Combined CI evidence: [Windows test run](https://github.com/simoneferraresi/IOPanel/actions/runs/40337724768).
Additional simulated/native-exit evidence: [Qt reliability campaign](https://github.com/simoneferraresi/IOPanel/actions/runs/40338197367).

The two final code changes are split by dependency: #263 (plot module)
precedes #264 (MainWindow). The latter must be checked again against the
squash-merged #263 commit with the repository's normal main-targeted
Windows test and packaging workflows before it is merged.
