# M4-E1 — Simulated CT400/PM5020 synchronized acquisition (no physical devices)

## Scope / qualification

M4-E1 introduces a GUI-independent prototype for the **ordering and failure
semantics** of a coordinated CT400 scan and PM5020 burst. The entry point
`SimulatedSynchronizedCoordinator` **refuses non-dummy backends**, is not
connected to the current Qt GUI, and cannot load physical instruments.

This is **not** a physical timing, optical safety, or clock calibration.
The two dummy devices do not generate or consume real BNC trigger edges.
The required `sync_text_source` supplies an explicitly synthetic,
CT400-format sync file, separate from the PM5020's synthetic burst; alignment
checks that the chosen indices are structurally valid. It does **not**
independently demonstrate that the two simulators share a clock.

## Sequencing / failure behavior

1. Require a preconnected `DummyCT400`, an idle connected
   `DummyPM5020`, and an uncontended coordinator run lock.
2. Configure CT400 scan range/resolution/detector selection and the
   PM5020 two-channel, one-sample-per-trigger burst contract.
3. Enable *simulated* CT400 external synchronization.
4. Arm the simulated PM5020 **before** CT400 scan start.
5. Start the CT400 scan and wait for its classified result; fatal/unknown
   codes abort; vendor warning codes are retained, not changed to success.
6. Retrieve processed CT400 data and the original sync text, then retrieve
   the PM5020 raw burst (both channels and native uint32 ticks).
7. Parse and align using **explicit** `index_base=0`, justified by
   Yenista CT400 Programming Guide 1.4, p. 37, with
   `timing_qualified=False` and no resampling/extrapolation.
8. Disable CT400 simulated sync output even on failure. On an error with
   an armed/errored PM5020, close the simulated meter to disarm it.
   Cancellation can stop a blocking simulated CT400 scan from a
   different thread; retry after PM5020 reconnect.

The coordinator does not take over the existing CT400/PM5020 GUI
connection owners. Its run mutex excludes only concurrent calls through
the **same** coordinator, not competing application workers; therefore
physical backends are prohibited. Hardware operation will require a
separate device-reservation and bounded-cleanup design and operator
supervised acceptance. There is **no EDFA integration**, laser-on command,
physical SDK dependency, or modified scan/monitoring worker.

## Reproducible driver-free demonstration

From the repository root, in a Python 3.12 test environment:

```python
from hardware.ct400_types import Enable, LaserInput, LaserSource
from hardware.dummy_ct400 import DummyCT400
from hardware.dummy_pm5020 import DummyPM5020
from logic.simulated_synchronized_acquisition import (
    SimulatedSynchronizedCoordinator,
    synthetic_ct400_sync_text,
)
from logic.simulated_synchronized_archive import archive_simulated_synchronized_capture

ct = DummyCT400(scan_duration=0)
ct.set_laser(LaserInput.LI_1, Enable.DISABLE, 1, LaserSource.LS_TunicsPlus, 1545, 1555, 10)
pm = DummyPM5020(burst_sample_count=257)
pm.connect()

coordinator = SimulatedSynchronizedCoordinator(
    ct, pm, lambda: synthetic_ct400_sync_text(first_index=14, point_count=240)
)
result = coordinator.run()
assert result.pm_burst.sample_count == 257
assert result.aligned.sample_count == 240
assert not result.timing_qualified
path = archive_simulated_synchronized_capture(result, "output/simulated_sync")
print(path)
```

This example is *synthetic*; the demonstrated waveform and timestamps
have no calibrated relationship to real physical trigger edges.

## Archive contract

`archive_simulated_synchronized_capture` stages a new run directory
before publishing it without overwriting existing runs. It writes:

- `pm5020_raw_burst.csv`: original buffer position, native uint32
  timestamp ticks, CH1 watts and CH2 watts, **unsliced**.
- `sync_wavelength.txt`: verbatim synthetic CT400 sync text with
  original pulse indices.
- `ct400_processed.csv`: independent processed CT400 wavelength grid
  with unqualified vendor-native detector values. This is **not**
  interpolated PM5020 data.
- `acquisition_metadata.json`: simulation flag, warning code,
  configuration, units, provenance, M4-E1 milestone, provisional
  zero-based choice, `timing_qualified=false`, and SHA-256 for each
  raw/processed file.

The schema retains `schema_version: 1` and compatible raw file headers
so that `tools.audit_pm5020_sync_run` can independently validate the
synthetic result. Generated archives must **never** be confused with
M4-C/D physical qualification evidence.

## Next gate (M4-E2 — not part of this PR)

Require documented ownership transfer and mutual exclusion with Qt CT400
scan workers and PM5020 monitoring; bound native wait/recovery behavior;
calibrate trigger-to-buffer correspondence and timestamp units with
operator-supervised physical tests. Only then permit a physical
coordinator and GUI integration. Preserve raw data and mark any
unqualified wavelength mapping in saved experimental datasets.
