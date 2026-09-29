# Laboratory validation report

## Run 1 — Offline audit

**Date:** 2026-09-26

**Operator/authorization reference:** Not applicable; no hardware operation was performed.

**Application commit:** `3a96ebbad3242b4c9c41ea7bca45985fd2f0c9a1`

**Branch:** `lab-validation/offline-audit-2026-09-26`
**Working tree at start:** clean; local `main`, local `origin/main` tracking ref, and the requested baseline all pointed to the same commit. No network fetch was performed during the offline audit.

## Offline checks

| Check | Result | Evidence / notes |
| --- | --- | --- |
| Repository baseline and history | PASS | Commit `3a96ebbad3242b4c9c41ea7bca45985fd2f0c9a1` is the `main` tip and is in its history. |
| Application configuration parse | PASS | Existing `config.ini` parses as valid. It selects the physical CT400 backend, configures the CT400 DLL, and enables two Vimba cameras. The file was not printed, copied, or modified; its device identifiers and settings are intentionally omitted here. |
| Full hardware-independent suite | PASS | In PowerShell, set `$base = Join-Path (Get-Location) '.pytest-lab-validation-tmp'`, create that directory, then run `python -m pytest -q -p no:cacheprovider --basetemp="$base"` — 29 passed. The first default invocation reached 22 passes but 7 Qt tests could not create pytest-qt temporary directories under the host's protected user temp path; rerunning with the workspace-local temp directory passed all 29. The temporary directory was removed. |
| Application Python | RECORDED | Python 3.12.8, 64-bit; Windows 10 build 19045. |
| Python packages | RECORDED | PySide6 6.8.1.1; NumPy 2.2.1; opencv-python 4.11.0.86; pytest 8.4.0; VmbPy 1.0.5. |
| Allied Vision software | RECORDED | Vimba X 2024.1.0.3916 and Vimba 6.0.0.32034 are registered as installed. Also installed: Vimba 1394 TL 1.6.0, USB TL 1.4.1, CL Config TL 1.2.0, and GigE TL 1.9.0. Vimba X GigE and USB `.cti` files are present. `VIMBA_HOME`, `VIMBA_X_HOME`, `GENICAM_GENTL32_PATH`, and `GENICAM_GENTL64_PATH` are configured; values are omitted from this report. No camera SDK discovery or streaming API was called. |
| CT400 software files | RECORDED | The DLL configured by the existing INI exists; static PE inspection identifies it as x64 and Windows file metadata reports version 1.4.1.0 (timestamp 2018-08-31). A local `CT400_lib.h`, `CT400_UM_3.9v1.0.pdf`, `CT400_PG.pdf`, and vendor example wrappers were found alongside it. No DLL was loaded and no vendor executable was run. |
| ctypes declarations against local header | PASS (static comparison) | `CT400_Init`, `CT400_CmdLaser`, `CT400_ScanStart`, `CT400_ScanStop`, `CT400_ScanWaitEnd`, and `CT400_Close` have matching integer widths, arguments, and return types in the Python wrapper. The wrapper uses `WinDLL`, matching the header's `__stdcall`. The wait buffer allocated by Python is 4096 bytes; the header declares a 1024-byte error array. |

The issue-relevant contract extracted from the locally installed CT400 header is:

| API | Header contract | What remains unspecified |
| --- | --- | --- |
| `CT400_Init(int32_t *iError)` | Returns a `uint64_t` handle; zero means initialization failed. Header notes `iError = -1001` for incompatible DSP firmware. | No additional threading or cleanup guarantees found in the header. |
| `CT400_CmdLaser(handle, input, enable, wavelength, power)` | Returns 0 on success, -1 otherwise. The input is an explicit `LI_1`–`LI_4` enum. | No statement that a command for one input disables any other input; no close-time laser-safe guarantee. |
| `CT400_ScanStart(handle)` | Returns 0 on success, -1 otherwise. | No thread-affinity or concurrency statement. |
| `CT400_ScanStop(handle)` | Purpose says it stops a scan; returns 0 on success, -1 otherwise. | No guarantee it safely interrupts a simultaneous wait call, no wait/stop ordering, and no timing bound. |
| `CT400_ScanWaitEnd(handle, char error[1024])` | Purpose says it waits for scan end and returns errors; zero means no error, -1 otherwise; error text is written to the supplied buffer. | No timeout, polling behavior, interruptibility, concurrency, or thread requirements. Specific non-−1 scan status meanings are not defined in this header. |
| `CT400_Close(handle)` | Releases memory allocated by the DLL; returns 0 on success, -1 otherwise. | No laser-output or selected-input cleanup guarantee. |

The Python wrapper's `scan_wait_end()` treats `-1` as a communication/call error and returns other negative values to its caller. The local header does not define those other values. Accordingly, detailed status mappings in the Python enums remain unverified against this header. The header is local vendor API material, but its exact match to the installed DLL build is not independently attested; its declarations agree with the DLL file version context and the checked-in ctypes signatures.

The local docs do not establish whether `ScanWaitEnd` is a blocking call in the operational sense required by Issue #22, whether `ScanStop` can interrupt it safely from another thread, or what return codes are safe to treat as cancellation. At the time of this initial audit, static review found `CT400.close()` issuing a disable request for `LI_1` before `CT400_Close`, while scan cleanup disabled the selected input. The vendor header did not say that disabling `LI_1` disables other inputs. No laser commands were sent to test this behavior; the Issue #23 section below records the software correction.

## Physical gates (initial audit status; historical; see completed camera qualification below)

| Gate | Result | Notes |
| --- | --- | --- |
| A — Offline environment/configuration | PASS (offline checks only) | Hardware-independent tests and config parsing passed. The site/operator approval record and approved scan limits are not available in this offline audit. |
| B — Camera discovery and controlled streaming | PARTIAL | Gate B1 discovery PASS; Gate B2 Top-camera streaming PASS. Side-camera streaming and the complete IOPanel camera workflow remain NOT RUN. See the separate run records below. |
| C — CT400 identity, connection, safe idle | NOT RUN | No CT400 initialization or device query was initiated. Instrument identity, firmware, connection and independent safe-state indication remain unverified. |
| D — Operator-approved CT400 scan | NOT RUN | No scan settings were provided or used; no laser command or scan was issued. |
| E — Supervised cancellation/shutdown | NOT RUN | No active scan or cancellation was initiated. Safe interrupt and shutdown behavior remain unresolved. |
| F — Concurrent scan and camera streaming | NOT RUN | No hardware acquisition was initiated. |
| G — Final hardware resource release | NOT RUN | No device resources were opened. |

At the time of this initial offline audit, no physical hardware validation had been completed; the later camera qualification below supersedes the earlier camera status. No simulated result is represented as physical evidence. No hardware or driver failure was observed in that audit. No production wrapper changes were made.

## Remaining questions and next action

- Issue #22's ScanWaitEnd/ScanStop ordering and scan-code questions are superseded by the versioned Programming Guide review in “CT400 Programming Guide lifecycle correction — Issue #22” below. Generic DLL thread-safety/reentrancy remains undocumented; this report does not claim that physical cross-thread behavior is validated.
- Obtain vendor confirmation of the installed header/DLL pairing and whether `CT400_Close` or any `CT400_CmdLaser` disable operation affects other inputs.
- Before live validation, have the responsible operator approve device-specific settings and safe-state indications, then proceed through the existing protocol one gate at a time. Gates B–G each require separate authorization before hardware operation.
- Physical cancellation and close-during-acquisition qualification remain outstanding; no scan or laser command was issued during the software correction.

## Follow-up audit — software, Git and camera preparation

**Date:** 2026-09-26. These are source, package metadata, installed-file and process-list findings. Offline configuration validation imported `app`, which imports `hardware.camera` and the installed VmbPy package; VmbPy's Python binding loads the VmbC DLL at import. No `VmbSystem` startup/context, camera discovery, camera open/stream, CT400 call, or instrument operation was performed. Loading the DLL alone was not treated as a hardware compatibility test.

### Git and publication

An explicit fetch of `refs/heads/main` from `origin` completed successfully. Fetched `origin/main` and local `main` both resolve to `3a96ebbad3242b4c9c41ea7bca45985fd2f0c9a1`; the remote main had not advanced at fetch time. A subsequent `ls-remote` attempt failed to connect, but the direct fetch had already confirmed the main ref. The local documentation branch is still separate from `main`.

GitHub CLI was checked independently of the GitHub connector. `gh auth status` reports the cached GitHub token is invalid. The connector also rejected issue-comment writes with HTTP 403. No token or credential value is recorded here.

### Vimba X binding/runtime and transport layers

| Finding | Evidence and limit |
| --- | --- |
| Python environments | The current shell's `python` resolves to Python 3.12.8 x64 and has VmbPy 1.0.5 in user `site-packages`. The repository `.venv` also uses Python 3.12.8 x64 but has no VmbPy installed. README's documented `python app.py` uses the current shell's interpreter; a `uv run` launch would use `.venv` and would not have the optional camera binding. No IOPanel process was found, so an alternate shortcut/launcher cannot be ruled out. |
| VmbPy resolved by the documented Python | Package metadata resolves `vmbpy` 1.0.5 from the current shell interpreter's user `site-packages`. Its install metadata's source archive hash matches the VmbPy 1.0.5 wheel bundled with the installed Vimba X SDK. The package source contains the API names imported by `hardware/camera.py`, including `intersect_pixel_formats`. The package and symbol checks did not instantiate `VmbSystem`. |
| SDK pairing | Installed Vimba X release notes identify release 2024-1 and list VmbPy API 1.0.5, VmbC API 1.0.6, GigE TL 1.10.0, and USB TL 1.5.0. The installed VmbC DLL reports 1.0.6.23201; Image Transform reports 2.1.0.0. The local bundle therefore matches the version set published in those release notes. This is SDK/version evidence, not a camera compatibility test. |
| Application dependency mismatch | `pyproject.toml` requires `vmbpy>=1.1.0`; `uv.lock` resolves 1.2.2, while the current shell Python has 1.0.5. The import names IOPanel uses are present in 1.0.5, but the installed environment is below the declared project requirement. Do not alter the working SDK or Python environment as part of this audit. |
| Runtime selection | The installed VmbPy Windows loader constructs VmbC's path from `VIMBA_X_HOME\api\bin`; `VIMBA_X_HOME` points to Vimba X. `VIMBA_HOME` separately points to legacy Vimba 6.0. The configured 64-bit GenTL path places Vimba X CTIs first and also includes legacy Vimba 6.0 CTIs; the 32-bit path includes legacy CTIs. Other CTI directories are also configured. This mixed path is a potential transport-layer selection/duplicate-discovery concern; no enumeration was run to establish an actual conflict. |
| IOPanel camera behavior from source | The current config parses with both named camera sections enabled and backend `vimba`. `MainWindow` startup initializes enabled physical cameras. `VimbaCam.open()` opens the selected device, starts streaming, and configures `AcquisitionMode=Continuous`, `TriggerMode=Off`, `ExposureAuto=Off`, `GainAuto=Off`, Gamma (clamped to 1.0), and an OpenCV-compatible pixel format. This static behavior makes normal GUI startup unsuitable for a no-settings-change discovery or streaming procedure. |
| Passive process inspection | `Get-Process` showed no process named IOPanel, Vimba/Vmb, Vimba viewer, CT400/Yenista, LabVIEW, MATLAB, Python, or common machine-vision viewer names. NI Device Monitor and NI background services were present. WMI process inspection was denied. Process names cannot prove that no other application/service owns a camera, so exclusivity remains unverified. |

### Temporary camera-validation configuration

Created a local-only temporary camera-validation configuration as a copy of the existing config with `ct400_backend = simulation` added. Offline model parsing confirmed DummyCT400 is selected and the two camera configurations are unchanged. The original `config.ini` was not edited. The temporary file contains private laboratory camera configuration and is intentionally untracked and excluded from publication.

Static source review confirms `CT400InitWorker.run()` checks the `simulation` backend before DLL lookup or `CT400(...)` construction and returns `DummyCT400`. Thus, if this temporary file is passed to IOPanel, the CT400 initialization worker cannot load or call the physical CT400 DLL. This proof applies to that worker path; it does not make application startup a discovery-only action, because enabled Vimba cameras open during startup.

### Gate B1/B2 state

### Physical camera run — Gate B1 discovery

**Date:** 2026-09-27 (operator-reported run; exact time not provided). **Result: PASS for discovery only.** The operator ran the prepared `tools/vimba_b1_diagnostic.py` in ordinary Windows Command Prompt, using system Python 3.12.8 x64, VmbPy 1.0.5, VmbC 1.0.6, and Vimba X GigE Transport Layer 1.10.0. The diagnostic used a ten-second discovery wait and reported normal VmbSystem context exit.

| Configured camera | Discovered identity | Interface | Configuration comparison |
| --- | --- | --- | --- |
| Top — Top camera | Top camera, Mako G-125B | Ethernet 2 | ID matched |
| Side — Side camera | Side camera, Mako G-125B | Ethernet 3 | ID matched |

The operator reported that both cameras were powered on and free, with the CT400 powered off, and that there were no duplicate camera IDs or serial numbers. Full serial numbers and private network/laboratory settings are not included. This run establishes camera discovery only; it does not establish camera opening, feature compatibility, frame delivery, resource release after streaming, or IOPanel workflow behavior. The earlier immediate discovery returned no devices. Whether the difference was caused by the ten-second wait or by the ordinary Windows Command Prompt versus Codex execution environment remains unknown.

### Physical camera run — Gate B2 Top streaming

**Date:** 2026-09-27 (operator-reported run; exact time not provided). **Result: PASS for the Top camera only.** The operator ran `tools/vimba_b2_top_stream.py` from ordinary Windows Command Prompt using Python 3.12.8 x64, VmbPy 1.0.5, and VmbC 1.0.6. After the ten-second VmbSystem discovery wait, the script selected the configured Top camera, identified as an Allied Vision Mako G-125B. It did not open the Side camera. The CT400 remained powered off; neither CT400 nor the IOPanel GUI was contacted/launched.

Before streaming, the script read AcquisitionMode `Continuous`, TriggerMode `Off`, Exposure `16955 µs`, Gain `30.0`, ExposureAuto `Off`, GainAuto `Off`, PixelFormat `Mono8`, dimensions `1292 × 964`, and reported frame rate `30.335204 fps`. It performed no feature writes. It received ten complete frames with distinct, increasing frame IDs 1–10 and increasing arrival timestamps. The last frame arrived 0.375 seconds after acquisition started. Sampled mean intensities were approximately 4–7; there were no incomplete frames, callback errors, or requeue errors.

`stop_streaming()` succeeded. The camera and VmbSystem contexts both exited normally, with no cleanup errors. The script reported PASS. No raw frames or full camera serial numbers were recorded or committed. This establishes only the standalone Top-camera streaming path; it does not establish Side-camera streaming or IOPanel's physical camera workflow.

The 15-second deadline bounds the asynchronous frame-collection wait; the observed ten-frame collection completed in 0.375 seconds. It is not an absolute wall-clock timeout for synchronous SDK open/start/stop/shutdown calls, for which VmbPy 1.0.5 provides no caller-supplied timeout. No force-termination was used.

### Gate B3 — Side camera controlled streaming

The Side-only diagnostic `tools/vimba_b3_side_stream.py` was prepared from the validated B2 procedure and remains restricted to the Side camera; the original Top diagnostic is unchanged. It waits ten seconds after entering VmbSystem, reads the existing camera settings without feature writes, requires `AcquisitionMode=Continuous` and `TriggerMode=Off`, targets ten complete frames within a 15-second frame-collection deadline, requeues callback frames, and explicitly stops streaming and exits both contexts. The 15-second deadline bounds frame collection only; synchronous SDK operations have no guaranteed timeout.

**Physical run — 2026-09-27, operator-reported. Result: PASS for Side camera streaming only.** The approved device was the Side camera, Allied Vision Mako G-125B. The run used Python 3.12.8 x64, VmbPy 1.0.5, VmbC 1.0.6, and the ten-second discovery wait. Before streaming, the read-only inspection reported AcquisitionMode `Continuous`, TriggerMode `Off`, exposure `6180 µs`, gain `30`, PixelFormat `Mono8`, dimensions `1292 × 964`, and frame rate approximately `30.335 fps`.

Ten complete frames arrived with distinct IDs and increasing timestamps; the last arrived 0.359 seconds after acquisition start. Sampled mean intensities were approximately 3.89–6.88. There were no incomplete frames or callback/requeue errors. `stop_streaming()` succeeded; the camera and VmbSystem contexts exited normally; no cleanup errors were reported. The final diagnostic result was PASS. No full serial number, raw frame, private configuration, or network settings are recorded. This validates only standalone Side-camera streaming; it does not establish IOPanel's camera workflow or CT400 behavior.

### Gate B4 — IOPanel application workflow with simulated CT400

**Result: PARTIAL.** The application startup, both configured camera open/start paths, and normal resource shutdown are verified from the operator-provided B4 log. The complete GUI image/FPS workflow and post-run camera feature state are not verified. Physical CT400 validation remains deferred.

**Run record — 2026-09-27, local time (Europe/Rome).** IOPanel 0.3.0 was launched with system Python 3.12.8 x64, VmbPy 1.0.5, VmbC 1.0.6, Vimba X 2024.1.0.3916, and the private B4 configuration selecting simulated CT400 plus Vimba for the exact approved Top and Side IDs. The application checkout was `a41e06ac77ab521de6cafc6c571a6e562c1550a9`, the last known checkout before the log's 20:12:48 start; the log itself does not contain a Git SHA. No private config path or contents are included here.

The log records MainWindow initialization and event-loop start, then successful VmbSystem entry. `CT400InitWorker` selected `DummyCT400`; it did not initialize the physical CT400. Camera logs identify the Top and Side cameras. For each, the log records successful device open, successful writes of AcquisitionMode `Continuous`, TriggerMode `Off`, ExposureAuto `Off`, and GainAuto `Off`, selection of PixelFormat `Mono8`, and streaming start. Both persistent conversion workers were created and started.

For each panel, the log records `_display_converted_image` receiving a first non-null pixmap with dimensions 1292 × 964 and then setting the panel aspect ratio. This verifies an initial converted-pixmap path reached the panel code, but it is not operator visual confirmation and does not establish sustained display, measured FPS, or simultaneous frame continuity. The log contains no Gamma value/range readback and no post-run feature readback. Gamma outcome and post-run camera settings remain unverified.

The operator manually closed the GUI. The log records successful `stop_streaming()` for both cameras, successful camera device closes, successful VmbSystem exit, and application shutdown completion, with no reported cleanup errors. The only warning-level messages in the file are the expected DummyCT400 notices. The file logger does not capture every possible Qt stderr warning.

The B4 private configuration used here is separate from the operational `config.ini` and the earlier private validation configuration. Source review confirms that its simulation branch returns a `DummyCT400` before CT400 DLL lookup or construction. The piezo startup-discovery call remains commented out; no scan, laser, CT400, or piezo operation is part of this record.

### Gate B4 post-run camera settings comparison (prepared; NOT RUN)

`tools/vimba_b4_postrun_settings.py` is a read-only diagnostic prepared for separate authorization. It waits ten seconds after VmbSystem entry, verifies and opens only the exact approved camera IDs sequentially, and reads AcquisitionMode, TriggerMode, exposure, gain, auto modes, PixelFormat, Width, Height, frame rate, and Gamma value/range. It performs no writes, starts no stream, and acquires no frames. The report compares available values with B2 Top and B3 Side observations and marks settings without a recorded baseline as such; Gamma has no pre-run baseline. Its synchronous SDK calls have no guaranteed timeout. This diagnostic has not been run.

After separate authorization, run it from the repository directory in an ordinary Windows Command Prompt with the installed system Python:

```bat
python tools\vimba_b4_postrun_settings.py
```

### VmbPy and Vimba X compatibility (historical profile analysis)

The laboratory pairing Vimba X 2024.1.0.3916 / VmbC 1.0.6 / VmbPy 1.0.5 completed the B1–B3 diagnostics and the B4 IOPanel startup, camera streaming-start, initial conversion, and clean shutdown path. `pyproject.toml` still declares `vmbpy>=1.1.0`, and `uv.lock` currently selects VmbPy 1.2.2. Allied Vision's VmbPy release notes identify 1.1.0 as the change that includes Vimba X library dependencies in the wheel; the 1.0.5 laboratory binding is supplied with its installed SDK. [Allied Vision VmbPy release notes](https://github.com/alliedvision/VmbPy/releases)

Recommended strategy: document supported VmbPy/VmbC/Vimba X pairings as tested profiles; let the lab SDK provide its matching binding instead of upgrading the operational system Python; and keep any newer wheel-based development profile separate. Add hardware-independent camera-adapter compatibility tests for each supported binding profile. This B4 record verifies one bounded application path under 1.0.5 but does not establish every camera-control feature/API or long-duration compatibility. No SDK installation was changed.

### Qt resource and stylesheet investigation

The working checkout has `resources/resources.qrc` with 16 references to `resources/icons/*.svg`; none of the referenced files, an `icons` directory, or `resources_rc.py` is present in the laboratory working tree. Current `main` tracks no SVG assets or generated resource module. The broad `.gitignore` rule `*.svg` would ignore SVG files placed in the tree. A separate legacy `origin/master` history contains an older set of 11 icons and a generated module, but it is a separate history and lacks five names referenced by the current qrc; it is not evidence that the current artwork is available or suitable. The qrc is not imported or referenced by current Python source, so the missing bundle did not block this B4 startup. Whether the current icons were accidentally omitted or intentionally left unused cannot be established from the present checkout.

For reproducible use, first decide whether this qrc is still needed. If retained, restore the intended SVG sources as tracked assets with narrow `.gitignore` exceptions, generate the ignored Python output with `pyside6-rcc resources/resources.qrc -o resources_rc.py`, and import that generated module before any `:/icons/...` use. Add a packaging check that validates all qrc paths, runs the compiler, imports the generated module, and checks representative resource aliases. Generation was not attempted locally because all 16 source files are absent; the command is based on Qt for Python's documented interface and has not yet been validated against this checkout's missing inputs. [Qt for Python `pyside6-rcc` documentation](https://doc.qt.io/qtforpython-6/tools/pyside-rcc.html)

The B4 application log has no stylesheet parse warning, though its file logger does not capture all Qt diagnostics. An independent offscreen QApplication reproduced `QtWarningMsg: Could not parse application stylesheet` under PySide6 6.8.1.1 / Qt 6.8.1 when applying the current application stylesheet and creating a widget. Isolation identified the CSS custom-property declarations (`--primary-*`, etc.) inside the emitted `:root` block in the application stylesheet; Qt QSS does not support these declarations, as the adjacent source comment itself notes. Keeping the block's ordinary `border` declaration while removing the custom-property declarations suppresses the warning; replacing the declarations in memory confirmed the cause. No production stylesheet was changed. The narrow regression test should apply the app stylesheet, create a widget, capture Qt messages, and assert no stylesheet parse warning. Follow-up issues: #27 resource bundle, #28 stylesheet warning, and #29 SDK compatibility.

## CT400 Programming Guide lifecycle correction — Issue #22

**Source:** Yenista *CT400 Programming Guide CT400_PG_1.4v1.0* (applies to CT400 library v1.4.x / DSP 1.12) and *CT400 User Manual CT400_UM_3.8v1.0*. The installed laboratory DLL was previously statically identified as Windows x64, version 1.4.1.0. No DLL was loaded and no physical operation was performed for this software correction.

The Programming Guide defines `CT400_ScanStart` as starting a scan (`0` success, `-1` failure), `CT400_ScanStop` as the user Stop action corresponding to the official CT400 GUI's red Stop button after Start (`0` success, `-1` failure), and `CT400_ScanWaitEnd` as waiting for the scan to finish and returning errors or warnings. `ScanWaitEnd` is not a polling API: `0` is success; positive values are result codes; no in-progress status is documented. Code `1` means measurement cancelled by the user, and the guide says this occurs only after `CT400_ScanStop` is called by the user. Codes `2`–`5` are documented scan errors: data exchange with DSP, wavelength referencing, switch failure, and DSP communication, respectively. Initialization error `-1001` applies to `CT400_Init` compatibility handling, not `ScanWaitEnd`.

Documented warnings are `100`–`104`, `106`, `108`–`115`, `117`–`124`, and `999`; omitted numbers such as `105`, `107`, and `116` are not assigned meanings. Warning `999` says the scan is performed after sampling resolution is adjusted. Warnings are retained and surfaced separately from fatal errors; completed data is still retrieved for documented warning results. The raw integer return and vendor `tcError` string are preserved, including unknown values. No meanings are inferred for undocumented codes.

The scan worker now performs configure → one `CT400_ScanStart` → one blocking `CT400_ScanWaitEnd` → classify result → retrieve data for success/warnings → selected-input disable in `finally` → finish. It calls `CT400_ScanStop` only for a user stop request, at most once; it does not call Stop after normal completion. The UI remains busy until the wait returns and cleanup/worker shutdown finish. Closing the application during an active scan requests Stop and defers close; it neither closes CT400 beneath `ScanWaitEnd` nor terminates the scan thread. A Stop/WaitEnd completion race follows the actual WaitEnd result.

The guide establishes this Stop and cancellation result workflow, but does not explicitly document a general same-handle DLL thread-safety or reentrancy guarantee. The application issues the documented Stop request while the worker is blocked in WaitEnd; this native concurrency point remains a vendor-contract caveat. Physical behavior, safe-state indication, and hardware cleanup still require supervised laboratory qualification. No physical CT400 validation is claimed by these software changes.

## CT400 selected-input cleanup — Issue #23

The Yenista CT400 Programming Guide 1.4 documents `CT400_CmdLaser` with an explicit `LI_1`–`LI_4` input argument and `ENABLE`/`DISABLE` mode. `CT400_Close` is documented as connection and allocated-resource cleanup. The guide does not specify that closing disables one or all laser inputs, stops an active scan, or establishes an optical safe state. The vendor example explicitly disables its selected input before calling `CT400_Close`.

IOPanel now keeps laser policy above the native resource wrapper. `CT400.close()` only releases the native handle and is idempotent. Scan cleanup disables the scan panel's selected input; monitor stop and cleanup disable the monitor panel's selected input; connected application shutdown disables the configured connection input using the configured safe wavelength and power before releasing the handle. A successfully disconnected CT400 skips that redundant shutdown laser command while still releasing an initialized native handle. No all-input shutdown behavior is assumed.

This is a software-only change. The selected-input calls and resource-release ordering are covered by fake-backed tests. Physical laser disable behavior, optical output state, and any effect of `CT400_Close` on hardware remain unverified; Issue #23 remains open pending supervised physical safe-state validation.

### Offline test warning follow-up

One earlier full-suite run emitted a `RuntimeWarning` from pytest-qt 4.4.0 at `pytestqt/wait_signal.py:741`: it could not disconnect `MultiSignalBlocker._quit_loop_by_timeout` from `timeout()`, during `test_scan_failure_is_reported_while_camera_keeps_streaming`. That run still passed all 29 tests. On 2026-09-27 the documented offline suite passed with 29 tests using `uv run --offline --no-sync pytest -q -p no:cacheprovider --basetemp=<workspace-local-temp>` with `UV_CACHE_DIR` also pointed to a workspace-local temporary directory. An initial attempt using the shared uv cache was denied by the sandbox; no sync/install was performed. Three additional full-suite runs under system Python each passed 29 tests, and eight repetitions of the implicated test each passed; none reproduced the warning. No production code, test assertions, warning filters, or hardware code were changed. Current evidence indicates an intermittent pytest-qt/Qt signal-cleanup warning; its cause is not established, so it remains a low-confidence flake rather than a confirmed defect.


## Completed physical camera qualification: preferred candidate

**Result: PASS for the tested IOPanel camera path.** The isolated project/candidate environment was Windows x64 with Python 3.12.8, Allied Vision Vimba X 2026-2, VmbPy 1.2.2, VmbC 1.3.1 and VmbImageTransform 2.3. The physical devices were two Allied Vision Mako G-125B cameras, Top and Side, using the Vimba/Vimba X backend.

Both cameras passed standalone physical streaming. Normal IOPanel startup with both cameras and DummyCT400 opened and streamed both cameras, displayed converted frames, and shut down cleanly with VmbSystem released. Manual Exposure and Gamma controls were exercised successfully on each camera. One-shot Auto Exposure succeeded on both; one-shot Auto Gain also succeeded on both after the Vimba X update. VmbPy 1.2.2 / VmbC 1.3.1 worked with the existing Vimba X installation, and the physical dual-camera path continued to work after Vimba X was updated to 2026-2. Continuous dual-camera operation was observed during normal operator use; no separately timed formal endurance gate was run.

The final isolated GenTL test removed all legacy Allied Vision Vimba 6 transport-layer paths from `GENICAM_GENTL64_PATH`, kept Basler Pylon paths unchanged, and retained `C:\Program Files\Allied Vision\Vimba X\cti`. Both cameras opened, streamed, displayed frames and closed normally. This demonstrates practical independence from the legacy Vimba 6 GenTL transport layers for the tested IOPanel camera path. It does not establish that every possible DLL-loading mechanism was forensically excluded. Vimba 6 may remain installed for rollback/reference; its GenTL transport layers were not required by this tested camera path.

CT400 was intentionally simulated throughout this camera qualification. These camera results do not validate physical CT400 operation under the candidate environment. Physical CT400 validation remains a separate activity.

Earlier B1-B4 and compatibility entries in this report record historical, narrower observations made on the former Vimba X 2024.1.0.3916 / VmbC 1.0.6 / VmbPy 1.0.5 profile. They are retained as historical/rollback evidence and are superseded by this completed qualification wherever they describe current preferred-profile status or say that camera controls, display, or VmbPy 1.2.2 physical validation remain unverified.
