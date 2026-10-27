# Phase E final qualification — 27 October 2026

## Decision

**Hardware-independent source and frozen-package qualification passed for local candidate `4de5211351caa46fd3cfa0079866db35858c8348`. Phase E is not ready for integration yet.** Native MATLAB acceptance and physical CT400/Vimba acceptance remain unrun and require an authorized lab operator. The local hardening change has not reached GitHub or CI because automatic approval review rejected the source upload. Keep PRs #266–#268 as drafts and do not merge on the evidence in this report alone.

This report describes the tested source SHA. The native acceptance runner added afterward is a local, plan-only checked tool and was not part of the tested application source commit.

## Source change reviewed and qualified

The independent review found a MATLAB `FutureResult` final-reference release on the Qt polling thread in successful/error startup completion. Installed MATLAB Engine 24.2.2 finalizer source showed that releasing this object invokes the vendor native API. The correction transfers final reference release to the manager's tracked disposal worker and prevents Engine quit from racing the future destructor. It also clears exception traceback/context references that otherwise retain the future. See [the independent review](phase-e-final-review-2026-10-27.md).

## Windows qualification

Environment: Windows x64, Python 3.12.8, PySide6/Qt 6.12.0, pinned project lock, and no camera or MATLAB Engine package in the isolated build/test environment. Campaign source was clean at the recorded SHA. Exact collection: **1,617 tests** (core 1,084, camera 215, plot 318).

Command:

```powershell
uv run --no-sync python tools/qt_reliability_campaign.py --output output\phase-e-final-qualification-4de5211 --core-runs 1 --camera-runs 2 --plot-runs 2 --stress-cycles 10
```

| Group | Result | Child / supervisor | Notes |
| --- | --- | --- | --- |
| Stress | 10 cycles passed | 0 / 0 | Bounded lifecycle stress |
| Core | 1,083 passed, 1 skipped | 0 / 0 | 198 existing warnings |
| Camera | 215 passed × 2 | 0 / 0 | 25 existing warnings per run; simulated/fake camera only |
| Plot | 318 passed × 2 | 0 / 0 | 23 existing warnings per run |

Campaign collection and supervisor returned 0; `complete=true`. Warnings were existing NumPy 2.5 / pyqtgraph shape-assignment deprecations. Detailed child evidence is in [campaign.json](../output/phase-e-final-qualification-4de5211/campaign.json) and sibling run directories.

The exact-source checks also passed: Ruff check across the repository, Ruff format check across 288 files, Pyright with 0 errors/warnings/information, and exact test collection 1,617. The focused manager/cooperative-shutdown suite passed 32 tests with 3 existing deprecation warnings.

During the new focused regression work, an early test exposed two real ownership hazards in the proposed correction: a fast disposal worker could race the GUI's last local reference, and an exception traceback could retain the failed future through its frame. Retry also had to wait for a pending successful-future release before starting a replacement. The final handoff barrier, traceback clearing, disposal gate, and explicit destructor-order tests address these cases. The exact final focused run passed. Earlier unsuccessful campaigns in the E1/E2/E3 history remain recorded as failures with their fixes in the phase-specific validation notes; this report counts only the final complete campaign above as passing evidence.

## Frozen package and graphical smoke

The locked environment was synchronized with `uv sync --locked --no-default-groups --extra dev --extra test --extra lint --extra typecheck`; no lockfile changes occurred. The PyInstaller build from the same application source SHA completed with PyInstaller 6.22.3 using the repository spec and isolated output paths:

```powershell
uv run --no-sync pyinstaller --clean --noconfirm --distpath output\phase-e-package-4de5211\dist --workpath output\phase-e-package-4de5211\build packaging\IOPanel.spec
```

It produced [IOPanel.exe](../output/phase-e-package-4de5211/dist/IOPanel/IOPanel.exe), `_internal`, and the simulation sidecar config: 449 files, 339,454,633 bytes. The sidecar hash matches `packaging/config.simulation.ini` (`5EDDB16D1DFCF67BF86AC116E738B15D1DC69B5C2B2900C5BB0BB68D15E6402D`). Optional dependency warnings covered unused Jupyter/scipy/tzdata components, absent macOS serial frameworks, and optional OpenGL runtimes. MATLAB Engine was absent and VmbPy was excluded as intended.

Commands and results:

```powershell
uv run --no-sync python tools/smoke_frozen.py output\phase-e-package-4de5211\dist\IOPanel\IOPanel.exe --timeout 60
uv run --no-sync python tools/smoke_frozen_opengl.py output\phase-e-package-4de5211\dist\IOPanel\IOPanel.exe --platform windows --timeout 30 --evidence-dir output\phase-e-package-4de5211\opengl-evidence
```

The bounded executable smoke passed: simulated CT400 and camera came online, a simulated frame was saved, resources shut down, and the process exited 0 using offscreen Qt, a different working directory, and isolated `LOCALAPPDATA`. A physical CT/camera profile failed closed before MainWindow construction. The OpenGL smoke also passed on Windows: synthetic alignment map, axes, labels and colorbar rendered, viewer reuse worked, and physical-profile rejection was observed. The build and smoke used the existing PyInstaller spec and simulation configuration; they made no instrument call.

Scope: packaged smoke did not individually exercise simulated scanning, monitoring, or repeated panel open/close sequences. Source lifecycle tests exercise the corresponding cleanup with fakes. No physical SDK, CT400, camera, or laser operation occurred.

## Startup performance comparison

Five offscreen captures per revision compared origin/main `63c0c4b` with the exact candidate under the same locked environment and Qt. `environment_mismatch=false`.

| Metric | main median | candidate median | Change |
| --- | ---: | ---: | ---: |
| Entry to first paint event | 1.87584 s | 1.84104 s | −1.86% |
| MainWindow construction | 0.15510 s | 0.15747 s | +1.53% |
| First Qt callback | 5.78 ms | 5.66 ms | −2.1% |

These small differences show no material startup regression in this offscreen measurement. The metric does not measure compositor presentation, camera throughput, idle CPU, or active instrument operations. Evidence: `output/phase-e-package-4de5211/startup-comparison.json`.

## Native MATLAB acceptance — pending operator approval

The workstation has `C:\Program Files\MATLAB\R2024b\bin\matlab.exe`; read-only environment metadata reports MATLAB Engine 24.2.2. MATLAB R2024b Update 4 is recorded by the preceding E3 lab validation. The prepared native environment is the preserved lab `.venv` with Python 3.12.8 / MATLAB Engine 24.2.2 / MATLAB R2024b Update 4; execution must be launched from `.phase-e-worktree` so application imports resolve to the reviewed tree. MATLAB was **not launched**. No native FIG file was produced or reopened. Vendor calls, future cancellation semantics, license cleanup, and test-owned MATLAB PID exit remain unverified.

The added [native acceptance runner](../tools/matlab_lifecycle/native_fig_acceptance.py) defaults to plan-only and its `--help` and plan modes were checked without importing/starting MATLAB. Ruff and Pyright passed for the runner. The runner itself is new after source qualification; its native execution path is unvalidated. It requires `--execute`, `--confirm-native-matlab`, a fresh evidence directory, and an exact typed phrase. It runs the cases sequentially in separate children, records process ancestry/identity snapshots, event timestamps, startup cancellation, manager states, FIG thread retirement, Engine lease acquire/release, native quit entry/return and child exit. It applies a per-scenario observation deadline and never terminates processes. A deadline leaves the attributed test child/native owner running, records the pending state, and stops the sequence for operator-controlled recovery. Review the source and do a non-native plan review before anyone executes it.

Suggested approved invocation (do not run without explicit operator approval for these cases):

```powershell
C:\Users\bellanca\Desktop\IOPanel\.venv\Scripts\python.exe tools\matlab_lifecycle\native_fig_acceptance.py --scenario all --execute --confirm-native-matlab --evidence-dir output\native-matlab-acceptance-<new-id> --deadline-minutes 20
```

Run this from `C:\Users\bellanca\Desktop\IOPanel\.phase-e-worktree`, with the candidate application source at `4de5211351caa46fd3cfa0079866db35858c8348`; the runner manifest records the complete current checkout SHA as well.

The operator must type exactly `I approve native MATLAB Engine acceptance on this workstation` at the prompt. Cases are: close before startup (zero launches); normal synthetic FIG export and reopen/value/style checks (one launch); close during startup (zero or one launch depending on actual cancellation/completion); deliberately rejected cancellation followed by one late-startup disposal (one launch); and close during an active FIG call with a GUI heartbeat and verified save-before-quit ordering (one launch). Expected impact is **3–4 sequential MATLAB Engine startups/license checkouts**, with at most one test child active at a time. The default deadline is 20 minutes per scenario, configurable up to 45; if a scenario blocks, the runner stops advancing and leaves that scenario's child/session for the operator to inspect without issuing termination. Evidence includes timestamped events/manifest, Python and MATLAB process PID/parent/path/owner/creation metadata, manager states, cancellation, lease and FIG-thread events, quit entry/return, FIG existence/reopen assertions, and process status after child exit.

The runner uses synthetic immutable arrays, isolated QSettings, and a standalone PlotWidget. It will not read or alter the operational lab config; call CT400, Vimba, piezo/stage, laser, EDFA, or scan/alignment code; change instrument settings; or terminate any MATLAB/Python process. It does not touch unrelated MATLAB sessions. It writes only to the explicitly chosen new evidence directory. If a child blocks, recovery is left to the operator and the lab's MATLAB/license procedure.

Review each scenario's logs and supervisor evidence. Verify child return 0, orderly widget close, manager shutdown completion, worker retirement before quit, expected FIG values/labels/colors, and disappearance of only the MATLAB process attributed to that scenario. Existing/unrelated MATLAB sessions must remain untouched. A timeout is pending/failed evidence, not a pass; do not terminate an image-name match or claim cleanup from API return alone.

## Physical acceptance — pending lab operator and instrument availability

Nothing in this section was executed. Use the lab's approved operational config and preserve its existing settings and hashes. An authorized operator must identify the device, serial/USB identity, interlocks, laser state, safe setpoints, and recovery procedure before connecting.

| Device/path | Planned acceptance | Pass evidence | Current result |
| --- | --- | --- | --- |
| CT400 | With operator-approved safe state, connect the identified unit; exercise monitor start/stop, selected-input behavior, and orderly close while monitoring is active. Run scan/alignment paths only if separately authorized for the chosen optical state. | Exact device identity, app/firmware/config, timestamped operation log, monitor-stop acknowledgement, selected-input/laser safe state, accepted close, connection release; record any blocked call as pending. | **Not run** |
| Vimba camera | Connect only the approved camera by serial; verify initialization, streaming/frame delivery, stop/close while a conversion is in flight, and release. | VmbPy/device/firmware identity, serial, frame evidence, conversion retirement, camera close/system release and process exit. | **Not run** |
| MATLAB/FIG | Follow the isolated native runner procedure above; no instruments are involved. | Runner manifest/events, reopened FIG assertions, shutdown order, child exit and attributed PID observation. | **Not run** |

Physical acceptance procedure:

1. **Preconditions:** confirm the tested source SHA, approved package/config paths, instrument owner and maintenance window; preserve a copy/hash of the current lab config; record CT400 model/serial/firmware, camera model/serial/firmware, Vimba version, and connection method. An operator checks interlocks, optical table state, beam enclosure, approved low-power/safe laser state, beam blocks, and local laser SOP. Keep lasers disabled unless the operator's established procedure explicitly requires a separately approved safe state. No stage movement, scan, alignment, or optical-power change is part of this basic shutdown check.
2. **CT400 start/stop:** operator connects the identified CT400, verifies the GUI identity, starts only the power monitor, observes stable readings, then stops monitoring. Confirm the selected laser input is disabled according to the device UI/protocol and monitor ownership is released before proceeding.
3. **Close during monitoring:** operator restarts the monitor and requests ordinary window close while readings are active. Expect the GUI to remain responsive and close to wait for the in-flight read/stop acknowledgment. Verify the selected input reaches the documented safe/off state, no new read begins after stop acknowledgment, and the device connection releases only after worker retirement.
4. **Camera streaming and close:** operator opens only the approved camera by serial, streams a short preview, and closes its panel while frames/conversion are active. Expect frame intake to stop, the in-flight conversion to retire, panel close to complete, and VmbSystem/camera resources to release once. If the SOP permits, separately disconnect/reconnect that same camera through the supported GUI recovery path; do not reset or power-cycle it as part of this test.
5. **Combined shutdown:** only after the standalone cases pass, operator starts CT400 monitoring and the camera preview together, confirms laser remains in the approved safe state, and requests application close. Expect both owners to retire independently before final device release/window close. Do not start scan/alignment or energize the laser for this case.
6. **Evidence:** preserve timestamped application logs, exact config hash, operator/device identities, monitor stop/laser-off confirmation, frame/stream result, close/retirement sequence, device release and application exit code. Attach evidence to the candidate SHA; redact sensitive lab/network identifiers before sharing outside the lab.
7. **Rollback/recovery:** if any operation stalls, reports an unexpected laser state, loses device ownership, or fails to retire, stop the acceptance sequence. Keep the GUI/process and hardware in the operator-approved state; use the lab SOP to disable/secure the optical source and recover the identified instrument. Do not force-kill the process or disconnect/reset equipment to make the test appear to pass. Record the failure and involve the instrument owner before retrying.

Physical acceptance must be additive evidence tied to the candidate SHA. Do not use a different working tree/config silently, and do not mark a hung driver call passed because the window eventually disappeared.

## Publication and integration status

The connector attempted to upload the source blob for `logic/matlab_engine_manager.py` and the regression file `tests/test_matlab_engine_manager.py` to `simoneferraresi/IOPanel` for a draft hardening PR. Automatic approval review rejected both uploads because they disclose full source files to the external GitHub repository without trusted authorization for those exact payloads/destination. No alternative upload route was used. The commit remains local at `4de5211351caa46fd3cfa0079866db35858c8348`; it has no remote branch, PR, or CI result.

Latest inspected PR heads: #266 `87791a2bfdce30e630b35986930854a97ad389a0`; #267 `04cf3f64dbaf4f46402fc3c0fa92cb2d29e5218c`; #268 `fae56c9d1fca574c089e0dacccf9d23e0195a6bb`. All three were open, draft, and mergeable; no review submissions, inline threads, or comments were present. Historical CI runs on those heads passed. No remote branch update occurred during this qualification. They remain drafts. No PR was merged. No main branch, existing PR branch, physical device, or real MATLAB session was modified by this qualification.

The original checkout's `config.ini` and untracked `config.lab.ini` remain unchanged. The final recommendation is **hold integration until the local hardening is reviewed and receives CI, and authorized operators provide native MATLAB plus the relevant physical device acceptances**. See [merge readiness](phase-e-merge-readiness-2026-10-27.md) for the tested sequencing options.

## Final status by area

| Area | Independent review | Fixes | Full CI / local campaign | Packaged smoke | Native/device acceptance | Merge ready |
| --- | --- | --- | --- | --- | --- | --- |
| E1 CT400 | Passed; no new finding | No follow-up fix | Passed | Simulation package smoke passed | CT400 physical acceptance **Pending approval** | **No**; E1 is separable but physical acceptance and normal PR review remain |
| E2 camera | Passed; no new finding | No follow-up fix | Passed | Simulated-camera package smoke passed | Physical Vimba acceptance **Pending approval** | **No**; follows E1, independent of E3, but device acceptance/review remain |
| E2 TaskRunner | Passed; no new finding | No follow-up fix | Passed | Included in packaged application lifecycle smoke only | Not applicable | **No**; PR/review sequence remains |
| E3 FIG | Passed; startup finalizer issue corrected in follow-up commit | Fixed locally at `4de5211` | Passed on corrected tree | Frozen GUI/OpenGL smoke passed; native FIG export not covered | Native MATLAB/FIG **Pending approval** | **No**; follow-up unavailable remotely and native acceptance pending |
| E3 Engine manager | Passed; P1 future-finalizer finding fixed | Fixed locally at `4de5211` | Passed on corrected tree | Package excludes MATLAB Engine by design | Native MATLAB process/cancellation acceptance **Pending approval** | **No**; follow-up unavailable remotely and native acceptance pending |
