# CT400 ETA calibration — supervised physical runner (experimental)

**Issue #140. This runner is new and has not been physically accepted.**
Before using a physical CT400, obtain the laboratory owner/operator's approval
for this specific build, instrumentation and run plan, following the lab's safe
operating procedure. A successful mock CI test does **not** establish native
CT400 cancellation, fault recovery or optical safe state.

The existing **Instruments → CT400 ETA Calibration…** remains a hardware-passive
preview. The separate **Instruments → CT400 ETA Calibration — Supervised Runner…**
window is an explicitly operator-triggered, nonmodal campaign interface.
Nothing happens until the operator supplies a verified physical wavelength
range, session identifier, confirms detector setup/power/native-risk warning,
and approves the final Start confirmation. No code, script, CI job or remote
assistant should click Start on behalf of the operator.

## What the runner does

- Reuses the existing 18-case matrix and its unique run IDs and durable
  JSONL/CSV/JSON records. These are **timing metadata**, not sampled spectra.
- Dispatches scans one at a time **through the normal CT400 ScanPanel and its
  existing ScanWorker**. The runner never calls vendor DLL functions.
- Reserves the scan panel to prevent manual Start presses from overlapping
  the campaign. Other CT400 activities must remain idle; losing the confirmed
  input, speed, CT400 identity or operation ownership halts future scheduling.
- Does not schedule the next scan until the previous scan's worker has finished,
  its final laser disable call returned and operation ownership was released.
  A 1.5 s inter-run delay leaves a visible Stop/Pause opportunity.
- **Pause after current** allows the active scan to finish and then stops
  scheduling. **Stop campaign** cancels the in-progress acquisition through the
  existing ScanPanel Stop flow, waits for its worker and prevents new runs.
  An unexpected result or persistence failure aborts further scheduling.
- The 10 nm/s tests come first. Before the 5 nm/s block the runner **pauses**.
  The operator uses the application's normal **Disconnect** action, independently
  confirms safe state, then clicks **Prepare 5 nm/s next Connect**. This action
  updates only the *in-memory next-Connect configuration*: it does not command
  the CT400. The operator then manually uses ordinary **Connect** and explicitly
  confirms the new block. This is a snapshot of the speed *commanded at Connect*,
  **not vendor-device speed readback**.
- Case F uses DE1–DE4 only after the operator attests the extra detector wiring.
  Without that attestation the 3 F repetitions are logged as skipped.
- Each completed, cancelled, warning, failed or skipped run is saved immediately.
  Report fields include start/end, resolution, detectors, input, power, planned
  speed, actual Connect-time *command* speed, worker-stage timings, GUI duration
  and cleanup outcome. Any analysis must exclude skipped or synthetic records.

## Validation before physical execution

1. Install and launch the reviewed IOPanel build in the approved lab profile,
   with the physical CT400 in a known safe state.
2. Confirm the actual laser supported wavelength range and approved power from
   independent instruments/procedures, not from the app's configuration values.
3. Use **Connect** normally on the physically wired input at 10 nm/s. Check the
   app status and input authorization before opening the runner.
4. Enter the independently verified wavelength limits and complete the
   supervisor preflight. Configure the output folder on a drive with sufficient
   write permissions; existing campaign IDs are never overwritten.
5. Observe the live records and controls during every run. A known native
   `CT400_ScanStart` stall can still recur; the previous Issue #143 was closed
   as inconclusive, **not repaired**.
6. At the 5 nm/s block, do the separate Connect/Disconnect procedure exactly
   as instructed in the runner. The runner does not silently reconnect or
   alter the vendor's laser state.
7. After the last run/Stop, independently verify the optical safe state, use
   normal Disconnect and review the CSV/JSONL records. If the app crashes,
   preserve the partial journal and do not automatically resume old runs.

**Not claimed:** Physical ETA calibration or fit, hardware readiness signoff,
vendor-independent proof of safe state following a native failure, ability to
interrupt a blocking native DLL call, fully measured laser speed readback, or
automatic recovery after a hardware fault.

The editable scan-panel speed and the frozen in-memory Connect-time speed must
remain consistent. After the 5 nm/s block, the next-Connect configuration
remains at 5 nm/s until the operator restores it using an approved configuration
workflow. The runner aligns the panel label to the last approved speed at exit;
it does not automatically change the laser speed back to 10 nm/s.

## Offline verification

```powershell
uv run --no-sync python -m pytest -q tests/test_ct400_calibration.py tests/test_ct400_calibration_dialog.py tests/test_ct400_calibration_simulation.py tests/test_ct400_calibration_live.py -o faulthandler_timeout=30
```

Full Windows CI additionally runs Ruff, Pyright, pytest and one-folder packaging.
The integration tests replace every scan with a fake Qt signal sequence, and
never initialize or operate the vendor DLL.
