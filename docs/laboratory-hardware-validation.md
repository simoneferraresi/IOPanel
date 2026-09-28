# Laboratory hardware validation protocol

This checklist is for an authorized operator on the laboratory PC. It is a manual validation procedure; it does not certify or operate hardware automatically. The hardware-independent pytest suite uses simulators and mocks and is not evidence of physical compatibility.

Tracking issue: [#20](https://github.com/simoneferraresi/IOPanel/issues/20).

Camera qualification is complete for the preferred profile described in the [laboratory validation report](laboratory-validation-report.md). The procedure below remains the controlled workflow for future checks. The qualification used DummyCT400 and does not validate physical CT400 operation, which remains a separate activity.

## Safety and scope

- Obtain the site's normal instrument and laser authorization before starting. Follow the CT400, tunable laser, detector, camera and optical-table procedures in force at the lab.
- Use only scan limits, optical power, input ports, speed, detector selection and camera settings explicitly approved by the responsible operator for the installed setup. Record those approved values before a scan. Do not infer a safe range from the repository's example or default INI values.
- The application's `config.ini` defaults are example configuration, not validated laboratory limits. Make a lab-local copy and check every instrument and camera entry before launch.
- Launching the actual application initializes the configured CT400 and starts initialization/acquisition for enabled physical cameras. Do not launch it as a passive discovery utility. The application's camera discovery dialog also calls the Vimba discovery API and must be used only when authorized.
- Do not run a physical scan from a diagnostic script. The documented `app.py` entry point starts the GUI; measurements begin only after an operator uses the scan controls. Keep a qualified operator present for every live scan.
- Never use unplugging, process termination or a forced worker termination as a routine way to stop an active laser scan. Follow the instrument's approved emergency procedure if normal stop does not complete.
- Keep completed records in the lab's controlled location. Do not commit device serials, private addresses, operator names or completed live configuration files to the repository.

## CT400 software behavior established by source review

This is a static code finding, not a statement about undocumented DLL semantics:

- Physical startup loads `CT400_lib.dll` and calls `CT400_Init`; the wrapper stores the returned handle. A failed initialization raises an application error. The physical backend remains the default unless `ct400_backend = simulation` is explicitly configured.
- A scan sends a disable command to the selected input before setup, calls `CT400_SetScan` and `CT400_SetSamplingResolution`, starts via `CT400_ScanStart`, then polls `CT400_ScanWaitEnd`. Its `finally` path calls `CT400_ScanStop` if start succeeded, then sends disable to the selected input.
- `CT400.stop_scan()` calls `CT400_ScanStop`; it only logs a warning when the result is `-1` and does not propagate that return as an exception. `scan_wait_end()` treats `-1` as a wrapper-call communication error; other negative scan status values are returned and logged for the caller to handle. These interpretations come from repository code; confirm them against the vendor manual for the installed DLL.
- `CT400.close()` attempts `CT400_CmdLaser(LI_1, DISABLE, 1550.0, 1.0)` for every non-null handle, catches a disable-command exception, then calls `CT400_Close`. This is unconditional with respect to the input selected in the UI. The scan worker separately disables its selected input in `finally`. Thus `close()` does not establish that the selected input is safe, and the LI_1 command's effect on other inputs must not be assumed.
- The scan worker's `stop()` only clears a Python flag. The loop checks the flag after `scan_wait_end()` returns; there is no source-level evidence that the vendor wait call is interruptible. MainWindow requests worker stop, waits 500 ms for its QThread, then calls `terminate()` if it still runs. Forced termination can bypass Python `finally` cleanup. Do not use close-during-active-scan or forced termination as a lab procedure until the vendor-supported stop/wait behavior and an independent safe-stop method are confirmed.
- The same close handler closes the CT400 only after scan-thread cleanup. Whether any vendor call can block indefinitely, whether `CT400_ScanStop` is safe concurrently with `CT400_ScanWaitEnd`, and what all return values mean require the matching vendor documentation and/or controlled lab validation.

No authoritative public CT400_lib.dll API manual was located during this review. The names, signatures and return handling below are transcribed from the checked-in Python wrapper, not independently verified vendor specifications. EXFO's [CT400 product page](https://www.exfo.com/en/products/discontinued-products/ct400/) lists the product as discontinued and its service/support end date as March 31, 2023; that page does not document the DLL API.

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
2. Start IOPanel using the reviewed lab-local config and the normal GUI. Startup calls the CT400 initialization path (`CT400_Init`); record whether connection succeeds and the exact status/log message. Confirm it is the physical backend, not a `SIMULATED`/Dummy status. Confirm the expected device using the vendor-approved identity method; do not guess an instrument address.
3. Keep the scan idle. Verify the device is in the operator-confirmed safe state using the site's independent indication. Do not infer that the wrapper's fixed LI_1 close-time command controls the configured/selected input or proves safe state.
4. With no scan active and output confirmed safe, close IOPanel normally. Confirm it exits cleanly and the CT400 handle/connection is released according to supported driver/vendor indication. Review logs for the close-time disable attempt, but do not treat that attempt as independent confirmation. Do not use Task Manager as normal cleanup.

**Pass:** correct DLL/runtime/bitness and physical CT400 identity/connection; independently verified safe idle state; normal close releases the connection. **Fail:** any simulated backend presented as physical, unclear identity/state, or unclean resource release. Stop live testing and follow site procedure on any safety concern.

## D. Gate D — Operator-approved, low-risk CT400 acquisition

Prerequisites: Gate C passes; the responsible operator supplies and approves all site-specific scan limits, laser power, input port, detector and speed; the approved manual stop procedure and independent safe-state check are immediately available. Do not copy example/default INI values as operating limits.

Use the physical backend explicitly (or omit the setting only when intentionally relying on the documented physical default); disable cameras for this gate. Before clicking Scan, confirm the GUI reflects the operator-approved boundaries, resolution, speed, power/unit, input port and detector. Start one bounded scan through the GUI only after the operator reviews every value. Record these settings and confirm the plot displays wavelength and measured-power data. Compare returned sample count, wavelength boundaries/order and units against the matching CT400 manual and independent checks; the simulator's endpoint convention is not evidence about the physical DLL. No measurements start automatically.

**Pass:** operator-approved settings are displayed and recorded, the scan completes and its data match the vendor-defined semantics and independent checks. **Fail/stop:** unexpected output/state, ambiguous backend, boundary/count mismatch, communication issue or any operator concern. Do not repeat or widen a scan to diagnose an anomaly without new operator approval.

## E. Gate E — Supervised scan cancellation and shutdown

Prerequisites: Gate D passes and the lab operator has confirmed the vendor-supported cancellation sequence, expected `CT400_ScanStop`/wait behavior and an independent safe stop before any active scan. Current source contains a forced-termination fallback; do not deliberately provoke it during laser operation.

1. Conduct one separately approved cancellation while an active bounded scan is supervised. Use the GUI cancellation control, observe the scan status, selected-input laser safe state via an independent indication, worker completion and CT400 responsiveness.
2. Do not close the window during an active scan unless the operator has explicitly established that exact shutdown procedure as safe. Do not deliberately interrupt communications or disconnect devices during emission.
3. After scan has stopped and safe state is independently confirmed, close normally and check handle release.

**Pass:** cancellation is cooperative, the scan ceases, selected input is independently safe, no forced termination occurs and the CT400 releases cleanly. **Fail/stop:** stop does not complete, safe state cannot be confirmed, worker is terminated, or resource release is uncertain. Follow the site's emergency procedure; preserve logs without repeating the test.

## F. Gate F — Concurrent physical CT400 scan and camera streaming

Prerequisites: Gates A–E pass; camera and CT400 separately validated; only previously approved CT400 settings are used; operator supervises the entire run.

1. Use a reviewed configuration with physical CT400 and Vimba selected explicitly. Record camera SDK/transport/camera firmware and CT400 DLL/device/firmware versions.
2. Confirm identity and continuously updating frames before scanning. Start one previously approved scan via the GUI.
3. Observe and record multiple distinct physical frames arriving while the physical scan is visibly active (with times/evidence that establish overlap), then confirm scan wavelengths/powers appear in the real plot while camera streaming continues.
4. Only if the operator has approved and Gate E cancellation passed, repeat cancellation with camera streaming and check that cancellation does not stop camera frames and leaves selected laser input independently safe.
5. Stop scan, verify safe state, stop camera streaming and close normally. Closing the application during an active scan is outside this gate unless separately approved under Gate E.

**Pass:** physical devices are unambiguous; camera frames overlap in time with scan activity; plot receives scan data; scan completion/cancellation leaves camera operation intact; resources release. **Fail/stop:** frozen camera, missing data, ambiguous provenance, any unsafe laser state or unclean shutdown.

## Negative tests and recovery

Only conduct fault/recovery scenarios with an approved, instrument-specific procedure and no unapproved emission. Do not create a fault merely to complete a checklist.

For approved negative cases, include absent camera at startup, unavailable Vimba X/transport, CT400 unavailable while confirmed safe, and mixed camera initialization if the configuration supports it. Prefer a separate controlled software image for missing drivers. Never unplug a live instrument or induce CT400 communication loss during a scan without written/site-approved procedure. For each test verify affected-device attribution, no simulated data presented as physical, unaffected subsystem behavior and approved recovery.

**Pass:** every approved failure is visible and attributed to the affected device; no simulation is presented as physical data; unaffected subsystems remain usable where expected; recovery follows the approved procedure. **Fail/stop:** ambiguous data source, hidden error, stale device state, unsafe state or unrecoverable failure.

## G. Gate G — Final resource release and application shutdown

Prerequisites: no unresolved instrument error; any scan has completed or has been cancelled using the procedure validated at Gate E; the responsible operator confirms the safe-state indication before close.

1. Independently verify the selected laser input is safe; do not treat GUI status or the close-time LI_1 command as proof.
2. Stop camera acquisition and close IOPanel normally; verify no active workers, process handles or locked devices remain. Do not force terminate while any CT400 operation could be active.
3. Return source, shutter, optical path, detector and camera controls to the operator-approved state; finish lab checkout.
4. Complete the record sheet and use controlled lab storage. Attach only redacted logs/evidence to internal issue records.

**Pass:** all operations stopped, selected output independently verified safe, devices released and process exited cleanly. Otherwise fail and use the lab escalation procedure.

## Result record

For each Gate A–G record `PASS`, `FAIL`, or `NOT RUN`, date/operator reference, observations, expected versus actual behavior, redacted log references and separately filed software defects. Record the negative/recovery cases separately as `PASS`, `FAIL`, or `NOT RUN`. State explicitly that simulated/driver-free checks are not hardware results. Record any test stopped early for safety or reliability reasons. Do not advance past a failed prerequisite gate.
