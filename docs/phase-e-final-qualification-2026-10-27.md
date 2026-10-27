# Phase E final qualification — 27 October 2026

## Decision

**Hardware-independent source and frozen-package qualification passed for application candidate `4de5211351caa46fd3cfa0079866`. Phase E is not ready for integration yet.** The correction and associated reports were published after explicit authorization as successor draft PR #270. All four CI jobs passed on published head `f2e0b272724eb45aed5c60006d51d3d9b9bc3caa`. The runner safety update and follow-up widget fix are local pending publication; GitHub CLI authentication could not be restored because this environment denied the HTTPS login connection. One native Engine startup was attempted; R2024b showed Fatal Startup Error and Engine returned `Transport stopped`. The cause and license status remain unknown; no FIG was exported. Physical CT400/Vimba acceptance remains unrun and unauthorized. Keep #266–#270 as drafts and do not merge on this evidence alone.

The original Phase E qualification remains tied to its recorded source SHA. The later native attempt and local callback fix are separate evidence and changes; the startup failure did not qualify MATLAB FIG export or shutdown.

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

## Native MATLAB acceptance — startup failure

The reviewed runner used synthetic arrays and a standalone `PlotWidget` in the isolated Python 3.12.8 / PySide6 6.12.0 environment. The no-startup close case passed. Its first Engine startup launched the expected R2024b executable, which displayed **Fatal Startup Error**. After a normal close request to that test-owned window, Engine reported `Transport stopped`. No licensing diagnostic was surfaced, so the cause is undetermined.

The startup-failure callback then exposed an application defect: standalone `PlotWidget` instances had no `error_list` (or `saved_files_list`) until the export dialog initialized them. The callback raised `AttributeError` and left the FIG request pending. A focused regression now covers this path; both lists are initialized by the widget constructor. The supervisor timed out at 20 minutes with child 21280 still running; that child later exited on its own deadline. No MATLAB process remains and no later scenario started.

Evidence is in `output/native-matlab-acceptance-20261027-01/`. No FIG was exported or reopened. The remaining three scenarios did not run. No physical instrument APIs or laboratory configuration were used.

| Native scenario | Result |
| --- | --- |
| Close before startup | **Passed** |
| Normal FIG export/reopen | **Failed at Engine startup**; R2024b showed Fatal Startup Error, Engine returned `Transport stopped`; callback defect fixed locally |
| Close during startup | **Not run** |
| Rejected cancellation/late Engine | **Not run** |
| Close during FIG save | **Not run** |

## Physical acceptance — pending lab operator and instrument availability

Nothing in this section was executed. Use the lab's approved operational config and preserve its existing settings and hashes. An authorized operator must identify the device, serial/USB identity, interlocks, laser state, safe setpoints, and recovery procedure before connecting.

| Device/path | Planned acceptance | Pass evidence | Current result |
| --- | --- | --- | --- |
| CT400 | With operator-approved safe state, connect the identified unit; exercise monitor start/stop, selected-input behavior, and orderly close while monitoring is active. Run scan/alignment paths only if separately authorized for the chosen optical state. | Exact device identity, app/firmware/config, timestamped operation log, monitor-stop acknowledgement, selected-input/laser safe state, accepted close, connection release; record any blocked call as pending. | **Not run** |
| Vimba camera | Connect only the approved camera by serial; verify initialization, streaming/frame delivery, stop/close while a conversion is in flight, and release. | VmbPy/device/firmware identity, serial, frame evidence, conversion retirement, camera close/system release and process exit. | **Not run** |
| MATLAB/FIG | Synthetic standalone PlotWidget path; no instruments involved. | Runner manifest/events and attributed process identity. | **Startup failed** before FIG export; see native results above. |

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

The first publication attempt was rejected by automatic review because the source payloads lacked trusted authorization for that destination. After explicit user authorization, exact committed blobs were uploaded and matched their local Git blob SHAs. Successor commit `3246bd2d8ee7c7ba92bf80cb79cf656e37c703fa` has parent remote E3 `fae56c9d1fca574c089e0dacccf9d23e0195a6bb` and tree `b9272cf5dc11e565dcde3f12a071bb1b08b60a33`, exactly matching local deliverable tree `4972e3319a101260d5a0e759c05c20b3e7436bae`. Documentation follow-up `f2e0b272724eb45aed5c60006d51d3d9b9bc3caa` has parent `3246bd2` and tree `b02900c8af6f2b18145314040333239e5caf34d4`. Draft [PR #270](https://github.com/simoneferraresi/IOPanel/pull/270) targets #268's branch. All four jobs passed on `3246bd2` (run [40479569018](https://github.com/simoneferraresi/IOPanel/actions/runs/40479569018)) and on the docs head `f2e0b27` (run [40480514115](https://github.com/simoneferraresi/IOPanel/actions/runs/40480514115)).

The local runner safety update stops on unowned MATLAB processes, unexpected executable paths, or sessions still alive after the Python child exits. The user has authorized its publication, but the local GitHub CLI token is invalid and the interactive HTTPS login was denied by the environment socket restriction. The changes remain local pending restored authentication.

Latest inspected PR heads: #266 `87791a2bfdce30e630b35986930854a97ad389a0`; #267 `04cf3f64dbaf4f46402fc3c0fa92cb2d29e5218c`; #268 `fae56c9d1fca574c089e0dacccf9d23e0195a6bb`; #270 `f2e0b272724eb45aed5c60006d51d3d9b9bc3caa`. They were open drafts; #270 was mergeable. No reviews, inline threads, or comments were present. Historical CI on #266–#268 passed; #270 all four jobs passed on its current head. No PR was merged. No main branch, original Phase E branch, physical device, or real MATLAB session was modified.

The original checkout's `config.ini` and untracked `config.lab.ini` remain unchanged. Hold integration until the local runner and widget changes pass CI, native MATLAB FIG and shutdown acceptance complete, and separately authorized physical device acceptance is available. See [merge readiness](phase-e-merge-readiness-2026-10-27.md) for the tested sequencing options.

## Follow-up validation

The callback regression and focused MATLAB lifecycle suite passed (**64 tests**); Ruff, format check, Pyright, and exact collection verification passed (**1,617 tests**). The CI supervisor could not query Windows process identity (`Get-CimInstance`: Access denied). Its core child completed with **1,078 passed, 5 failed, 1 skipped**; the five failures were process-identity/cleanup checks under that restriction. Camera and plot partitions were not run, so the full CI gate remains incomplete.

## Final status by area

| Area | Independent review | Fixes | Full CI / local campaign | Packaged smoke | Native/device acceptance | Merge ready |
| --- | --- | --- | --- | --- | --- | --- |
| E1 CT400 | Passed; no new finding | No follow-up fix | Passed | Simulation package smoke passed | CT400 physical acceptance **Pending approval** | **No**; E1 is separable but physical acceptance and normal PR review remain |
| E2 camera | Passed; no new finding | No follow-up fix | Passed | Simulated-camera package smoke passed | Physical Vimba acceptance **Pending approval** | **No**; follows E1, independent of E3, but device acceptance/review remain |
| E2 TaskRunner | Passed; no new finding | No follow-up fix | Passed | Included in packaged application lifecycle smoke only | Not applicable | **No**; PR/review sequence remains |
| E3 FIG | Passed; startup finalizer issue corrected in follow-up commit | Failure-report initialization fixed locally | Published head CI passed; new changes awaiting final gate | Frozen GUI/OpenGL smoke passed; native FIG export not completed | **Failed at native startup**; callback regression fixed locally | **No**; native FIG acceptance and publication/CI remain |
| E3 Engine manager | Passed; P1 future-finalizer finding fixed | No further manager change | Published head CI passed; new changes awaiting final gate | Package excludes MATLAB Engine by design | **Startup failed** with `Transport stopped`; no native shutdown case completed | **No**; native lifecycle acceptance and publication/CI remain |
