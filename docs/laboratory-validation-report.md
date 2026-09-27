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

The local docs do not establish whether `ScanWaitEnd` is a blocking call in the operational sense required by Issue #22, whether `ScanStop` can interrupt it safely from another thread, or what return codes are safe to treat as cancellation. Static review also confirms `CT400.close()` issues a disable request for `LI_1` before `CT400_Close`, while scan cleanup disables the selected input. The vendor header does not say that disabling `LI_1` disables other inputs. No laser commands were sent to test this behavior.

## Physical gates

| Gate | Result | Notes |
| --- | --- | --- |
| A — Offline environment/configuration | PASS (offline checks only) | Hardware-independent tests and config parsing passed. The site/operator approval record and approved scan limits are not available in this offline audit. |
| B — Camera discovery and controlled streaming | PARTIAL | Gate B1 physical discovery PASS on 2026-09-27; Gate B2 streaming NOT RUN. See the separate run record below. |
| C — CT400 identity, connection, safe idle | NOT RUN | No CT400 initialization or device query was initiated. Instrument identity, firmware, connection and independent safe-state indication remain unverified. |
| D — Operator-approved CT400 scan | NOT RUN | No scan settings were provided or used; no laser command or scan was issued. |
| E — Supervised cancellation/shutdown | NOT RUN | No active scan or cancellation was initiated. Safe interrupt and shutdown behavior remain unresolved. |
| F — Concurrent scan and camera streaming | NOT RUN | No hardware acquisition was initiated. |
| G — Final hardware resource release | NOT RUN | No device resources were opened. |

At the time of this initial offline audit, no physical hardware validation had been completed. No simulated result is represented as physical evidence. No hardware or driver failure was observed in that audit. No production wrapper changes were made.

## Remaining questions and next action

- Obtain vendor confirmation or a matching versioned API reference for `ScanWaitEnd` blocking/timeout behavior, `ScanStop` interruption and cross-thread safety, and all scan result codes.
- Obtain vendor confirmation of the installed header/DLL pairing and whether `CT400_Close` or any `CT400_CmdLaser` disable operation affects other inputs.
- Before live validation, have the responsible operator approve device-specific settings and safe-state indications, then proceed through the existing protocol one gate at a time. Gates B–G each require separate authorization before hardware operation.
- Keep cancellation and close-during-acquisition tests blocked until a safe vendor-supported stop procedure is independently established. Do not remove the current forced-termination fallback or change laser cleanup based solely on this static audit.

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

Created local-only `config.camera-validation.local.ini` as a copy of the existing config with `ct400_backend = simulation` added. Offline model parsing confirmed DummyCT400 is selected and the two camera configurations are unchanged. The original `config.ini` was not edited. The temporary file contains private laboratory camera configuration and is intentionally untracked and excluded from publication.

Static source review confirms `CT400InitWorker.run()` checks the `simulation` backend before DLL lookup or `CT400(...)` construction and returns `DummyCT400`. Thus, if this temporary file is passed to IOPanel, the CT400 initialization worker cannot load or call the physical CT400 DLL. This proof applies to that worker path; it does not make application startup a discovery-only action, because enabled Vimba cameras open during startup.

### Gate B1/B2 state

### Physical camera run — Gate B1 discovery

**Date:** 2026-09-27 (operator-reported run; exact time not provided). **Result: PASS for discovery only.** The operator ran the prepared `tools/vimba_b1_diagnostic.py` in ordinary Windows Command Prompt, using system Python 3.12.8 x64 at `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, VmbPy 1.0.5, VmbC 1.0.6, and Vimba X GigE Transport Layer 1.10.0. The diagnostic used a ten-second discovery wait and reported normal VmbSystem context exit.

| Configured camera | Discovered identity | Interface | Configuration comparison |
| --- | --- | --- | --- |
| Top — `DEV_000F315B9CE1` | `DEV_000F315B9CE1`, Mako G-125B | Ethernet 2 | ID matched |
| Side — `DEV_000F315BA8F9` | `DEV_000F315BA8F9`, Mako G-125B | Ethernet 3 | ID matched |

The operator reported that both cameras were powered on and free, with the CT400 powered off, and that there were no duplicate camera IDs or serial numbers. Full serial numbers and private network/laboratory settings are not included. This run establishes camera discovery only; it does not establish camera opening, feature compatibility, frame delivery, resource release after streaming, or IOPanel workflow behavior. The earlier immediate discovery returned no devices. Whether the difference was caused by the ten-second wait or by the ordinary Windows Command Prompt versus Codex execution environment remains unknown.

### Gate B2 — Top camera controlled streaming

**Result: NOT RUN.** The B2 diagnostic script is prepared at `tools/vimba_b2_top_stream.py`, restricted to the approved Top ID `DEV_000F315B9CE1`, and has only received static review and a syntax/CLI-help check. It has not entered VmbSystem or opened a camera. The current Codex execution path is sandboxed and is not the ordinary Windows Command Prompt used for Gate B1, so no physical streaming attempt was made. No frame results or physical cleanup observations are available.

The script reads AcquisitionMode, TriggerMode, exposure, gain, pixel format, dimensions, frame rate, and available auto-exposure/auto-gain state before acquisition. It refuses to stream unless the existing mode is Continuous and trigger mode is Off; it performs no feature writes. The VmbPy 1.0.5 source documents `with camera` context management, asynchronous `start_streaming(handler=...)`, `queue_frame(frame)` as the callback's last frame operation, and `stop_streaming()` for cleanup. The frame-arrival wait is deadline-bounded to 15 seconds. The synchronous VmbPy open/start/stop/shutdown calls expose no caller-supplied timeout in this version; their wall-clock duration is therefore not bounded by the script, and no force-termination is used that could leave acquisition active. These source findings do not establish physical cleanup behavior. Streaming, frame format/intensity, and normal physical cleanup remain unverified.

The current Codex shell is sandboxed and is not the ordinary Command Prompt required for the physical run. To perform the already-authorized Gate B2 in ordinary Windows Command Prompt, run:

```bat
cd /d "%USERPROFILE%\Desktop\IOPanel-lab-validation"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" tools\vimba_b2_top_stream.py --camera-id DEV_000F315B9CE1
```
