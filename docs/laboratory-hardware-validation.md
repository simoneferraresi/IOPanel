# Laboratory hardware validation protocol

This checklist is for an authorized operator on the laboratory PC. It is a manual validation procedure; it does not certify or operate hardware automatically. The hardware-independent pytest suite uses simulators and mocks and is not evidence of physical compatibility.

Tracking issue: [#20](https://github.com/simoneferraresi/IOPanel/issues/20).

## Safety and scope

- Obtain the site's normal instrument and laser authorization before starting. Follow the CT400, tunable laser, detector, camera and optical-table procedures in force at the lab.
- Use only scan limits, optical power, input ports, speed, detector selection and camera settings explicitly approved by the responsible operator for the installed setup. Record those approved values before a scan. Do not infer a safe range from the repository's example or default INI values.
- The application's `config.ini` defaults are example configuration, not validated laboratory limits. Make a lab-local copy and check every instrument and camera entry before launch.
- Launching the actual application initializes the configured CT400 and starts initialization/acquisition for enabled physical cameras. Do not launch it as a passive discovery utility. The application's camera discovery dialog also calls the Vimba discovery API and must be used only when authorized.
- Do not run a physical scan from a diagnostic script. The documented `app.py` entry point starts the GUI; measurements begin only after an operator uses the scan controls. Keep a qualified operator present for every live scan.
- Never use unplugging, process termination or a forced worker termination as a routine way to stop an active laser scan. Follow the instrument's approved emergency procedure if normal stop does not complete.
- Keep completed records in the lab's controlled location. Do not commit device serials, private addresses, operator names or completed live configuration files to the repository.

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
| Vimba SDK version and installed transport layer(s) | |
| VmbPy version and Python environment | |
| Camera model, serial (lab record only), firmware, transport | |
| Camera pixel format and frame dimensions | |
| Approved scan start/end, resolution, speed, power/unit, input port, detector | |
| Approved camera exposure/gain and other changed controls | |
| Log path and report/evidence path (controlled lab storage) | |

Capture versions before opening IOPanel where possible, using vendor utilities that do not acquire or control the device. `uv.lock` and project metadata identify Python dependencies but do not establish installed CT400 DLL, Vimba SDK, transport-layer, firmware or hardware versions. Do not open a device concurrently from a vendor utility and IOPanel.

## A. Offline checks (no instruments connected)

Run these on the target PC before connecting or enabling equipment:

```powershell
git status --short --branch
git rev-parse HEAD
python -c "import platform, struct, sys; print(sys.version); print(platform.platform()); print('Python architecture: {}-bit'.format(struct.calcsize('P') * 8))"
uv --version
uv sync --extra test
uv run pytest -q
```

Expected: the recorded commit is the intended release; the environment resolves from the committed lock file; all hardware-independent tests pass. This test command must run without a CT400 DLL, Vimba SDK, cameras or CT400 connected. Investigate any failure; do not skip a test to produce a pass.

Validate the lab-local INI without constructing a window or opening devices:

```powershell
uv run python -c "from pathlib import Path; from app import load_raw_config_from_ini; from config_model import AppConfig; AppConfig.from_ini_dict(load_raw_config_from_ini(Path('config.lab.ini'))); print('Configuration is valid')"
```

This imports application modules but does not instantiate `MainWindow` or initialize an instrument. Confirm the intended backend explicitly. For physical work, omit `ct400_backend` only if intentionally relying on the backward-compatible default `physical`; never set a simulated backend for a physical validation run. Camera sections default to `vimba`; simulation must be explicitly selected and must not be used to pass a physical test. Check camera identifiers and enabled flags before launch. Do not print or attach the full lab INI to a public issue.

**Pass:** clean dependency/test result; configuration is valid and matches the intended physical devices; operator-approved limits are recorded. **Fail:** unresolved dependency/architecture mismatch, invalid/ambiguous config, unexpected simulation selection, or any unreviewed test failure. Stop before connecting/operating equipment on failure.

## B. CT400-only checks

Perform only with the operator present and the camera subsystem disabled in the lab-local configuration if the application permits that arrangement. Keep the optical path and laser state under the lab's approved procedure.

1. With scan idle and laser in the approved safe state, verify the configured `CT400_lib.dll` exists, its architecture matches Python, and vendor-required runtimes are installed. Record versions; do not copy DLLs from an unverified machine.
2. Start IOPanel using the reviewed lab-local config and the normal GUI. Startup calls the CT400 initialization path (`CT400_Init`); record whether connection succeeds and the exact status/log message. Confirm it is the physical backend, not a `SIMULATED`/Dummy status. Confirm the expected device using the vendor-approved identity method; do not guess an instrument address.
3. Before clicking Scan, have the operator approve and enter exact scan boundaries, resolution, speed, power/unit, input port and detector for the connected setup. Record them. Confirm the GUI reflects those entries.
4. Start one bounded, operator-approved scan from the GUI. Confirm it completes, the plot displays wavelength and measured-power data, requested boundary behavior and sample count agree with the CT400's documented resolution semantics, and values/units are plausible according to the operator's independent checks. Do not assume the simulator's inclusive endpoint convention proves CT400 behavior.
5. On a separate approved run, use the GUI cancellation action while a scan is active. Observe the cancellation status, scan cessation, CT400 responsiveness and laser-disabled state. Verify laser output state using the site's independent safe indication/check. The software attempts laser disable during worker cleanup and CT400 close; this does not replace independent verification.
6. With no scan active and output confirmed safe, close IOPanel normally. Confirm it exits cleanly and the CT400 handle/connection is released according to the supported driver/vendor indication. Do not use Task Manager as normal cleanup.

**Pass:** correct DLL/runtime/bitness and physical CT400 connection; scan data and plot agree with approved settings and vendor expectations; cancellation leaves output safely disabled; normal close releases the connection. **Fail:** any simulated fallback presented as physical, unexplained status/data, failure to stop or independently verify safe output, or unclean resource release. Stop live testing and follow site procedure on any safety concern.

## C. Allied Vision camera-only checks

1. Before opening IOPanel, record the installed Vimba SDK, VmbPy binding, transport-layer and camera firmware versions. Confirm the transport layer supports the connected interface. The Python binding alone is not the Vimba transport layer.
2. Enable only the intended camera(s) in the lab-local configuration, with `backend = vimba` (or leave backend unset only when the physical default is intended). Verify configured identifiers using `Instruments > Discover Cameras...` while authorized. Record model/identity in controlled storage and ensure each configured identifier is unique and correct.
3. Start IOPanel. Confirm each configured physical camera initializes, is identified by its physical name (no `[SIMULATED]` label), and streams frames into its camera panel. Record frame dimensions and pixel format from the application's supported information or the vendor utility while the device is not simultaneously open elsewhere.
4. Confirm image orientation against a known asymmetric target and the `flip_horizontal` setting. Confirm image intensity is not clipped or unexpectedly scaled for the selected format. Change only operator-approved exposure, gain and supported controls; verify the displayed response and record actual values.
5. Stop/close using the normal GUI path. Confirm streaming stops, the camera closes and can be reopened in a subsequent normal application start. Check that no other viewer owns the device.

**Pass:** expected physical camera identity, supported pixel format, stable updating image with verified orientation/intensity behavior, applied supported control changes and clean close/reopen. **Fail:** camera absent, identity mismatch, unsupported format, stale/corrupt frames, unexplained image transform/scaling, or device remains locked after close.

## D. Concurrent physical scan and camera streaming

Proceed only after sections B and C pass independently. Use only settings already approved for the exact setup. Start IOPanel once with the physical CT400 and intended Vimba cameras explicitly selected/configured.

1. Confirm physical device identities and that the camera view is updating before the scan.
2. Start the operator-approved CT400 scan from the GUI. While it is visibly in progress, observe the camera view continuously and verify multiple distinct frames continue to arrive (not merely a frame before and after the scan). Record scan and frame observations/timestamps without placing unapproved software in the instrument control path.
3. After completion, verify the plotted scan data are present and agree with the approved wavelength/resolution/power settings while the camera continues updating. If authorized, save scan data to a designated controlled test folder, inspect exported columns/units and provenance, then clearly segregate/delete the test measurement according to lab data policy. Do not treat a screenshot as raw measurement data.
4. On a separate approved scan, cancel through the GUI while camera streaming remains active. Confirm scan cancellation does not stop camera frames, laser output reaches the independently verified safe state, and a subsequent operator-approved scan can be run if the site procedure allows.
5. Close the application normally while camera streaming is active and no scan is running. Confirm both camera and CT400 resources release. Only test closing during an active scan if the responsible operator has explicitly approved it and an independent safe stop is available; current shutdown code has a timeout/forced-thread-termination fallback, which warrants special caution.

**Pass:** real camera frames demonstrably continue during the real physical scan, scan results reach the plot, cancellation/error in one path does not silently corrupt the other, and normal shutdown releases both systems. **Fail:** frozen camera, missing/incorrect plot, backend identity uncertainty, cross-path failure, unsafe laser state, or hanging/unclean shutdown. Stop and document exact logs/settings.

## E. Negative tests and recovery

Automated driver-free tests already cover simulator failures and worker recovery; they do not prove that physical hardware is safe to fault. Never deliberately interrupt an active scan, laser connection or camera link unless the authorized operator has approved the exact procedure and the manufacturer's/site process permits it.

- **Camera unavailable at startup:** with IOPanel closed, and only if approved, test a configuration whose camera identifier is absent or whose camera is safely disconnected. Confirm the UI reports the camera unavailable, leaves its view clearly unavailable, and does not label simulated frames as physical or silently substitute a simulator. Confirm CT400 availability/status remains independently understandable. Restore the camera and use a normal restart/reopen procedure; confirm recovery.
- **Vimba/transport unavailable:** preferably validate this on a separate non-lab Windows installation or controlled software image with no transport layer, not by uninstalling drivers on the live acquisition PC. Confirm the app reports unavailable discovery/opening and no camera is represented as connected. Restore the known-good image before live validation.
- **CT400 unavailable/communication failure:** first ensure laser output is independently safe and no scan is active. Only use a vendor/site-approved disconnected-start or communication-loss test. Confirm any Dummy/simulation status is explicit and the physical operation is blocked/clearly identified; never count its data as an experiment. Restore the connection and verify normal physical initialization before acquisition.
- **Mixed camera initialization:** if the lab has multiple configured physical cameras, test one failing identifier alongside one known-good camera only with approval. Confirm failure is attached to the correct panel and does not hide/replace successful physical frames. Do not induce hot-unplug during streaming unless specifically approved.

**Pass:** every failure is visible and attributed to the affected device, no physical failure silently becomes experimental simulation, unaffected subsystems remain usable when expected, and recovery follows an approved restart procedure. **Fail:** ambiguous source/data, hidden error, stale device state, or unsafe/unrecoverable state. File a separate defect with redacted logs and configuration details.

## F. Final shutdown and safety checks

1. Stop the scan through the GUI and wait for the worker to report completion/cancellation. Confirm laser disabled using an independent lab-approved indication; do not rely solely on the GUI message or software cleanup attempt.
2. Stop camera streaming and close IOPanel normally. Observe process exit and camera/CT400 release indicators. Record Qt/worker errors and any delay. Do not force-terminate the application while the CT400 may be scanning.
3. Return instruments, laser, shutters, optical path, source, detectors and camera controls to the operator-approved final state. Follow site checkout procedures and notify the responsible operator of anomalies.
4. Complete the record sheet and attach only redacted logs/results to the internal lab record. Keep raw experimental and identifying information in controlled storage.

**Pass:** scan and streaming are stopped, laser safe state is independently verified, devices are released, process exits cleanly and equipment is left in the approved state. Otherwise fail and follow the lab escalation procedure.

## Result record

For each A–F section record `PASS`, `FAIL`, or `NOT RUN`, date/operator reference, observations, expected versus actual behavior, redacted log references, and a separately filed issue for each software defect. State explicitly that simulated/driver-free checks are not hardware results. Record whether any test was stopped early for safety or reliability reasons.
