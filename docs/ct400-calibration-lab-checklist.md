# CT400 ETA calibration laboratory acceptance checklist

Physical campaign execution is disabled in this build, and campaign scheduling is not implemented. This checklist is for a later runner build, after Issue #125 connection-state work is merged and validated. The exact Issue #140 matrix is visible in the dry-run preview.

- [ ] Confirm the approved Issue #140 matrix, case order, repetitions and speed blocks in the dialog preview.
- [ ] Connect using the ordinary IOPanel Connect action; verify instrument identity, laser input and laser model. Independently establish the connected laser's supported wavelength range using the lab's validated procedure; configuration limits are not instrument readback.
- [ ] Confirm the laser input, power and unit, configured connect-time speed, requested scan speed and enabled detectors shown in the campaign confirmation.
- [ ] Verify every planned case falls inside the connected laser range and uses the approved resolution.
- [ ] Use the ordinary simulated scan workflow and mock-only record tests to verify unique scan IDs, stage timing events, GUI duration, CSV rows, JSON manifest and JSONL journal. A campaign scheduler is not included in this build.
- [ ] Interrupt a simulated campaign during a scan and verify Stop is sent only through the normal scan worker, no next run begins, and the prior records remain readable.
- [ ] Confirm ownership remains held until the scan worker and CT400 cleanup finish; verify no instrument calls overlap.
- [ ] At the 10 nm/s to 5 nm/s boundary, pause for an operator speed change, reconnect through normal Connect, verify the new connected speed and require a fresh confirmation before resuming.
- [ ] Inject connection loss, speed mismatch, warning and fatal scan outcomes with mock devices; verify the campaign halts safely and records the outcome.
- [ ] Independently inspect predictions versus measured full durations and run-to-run variation. Do not apply an ETA correction until independent validation supports it.
- [ ] Obtain laboratory owner approval before any physical scan. No physical validation is performed by this coding workflow.
