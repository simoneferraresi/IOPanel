# Laboratory hardware validation protocol

This checklist is for an authorized operator on the laboratory PC. It is a manual validation procedure; it does not certify or operate hardware automatically. The hardware-independent pytest suite uses simulators and mocks and is not evidence of physical compatibility.

Tracking issue: [#20](https://github.com/simoneferraresi/IOPanel/issues/20).

The preferred camera profile and the supervised CT400 qualification are complete for the scope recorded in the [laboratory validation report](laboratory-validation-report.md). The procedure below remains the controlled workflow for future checks. Earlier camera-only qualification used DummyCT400 and did not validate physical CT400 operation; the later Gate-F run provides separate evidence for concurrent physical CT400 and dual-camera operation.

## Current qualification status

| Gate | Current result | Evidence scope |
| --- | --- | --- |
| A — Offline environment/configuration | PASS | Previously qualified hardware-independent and configuration checks; these are not physical evidence. |
| B — Physical camera path | PASS | Previously qualified preferred Vimba X profile and dual-camera workflow; see the report. |
| C — CT400 initialization, GUI connection/disconnection and safe idle | PASS | Physical CT400 backend initialized; controlled GUI connect/disconnect and clean resource release; independent safe-state indication confirmed. No serial-number identity query or device identifier was recorded. |
| D — Bounded physical scan | PASS | One supervised natural-completion scan at the recorded report settings; full data and independent safe-state confirmation. |
| E — Physical cancellation | PASS | One GUI Stop; blocked WaitEnd returned code 1; cleanup and independent safe-state indication succeeded. Close-while-scan-active was NOT RUN as a standalone test. |
| F — Concurrent CT400 and dual-camera operation | PASS | Two natural-completion physical scans with both Vimba camera feeds visibly live before, during and after; cancellation with cameras streaming was NOT RUN. |
| G — Shutdown and return to approved state | PASS | Runtime cleanup completed and operator confirmed approved source, optical path/shutter, detector and camera state. |

These results are specific to the installed/tested setup and the workflows described in the report. Physical success does not establish undocumented vendor guarantees, including general same-handle CT400 DLL thread safety or reentrancy. Formal timed endurance/stress testing was NOT RUN. Gate-F warning 117 was observed in the intentionally uncoupled optical qualification setup; see the report for its exact context. Software tests and simulated-device results remain separate from physical evidence.

## Safety and scope

- Obtain the site's normal instrument and laser authorization before starting. Follow the CT400, tunable laser, detector, camera and optical-table procedures in force at the lab.
- Use only scan limits, optical power, input ports, speed, detector selection and camera settings explicitly approved by the responsible operator for the installed setup. Record those approved values before a scan. Do not infer a safe range from the repository's example or default INI values.
- The application's `config.ini` defaults are example configuration, not validated laboratory limits. Make a lab-local copy and check every instrument and camera entry before launch.
- Launching the actual application initializes the configured CT400 and starts initialization/acquisition for enabled physical cameras. Do not launch it as a passive discovery utility. The application's camera discovery dialog also calls the Vimba discovery API and must be used only when authorized.
- Do not run a physical scan from a diagnostic script. The documented `app.py` entry point starts the GUI; measurements begin only after an operator uses the scan controls. Keep a qualified operator present for every live scan.
- Never use unplugging, process termination or a forced worker termination as a routine way to stop an active laser scan. Follow the instrument's approved emergency procedure if normal stop does not complete.
- Keep completed records in the lab's controlled location. Do not commit device serials, private addresses, operator names or completed live configuration files to the repository.

## CT400 software behavior established by the Programming Guide and source review

Yenista CT400 Programming Guide 1.4 is the source for the API behavior below. It applies to CT400 library v1.4.x / DSP 1.12. This section describes documented software semantics and current IOPanel behavior; it is not physical qualification.

- Physical startup loads `CT400_lib.dll` and calls `CT400_Init`; a successful call provides the handle used by the wrapper. The physical backend remains the default unless `ct400_backend = simulation` is explicitly configured.
- `CT400_ScanStart` starts a scan. `CT400_ScanWaitEnd` is a blocking wait-for-completion operation and IOPanel calls it once per scan. Its result is 0 for success, 1 for user cancellation following `CT400_ScanStop`, and 2–5 for documented fatal measurement errors. Documented 100-series and 999 results are warnings, not automatically fatal errors. Unknown raw results are retained without assigning an invented meaning.
- `CT400_ScanStop` is the documented user Stop operation. The cancellation result is observed when the blocking `CT400_ScanWaitEnd` returns code 1. The current IOPanel lifecycle is configure → `CT400_ScanStart` → one blocking `CT400_ScanWaitEnd` → classify result → retrieve data when appropriate → selected-input cleanup. A normally completed scan does not issue an unnecessary `CT400_ScanStop`.
- The application does not force-terminate an active CT400 scan thread. On close during a scan, IOPanel requests cooperative Stop and defers close until WaitEnd returns and worker cleanup completes. Gate E physically confirmed the tested GUI Stop → blocked WaitEnd → code-1 workflow on the installed setup. The Programming Guide does not document a general same-handle DLL thread-safety/reentrancy guarantee.
- `CT400_CmdLaser` addresses an explicit laser input `LI_1` through `LI_4`. `CT400.close()` is resource/connection cleanup only: for a live handle it calls `CT400_Close`, with no hidden laser command and no hard-coded `LI_1` policy. The Programming Guide does not say that `CT400_Close` disables a laser or establishes an optical safe state.
- Operational laser cleanup belongs to the workflow that selected the input: ScanWorker disables its selected scan input; power monitoring disables its selected monitor input; successful GUI Disconnect disables the configured CT400 input; application shutdown explicitly disables the configured input when required by the confirmed connection state, then releases the native handle. A successful earlier Disconnect avoids an unnecessary duplicate disable.
- The application serializes CT400 workflows using exclusive states `IDLE`, `CONNECTING`, `DISCONNECTING`, `SCANNING`, `MONITORING` and `ALIGNMENT`. `ALIGNMENT` covers fine alignment, spiral alignment and 2D mapping. Only one workflow owns the CT400 at a time. Shutdown waits for the owner to finish; scan uses ScanStop/WaitEnd/cleanup, monitoring stops fetching and disables its selected input, connection operations finish before shutdown resumes, and alignment requests cooperative `AlignmentWorker.stop()` and waits for selected-input cleanup and `operation_finished`.

This serialization is a conservative application policy because the Programming Guide does not document general same-handle reentrancy or thread safety. It does not claim the hardware or DLL cannot support concurrency. Physical results for the specific qualified workflows are recorded above and in the report; software-only or simulator results do not establish physical compatibility or optical safe state.

## Record sheet

Complete before testing. Use the commit actually tested, and record values as reported by the host, driver/vendor tools and instrument. Redact serials and private network identifiers in any repository issue or PR.

| Field | Recorded value |
| --- | --- |
| Date/time and operator / authorization reference | |
| IOPanel git commit and working-tree state | |
| Windows edition/build | |
| Python version and architecture (32/64 bit) | |
| `uv` version and `uv.lock` revision | |
| CT400 model, serial (lab record only), firmware | |
| CT400 DLL path/version, file architecture, vendor runtime | |
| Laser source model, firmware, connection/interface | |
| Vimba X version and installed transport layer(s) | |
| VmbPy version and Python environment | |
| Camera model, serial (lab record only), firmware, transport | |
| Camera pixel format and frame dimensions | |
| Approved scan start/end, resolution, speed, power/unit, input port, detector | |
| Approved camera exposure/gain and other changed controls | |
| Log path and report/evidence path (controlled lab storage) | |

Capture versions before opening IOPanel where possible, using vendor utilities that do not acquire or control the device. `uv.lock` and project metadata identify Python dependencies but do not establish installed CT400 DLL, Vimba X, transport-layer, firmware or hardware versions. Do not open a device concurrently from a vendor utility and IOPanel.

## A. Gate A — Offline environment verification (no instruments connected)

Run these on the target PC before connecting or enabling equipment:

```powershell
git status --short --branch
git rev-parse HEAD
python -c "import platform, struct, sys; print(sys.version); print(platform.platform()); print('Python architecture: {}-bit'.format(struct.calcsize('P') * 8))"
uv --version
uv sync --extra test
uv run pytest -q
```

Expected: the recorded commit is the intended release; the environment resolves from the committed lock file; all hardware-independent tests pass. This test command must run without a CT400 DLL, Vimba X, cameras or CT400 connected. Investigate any failure; do not skip a test to produce a pass.

Validate the lab-local INI without constructing a window or opening devices:

```powershell
uv run python -c "from pathlib import Path; from app import load_raw_config_from_ini; from config_model import AppConfig; AppConfig.from_ini_dict(load_raw_config_from_ini(Path('config.lab.ini'))); print('Configuration is valid')"
```

Create `config.lab.ini` only if it does not already exist, by copying the checked-in example and reviewing every value; never overwrite an existing lab configuration. For a repository-only smoke check, the same command can target `config.ini`.

This imports application modules but does not instantiate `MainWindow` or initialize an instrument. Confirm the intended backend explicitly. For physical work, omit `ct400_backend` only if intentionally relying on the backward-compatible default `physical`; never set a simulated backend for a physical validation run. Camera sections default to `vimba`; simulation must be explicitly selected and must not be used to pass a physical test. Check camera identifiers and enabled flags before launch. Do not print or attach the full lab INI to a public issue.

**Pass:** clean dependency/test result; configuration is valid and matches the intended physical devices; operator-approved limits are recorded. **Fail:** unresolved dependency/architecture mismatch, invalid/ambiguous config, unexpected simulation selection, or any unreviewed test failure. Stop before connecting/operating equipment on failure.

## B. Gate B — Passive camera discovery and streaming (no CT400 laser operation)

Prerequisites: Gate A passes; camera checks and device opening are authorized; CT400 laser operation is prohibited for this gate. Since normal MainWindow startup initializes the configured CT400, use the explicitly selected CT400 simulator for this gate (`ct400_backend = simulation`) while leaving the intended physical camera backend explicitly set to `vimba`. This tests the physical camera through the normal GUI pipeline without connecting to or commanding the CT400. The camera discovery dialog is passive, but still opens the Vimba discovery API and requires authorization. Never rely on implicit fallback.

1. With the application closed, record Vimba X, VmbPy, transport-layer and camera versions. Open the discovery dialog and verify the authorized camera identities. Do not start a scan, issue CT400 laser commands or enable a laser during this camera-only gate.
2. Start the application with a temporary, reviewed INI selecting CT400 simulation and Vimba for the enabled camera(s). Confirm the CT400 is visibly identified as simulated and the camera as physical.
3. Confirm frames reach and update the real camera panel. Check orientation against an asymmetric target, dimensions, pixel format, intensity scaling and only operator-approved camera controls.
4. Stop acquisition and close normally; verify the camera can be reopened and no process retains it.

**Pass:** discovery and repeated physical frames work, identity/backend labels are unambiguous, and camera resources release. **Fail/stop:** any CT400 physical connection/laser command occurs, simulator/physical identity is unclear, camera frames are stale or malformed, or close fails. Do not proceed to Gate C until the camera-only run is complete and its configuration is closed.

## C. Gate C — CT400 driver, identity, connection and safe idle

Prerequisites: Gate A passes; qualified operator present; camera backend disabled; approved CT400 manual/site stop procedure and independent safe-state indication are available. No scan is started in this gate.

1. With scan idle and laser in the approved safe state, verify the configured `CT400_lib.dll` exists, its architecture matches Python, and vendor-required runtimes are installed. Record versions; do not copy DLLs from an unverified machine.
2. Start IOPanel using the reviewed lab-local config and the normal GUI. Startup calls the CT400 initialization path (`CT400_Init`); record whether connection succeeds and the exact status/log message. Confirm it is the expected physical CT400 backend, not a `SIMULATED`/Dummy status. If a vendor-approved model/serial identity method is available, record its result in controlled lab storage; do not infer identity from a native handle or guess an instrument address.
3. Keep the scan idle. Verify the device is in the operator-confirmed safe state using the site's independent indication. `CT400.close()` is resource release only; do not infer laser state from `CT400_Close`.
4. If needed to qualify the configured laser interface, perform the normal GUI Connect/Disconnect operation only with operator authorization and no scan active. Do not perform a laser-enable operation merely to pass this gate.
5. After safe state is independently confirmed, close IOPanel normally. Confirm native resource release using supported driver/vendor indication. Do not use Task Manager as normal cleanup.

**Pass:** correct DLL/runtime/bitness and expected physical backend initialized; authorized controlled connection/disconnection succeeds where performed; safe idle state is independently verified; normal close releases the connection. Record model/serial identity only when actually queried. **Fail:** any simulated backend presented as physical, unclear backend/state, or unclean resource release. Stop live testing and follow site procedure on any safety concern.

## D. Gate D — Operator-approved, low-risk CT400 acquisition

Prerequisites: Gate C passes; the responsible operator supplies and approves all site-specific scan limits, laser power, input port, detector and speed; the approved manual stop procedure and independent safe-state check are immediately available. Do not copy example/default INI values as operating limits.

Use the physical backend explicitly (or omit the setting only when intentionally relying on the documented physical default); disable cameras for this gate. Before clicking Scan, confirm the GUI reflects the operator-approved boundaries, resolution, speed, power/unit, input port and detector. Start one bounded scan through the GUI only after the operator reviews every value. Record these settings and confirm the plot displays wavelength and measured-power data. Compare returned sample count, wavelength boundaries/order and units against the matching CT400 manual and independent checks; the simulator's endpoint convention is not evidence about the physical DLL. No measurements start automatically.

**Pass:** operator-approved settings are displayed and recorded, the scan completes and its data match the vendor-defined semantics and independent checks. **Fail/stop:** unexpected output/state, ambiguous backend, boundary/count mismatch, communication issue or any operator concern. Do not repeat or widen a scan to diagnose an anomaly without new operator approval.

## E. Gate E — Supervised scan cancellation and shutdown

Prerequisites: Gate D passes; the lab operator approves one bounded cancellation test; approved settings and an independent safe-state indication are available. The remaining uncertainty is the physical DLL's behavior when `CT400_ScanStop` is called while `CT400_ScanWaitEnd` is blocked. Do not deliberately test overlapping calls outside this supervised sequence.

1. Run one separately approved, supervised bounded scan. Click the GUI Stop control once. Observe that IOPanel issues `CT400_ScanStop`, waits for the blocking `CT400_ScanWaitEnd` to return, and reports the vendor cancellation result (code 1 when cancellation is confirmed).
2. Confirm the selected input's safe state using an independent indication. Confirm the application remains responsive, selected-input cleanup completes, and no forced thread termination occurs.
3. If also qualifying close-during-scan, let the normal deferred-close path finish only after WaitEnd and cleanup complete. Confirm native resource release afterward. Do not deliberately interrupt communications or disconnect devices during emission.

**Pass:** Stop is issued once, WaitEnd returns the documented cancellation behavior, the selected input is independently confirmed safe, the application remains responsive, no forced termination occurs, and resources release cleanly. **Fail/stop:** Stop/WaitEnd does not complete, code/result is unexpected, safe state cannot be confirmed, or resource release is uncertain. Follow the site's emergency procedure; preserve logs without repeating the test.

## F. Gate F — Concurrent physical CT400 scan and camera streaming

Prerequisites: Gates A–E pass; camera and CT400 separately validated; only previously approved CT400 settings are used; operator supervises the entire run.

1. Use a reviewed configuration with physical CT400 and Vimba selected explicitly. Record camera SDK/transport/camera firmware and CT400 DLL/device/firmware versions.
2. Confirm identity and continuously updating frames before scanning. Start one previously approved scan via the GUI.
3. Observe and record multiple distinct physical frames arriving while the physical scan is visibly active (with times/evidence that establish overlap), then confirm scan wavelengths/powers appear in the real plot while camera streaming continues.
4. Only if the operator has approved and Gate E cancellation passed, repeat cancellation with camera streaming and check that cancellation does not stop camera frames and leaves selected laser input independently safe.
5. Stop scan, verify safe state, stop camera streaming and close normally. Closing the application during an active scan is outside this gate unless separately approved under Gate E.

**Pass for the natural-completion concurrency qualification:** physical devices are unambiguous; camera frames overlap in time with the scan; the plot receives scan data; cameras remain operational after scan completion; resources release. The optional cancellation-with-cameras extension is a separate scope item and must be recorded as `NOT RUN` unless performed. **Fail/stop:** frozen camera, missing data, ambiguous provenance, any unsafe laser state or unclean shutdown.

## Negative tests and recovery

Only conduct fault/recovery scenarios with an approved, instrument-specific procedure and no unapproved emission. Do not create a fault merely to complete a checklist.

For approved negative cases, include absent camera at startup, unavailable Vimba X/transport, CT400 unavailable while confirmed safe, and mixed camera initialization if the configuration supports it. Prefer a separate controlled software image for missing drivers. Never unplug a live instrument or induce CT400 communication loss during a scan without written/site-approved procedure. For each test verify affected-device attribution, no simulated data presented as physical, unaffected subsystem behavior and approved recovery.

**Pass:** every approved failure is visible and attributed to the affected device; no simulation is presented as physical data; unaffected subsystems remain usable where expected; recovery follows the approved procedure. **Fail/stop:** ambiguous data source, hidden error, stale device state, unsafe state or unrecoverable failure.

## G. Gate G — Final resource release and application shutdown

Prerequisites: no unresolved instrument error; any scan has completed or has been cancelled using the procedure validated at Gate E; the responsible operator confirms the safe-state indication before close.

1. Verify all CT400 operations are IDLE and independently confirm the selected input's safe state. Do not treat successful `CT400_Close` as proof that optical output is safe.
2. Stop camera acquisition and close IOPanel normally. Verify there is no active CT400 owner and that native resources release cleanly. Do not force terminate while any CT400 operation could be active.
3. Return source, shutter, optical path, detector and camera controls to the operator-approved state; finish lab checkout.
4. Complete the record sheet and use controlled lab storage. Attach only redacted logs/evidence to internal issue records.

**Pass:** all operations stopped, selected output independently verified safe, devices released and process exited cleanly. Otherwise fail and use the lab escalation procedure.

## Result record

For each Gate A–G record `PASS`, `FAIL`, or `NOT RUN`, date/operator reference, observations, expected versus actual behavior, redacted log references and separately filed software defects. Record the negative/recovery cases separately as `PASS`, `FAIL`, or `NOT RUN`. State explicitly that simulated/driver-free checks are not hardware results. Record any test stopped early for safety or reliability reasons. Do not advance past a failed prerequisite gate.
