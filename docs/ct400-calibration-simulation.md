# CT400 ETA calibration: offline coordinator prototype

Issue #140 has a **simulation-only** state-machine prototype in
`logic/ct400_calibration_simulation.py`. It deliberately does **not** run a
CT400 scan. No CT400, Qt, ScanWorker, vendor DLL, laser, or other instrument is
imported or called. The existing `CT400CalibrationDialog` remains a dry-run
preview, and its physical **Start campaign** button remains disabled.

## What is implemented

- The existing 18-case plan, uniquely identified run IDs and immutable
  per-run snapshots are reused unchanged.
- A caller explicitly starts the prototype and acknowledges *simulated* speed
  checkpoints (initial 10 nm/s and the later 5 nm/s block).
- Exactly one fake run may be reserved with `begin_next_run()`; only a matching
  run ID can be finalized. There is no automatic next-run callback and no timer.
- Between fake runs, `pause()`/`resume()` and `stop()` prevent further
  scheduling. In an active fake run, pause is deferred until completion; stop
  or abort waits for its synthetic terminal report then becomes terminal.
  These methods do **not** send any real ScanStop or other hardware command.
- Failed and cancelled fake outcomes stop scheduling. A warning pauses pending
  explicit review. Unsupported detector cases can be journaled as skipped.
- Every finished/skipped run is persisted through the existing append-only
  JSONL journal and atomic CSV/JSON snapshots. A persistence error is terminal:
  the caller must inspect the journal rather than silently retrying a row.
  An interrupted journal can be restored for analysis, **not** for auto-resume.

## Minimal simulation-only example

Run this only in a developer environment. The synthetic elapsed and stage
timings are **not optical measurements** and must not be fitted to physical
hardware ETA values.

```python
from pathlib import Path

from logic.ct400_calibration_simulation import (
    SimulatedCalibrationCoordinator,
    SimulationState,
)

controller = SimulatedCalibrationCoordinator(Path("example-fake-calibration"))
controller.start()

while controller.state not in {
    SimulationState.COMPLETED,
    SimulationState.FAILED,
    SimulationState.STOPPED,
    SimulationState.ABORTED,
}:
    if controller.state is SimulationState.SPEED_CHECKPOINT:
        # Simulation acknowledgement only; never connects/reconfigures CT400.
        controller.acknowledge_simulated_speed(controller.pending_speed_nm_s)
    elif controller.state is SimulationState.RUNNING:
        run = controller.begin_next_run()  # Returns a plan entry; no instrument call.
        controller.complete_run(
            run.run_id,
            outcome="success",
            simulated_gui_duration_seconds=run.nominal_sweep_seconds + 0.5,
            stage_seconds={"scan_wait_end_seconds": run.nominal_sweep_seconds},
        )
    else:
        raise RuntimeError(f"Simulation needs explicit operator disposition: {controller.state}")

assert controller.completed_count == 18
```

For a stop *during* a fake run, call `stop()` after `begin_next_run()`,
then report that run's synthetic terminal result with `complete_run()`.
No next run is scheduled. To simulate a failed scan, call `complete_run()`
with `outcome="failed"` and a nonempty reason.

## Important gaps before physical integration

This prototype intentionally has no GUI controls for an active campaign and
no bridge to the actual CT400 `ScanWorker`, scan-completion events, Qt event
loop, or physical connection lifecycle. Its speed acknowledgement is only an
offline test state, **not instrument speed readback or operator authorization**.

A **separate implementation and review** must bind the coordinator to the
existing authorized scan UI path and confirm real worker ownership, per-run
timings, controlled pause/stop semantics, post-scan cleanup, fresh Connect-time
speed approval, safe-state verification, warnings, and physical interlocks.
No backend may be wired to the existing disabled physical Start button until
that integration, negative-path tests, explicit risk review, and supervised
laboratory acceptance are complete. Closing Issues #143 and #148 as
not-planned/inconclusive does not waive those prerequisites.

Offline verification:

```powershell
uv run --no-sync python -m pytest -q tests/test_ct400_calibration.py tests/test_ct400_calibration_dialog.py tests/test_ct400_calibration_simulation.py
```
