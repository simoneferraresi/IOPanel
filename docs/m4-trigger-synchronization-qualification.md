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
5. Implement and review CT400's external synchronization API in the
   current driver before any live coordinator. It appears in the notebook
   but not yet in the supported `hardware.ct400.CT400` methods.
6. Keep CT400 and PM5020 ownership independent until a single acquisition
   coordinator can serialize all device calls without deadlocking the
   existing QThreads or leaving armed bursts active.
7. Do not port the notebook's `PersistentLaserCT400.close()` override,
   which intentionally skips laser-disable cleanup. Its behavior is not an
   acceptable shutdown foundation.
8. The notebook's 1530–1565 nm example starts **below the EAD-2K-C
   amplifier's 1535 nm nominal operating range**. Do not connect the EDFA
   to the workflow or enable optical amplification in this milestone.
   Future optical/seed safety qualification is separate.

No oscilloscope evidence, physical-trigger capture, or calibration
constants have been obtained for M4-A. A future M4-B should record
physical timing evidence and only then approve a narrowly scoped,
operator-supervised orchestration path.
