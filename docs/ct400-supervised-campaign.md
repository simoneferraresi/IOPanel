# CT400 ETA calibration — supervised campaign runner (Issue #140)

This proposal adds an explicitly operator-started *physical* calibration
session to **Instruments → CT400 ETA Campaign (Supervised)…**.
The legacy **CT400 ETA Calibration…** menu item remains a dry-run preview.

## Safety and prerequisites

- **No laboratory runs were executed in development or CI.** This feature is
  not physically qualified until a separate authorized, attended test on the
  laboratory PC. Review the code, UI and fake tests before using real hardware.
- No code in the new dialog calls a CT400 DLL method. Real acquisitions are
  submitted through `CT400ControlPanel.run_calibration_scan()`, which delegates
  to the **existing ScanWorker**, including laser disables, ScanStop ownership,
  operation-state gating and cleanup/recovery handling.
- The real CT400 must be initialized, **explicitly Connected**, idle, on the
  physically correct laser input, with confirmed power, laser range, detector
  wiring and safe optical state. Mere CT400 Init is not authorization.
- A distinct **operator checkbox** and a No-default confirmation are required
  before the first scan. Unchecking case F confirms DE2–4 are *not* known to be
  connected; those three runs are recorded as skipped with the reason.
- The dialog never automatically connects, disconnects, power-cycles or
  changes the laser's sweep speed during an active connection.
- The GUI blocks ordinary scan/monitor controls during the campaign; normal
  CT400 operation-state safety is preserved. A new scan will not be launched
  until the previous worker has emitted its terminal timing and released
  operation ownership and cleanup.
- Stop prevents all subsequent runs and invokes the **ordinary ScanWorker
  cancellation path** when a scan is active. Because vendor calls are blocking,
  Stop cannot preempt a blocked ScanStart or guarantee immediate optical
  safe-state; follow laboratory Stop/safe-state procedures. Never force-kill
  the Qt worker.
- Unexpected CT400 outcomes, loss of input authorization/device ownership,
  missing timing IDs, or JSONL persistence failures halt the sequence. Warnings
  pause for explicit review; any native error still requires the separate
  fail-closed recovery state and independent optical safe-state confirmation.
- **No unattended scheduling**: the application and qualified operator must
  remain present. The optional auto-next box advances only within one
  operator-approved speed block after a 2.5-second gap.

## How a qualified operator would run it after approval

1. Independently verify laser model/range, approved 1 mW (or appropriate)
   power, Input 1 or the actual wired input, and detector cabling. Start from
   CT400 Ready (Disconnected). Set the application's ordinary Connect speed
   to **10 nm/s** using the dialog's explicit **Set next Connect to
   10 nm/s** button *while disconnected*, then perform the ordinary Connect.
   Verify connected status. Do not
   treat panel speed edits as an instrument speed change.
2. Open **Instruments → CT400 ETA Campaign (Supervised)…**. Select an empty
   output directory (per-run filenames have a fresh campaign UUID).
3. Confirm the optical setup/authorization checkbox, and optionally confirm
   DE2–4 for case F. Choose whether the next run requires a manual Continue or
   the dialog may automatically advance between runs at the same speed.
4. Click **Start supervised campaign** and explicitly approve the physical
   confirmation. Observe the scan, spectrum, logs, progress and emitted files.
   **Pause** only between runs; **Stop** requests ordinary cancellation, then
   waits for actual worker cleanup.
5. After the fifteen 10 nm/s repetitions, the campaign holds a **speed
   checkpoint**. Use the ordinary **Disconnect CT400** and independently
   confirm optical safe state. Use the checkpoint's **Set next Connect to
   5 nm/s**: this modifies **in-memory configuration only**, and does **not**
   command hardware. Then perform the ordinary Connect. The dialog refuses to
   continue until the recorded submitted Connect-time speed is 5 nm/s,
   the input is still authorized, and the operator provides a **fresh optical
   safety checkbox + separate confirmation**.
6. Run the final three 5 nm/s measurements, save the JSONL journal, CSV and
   JSON manifest, Disconnect normally, and independently verify optical safe
   state. Document all warnings and actual device/model/firmware metadata
   separately; successful DLL cleanup return alone does not prove safe optics.

**Important:** `config.scan_defaults.speed_nm_s` is temporarily changed
in-memory to 5 for the next normal Connect. It is not device readback. The
dialog restores the original editable scan-panel fields at completion, but
does *not* silently reset the application Connect speed while a 5 nm/s
connection is active. The next session must check the configured speed
explicitly.

## Qualification and rollout gates

The PR must pass Ruff, Pyright, full pytest and Windows packaging. Its fake
GUI tests verify results, speed checkpoint, Stop, warning/failure, stale run IDs
and output. None proves real DLL concurrency, detector wiring, native return
codes, safe laser disable, the speed readback, or physically correct delays.

**Do not merge or use for physical measurements** until a separate
review has confirmed that the revised Connect-time speed snapshot does not
alter unrelated connection behavior and that a qualified operator accepts
native-risk mitigations (the former intermittent ScanStart fault is still
unexplained). Begin physical qualification with *one* attended short scan,
verify Stop and journal, and only then separately authorize the 18-run
campaign. Issues #143 and #148 being administratively closed did not
physically eliminate the intermittent DLL risk.
