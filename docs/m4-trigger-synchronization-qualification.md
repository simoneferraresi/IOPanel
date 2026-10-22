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
The CT400 pulse-counter numbering is now documented by the original manufacturer (see below),
but its direct correspondence to the PM5020 sample buffer remains to be
physically qualified.

M4-A replaces the implicit mapping with a **mandatory explicit
`index_base=0` or `index_base=1`** and checks for out-of-range indices,
duplicates, backwards sequence, missing indices and unexpected wavelength
order. Sparse mappings require `allow_sparse=True`. The result explicitly
retains `timing_qualified=False`: a syntactically valid mapping is **not
evidence** that the hardware trigger edges, index origin, or latencies match.
The manufacturer-supported candidate for CT400 pulse numbering is `index_base=0`.
Retain `index_base=1` only as an explicit diagnostic alternative until the
cross-instrument correspondence is qualified. Do not choose based on a smooth plot.

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

## CT400 manufacturer-confirmed zero-based pulse counter (22 October 2026)

**Source:** Yenista Optics, *CT400 Programming Guide*, CT400_PG_1.4v1.0
(10 June 2016), section 2.4.17, p. 37. This is the original CT400
manual, not an inference from a newer CT440.

The description of `CT400_GetNbDataPoints(uiHandle, &iDataPoints,
&iDiscardPoints)` explicitly identifies `iDiscardPoints` as the
trigger-pulse **index** of the first valid CT400 measurement, and
states that this index **equals the total number of spurious pulses
generated at the beginning of the sweep**. If `D` spurious trigger
pulses occur before the first valid pulse, those preliminary pulses
occupy indices `0 ... D-1` and the first valid pulse has index `D`.
This establishes the vendor-documented **zero-based CT400 external
trigger-pulse counter**. Section 2.2.8 (p. 20) states that
`CT400_SetExternalSynchronization` causes BNC A to generate a pulse
for each measurement.

The completed 22 October M4-D1 dark captures contain first CT400
sync-file indices 14, 19 and 20, respectively. These are consistent
with the vendor's discarded-pulse convention. However, the manual's
`CT400_ScanSaveWavelengthSyncFile` entry (section 2.4.7, p. 31)
describes writing a text file without documenting its column semantics
or explicitly stating that the exported first column is the same
pulse index returned via `iDiscardPoints`. The archived captures
also do **not** contain a direct `iDiscardPoints` readback.
Consequently that exported-column connection remains to be verified.

**Implementation policy:** use `index_base=0` as the
manufacturer-supported **provisional CT400-to-PM5020 mapping**:
`pm_buffer_index = ct400_pulse_index`, subject to the assumption
that PM5020 was armed before the *first* BNC A pulse and recorded
exactly one sample per pulse, with no missing or extra edges.
Retain `index_base=1` for diagnostics, and retain
`timing_qualified=False` until the complete trigger/count/latency
relationship has been physically verified. This documentation
**does not** assert calibrated wavelength accuracy or sample-time
scale, or retroactively rewrite immutable capture metadata.

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

1. Confirm the **actual CT400 sync-file column semantics**, including
   whether exported pulse indices implement the zero-based counter documented
   for `CT400_GetNbDataPoints` (manual p. 37). Check whether the PM5020
   records all emitted edges, including preliminary pulses, and whether
   missing/extra triggers occur.
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
**both** explicit zero/one-based PM-buffer mapping hypotheses, even though
CT400's pulse-counter convention itself is manufacturer-documented as zero-based.
Even if both candidates pass, the result remains `timing_qualified=False`.
A successful file integrity check must not be interpreted as agreement on
the CT400-to-PM5020 buffer correspondence or synchronization latency.

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
