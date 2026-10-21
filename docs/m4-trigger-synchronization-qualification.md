# M4-A — CT400/PM5020 offline trigger-index alignment (no hardware operation)

## Scope and provenance

This milestone adds **only offline analysis** of an existing CT400
wavelength-sync log and a previously captured `BurstPowerData`. It does
not arm a meter, enable a laser, configure CT400 synchronization, read
an instrument, create hardware workers, or change EDFA behavior.

Source procedure: laboratory `PM5020_burstMeas(1).ipynb`, especially
"PM5020 Burst Measurements". The notebook configures PM5020 CW burst
measurement on both channels, source 3 (notebook says AUX), initial delay
0, one sample per trigger, one average. It arms PM5020 first, starts the
CT400 scan second, retrieves PM samples and saves a two-column
CT400 `pulse_index wavelength_nm` sync log. The notebook then slices
PM samples from `pulse_indices[0]` to `pulse_indices[-1]`, **assuming**
CT400 indices coincide with contiguous PM5020 sample-buffer positions.
That assumption has not been independently verified.

M4-A replaces the implicit mapping with a **mandatory explicit
`index_base=0` or `index_base=1`** and checks for out-of-range indices,
duplicates, backwards sequence, missing indices and unexpected wavelength
order. Sparse mappings require `allow_sparse=True`. The result explicitly
retains `timing_qualified=False`: a syntactically valid mapping is **not
evidence** that the hardware trigger edges, index origin, or latencies match.
Do not select an index base solely because a smooth plot results.

`logic.trigger_sync_alignment` also refuses to infer time from PM5020
unsigned 32-bit ticks. Timestamp duplicates, reversals and rollover are
rejected pending characterization. First/min/max tick increments are
diagnostics **in ticks, not seconds**. Both PM5020 channels remain in
watts. CT400 raw sync wavelengths remain in nanometers.

Optional projection to CT400 processed wavelength points uses linear
interpolation bounded by the *observed sync interval*. It supports
increasing/decreasing wavelength scans and **rejects extrapolation**;
this differs intentionally from the notebook's SciPy interpolation
configured with `fill_value="extrapolate"`, which could invent apparent
power outside the physically synchronized range.

## Software demonstration (all synthetic)

```python
from hardware.pm5020_types import BurstPowerData
from logic.trigger_sync_alignment import align_ct400_burst, interpolate_within_sync_bounds, parse_ct400_sync_text

pulses = parse_ct400_sync_text("0 1550\n1 1551\n2 1552")
burst = BurstPowerData((100, 200, 300), (0.1, 0.2, 0.3), (1e-6, 2e-6, 3e-6))
candidate = align_ct400_burst(pulses, burst, index_base=0)
projection = interpolate_within_sync_bounds(candidate, (1550, 1550.5, 1552))
assert not candidate.timing_qualified
```

Neither the text-parser success nor the projection makes the results
physically timing-qualified; use them for test fixtures only until the
lab acceptance gate is completed.

## Physical acceptance work (not performed)

Before permitting coordinated CT400/PM5020 scans in IOPanel:

1. Confirm the **actual CT400 pulse-log format**, pulse-index origin, and
   what exactly constitutes a recorded pulse. Check whether indices count
   all emitted edges and whether missing/extra triggers occur.
2. Characterize CT400 external-synchronization pulse polarity/width,
   wiring, TTL levels, termination, edge-to-wavelength relationship, and
   PM5020 AUX/trigger source semantics on the lab setup.
3. Confirm PM5020 input ranges, sensor wavelengths, PM5020 native
   sample-timestamp units and rollover, sample count accuracy and
   one-trigger-to-one-sample behavior. Measure offsets and jitter across
   several speeds/sampling resolutions, not just one scan.
4. Confirm sequence guarantees: PM5020 armed **before** CT400 scan start,
   scan-end behavior, PM buffer overflow and missing TTL, cancellation,
   disconnect and recovery, and safe cleanup of BOTH instruments.
5. Review and qualify the new CT400 external synchronization API against
   the laboratory DLL before any live coordinator. The entry point is now
   declared in `hardware.ct400.CT400`, but its physical trigger behavior
   remains unverified.
6. Keep CT400 and PM5020 ownership independent until a single acquisition
   coordinator can serialize all device calls without deadlocking the
   existing QThreads or leaving armed bursts active.
7. Preserve the original notebook's intentional continuous-seed requirement
   for future EDFA experiments, but do not port its legacy `close()` override
   wholesale: the legacy base class explicitly issues `cmd_laser(DISABLE)`,
   whereas current IOPanel `CT400.close()` releases its handle without a
   laser emission command. Document and physically qualify shutdown behavior
   independently of synchronization.
8. The notebook's 1530–1565 nm example starts **below the EAD-2K-C
   amplifier's 1535 nm nominal operating range**. Do not connect the EDFA
   to the workflow or enable optical amplification in this milestone.
   Future optical/seed safety qualification is separate.

No oscilloscope evidence, physical-trigger capture, or calibration
constants have been obtained for M4-A. A future M4-B should record
physical timing evidence and only then approve a narrowly scoped,
operator-supervised orchestration path.

## Auditing the next raw capture (M4-A)

Once the notebook's safety/review gate and vendor trigger configuration are
physically qualified, a completed run's raw archive can be audited **offline**:

```powershell
uv run --locked python -m tools.audit_pm5020_sync_run "output/burst_runs/RUN_ID"
```

The audit verifies the archived raw PM CSV and CT400 sync file against the
metadata SHA-256 hashes; checks sample-buffer indices and native uint32 tick
fields; compares the PM sample count reported in the metadata; and reports
**both** explicit zero/one-based CT400 pulse-index mapping hypotheses.
Even if both candidates pass, the result remains `timing_qualified=False`.
A successful file integrity check must not be interpreted as agreement on
the trigger index origin or synchronization latency.

The original notebook's `PersistentLaserCT400.close()` override was
intentional: it preserved optical seeding to prevent the EDFA's low-input
shutdown between SHG scans. Current IOPanel `CT400.close()` also releases
the driver handle **without explicitly sending a laser-disable command**,
so duplicating that subclass is unnecessary in IOPanel. This is a code-level
comparison, not a physical verification of emission persistence on shutdown.
For the present M4 timing campaign, keep the EDFA out of the optical path.


## CT400 external synchronization driver support

The original laboratory `ct400.py` supplied with the notebook declares
`CT400_SetExternalSynchronization(uint64 handle, int32 enable) -> int32`
and calls it via `set_external_synchronization(Enable.ENABLE)`. M4 ports
the small method into both the abstract and concrete IOPanel CT400
interfaces, and records the simulated state in `DummyCT400`.

The IOPanel implementation explicitly passes `Enable.value`, checks for a
live native handle, and requires a zero return code. The lab PC's actual
`CT400_lib.dll` was checked on 2026-10-21: it exists, loads with
`ctypes.WinDLL`, and **exports `CT400_SetExternalSynchronization`**.
That was an export-only inspection: no CT400 initialization, scan, trigger
configuration or laser command was executed.

The function is now a **required** native entry point during CT400 DLL
signature initialization. If a different DLL lacks it, CT400 initialization
fails with an actionable `CT400InitializationError` rather than silently
operating without the requested capability. This intentionally changes the
compatibility floor for physical CT400 installations. Presence of an exported
symbol does **not** yet validate its ABI, accepted flags, output pulse
properties, timing or interaction with the PM5020.

The method must be called deliberately by a future supervised workflow:
no GUI action invokes it, there is **no automatic trigger enable** on
connection or scan start, and it does not change optical emission or
PM5020 burst configuration. The DLL export and its argument types come
from the operator-provided legacy code; exact CT400 DLL/firmware
compatibility, trigger electrical specifications and sample correspondence
must still be measured in the lab before coordinated scans.

Do not replace current `CT400.close()` with the uploaded legacy version:
the latter explicitly calls `cmd_laser(DISABLE, ...)` and would remove
the continuous optical seed required for the separate EDFA SHG workflow.
