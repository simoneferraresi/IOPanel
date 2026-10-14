# CT400 scan time estimate

The physical scan panel now distinguishes two estimates:

- **Lab-calibrated total duration** where physical timing measurements
  support the particular instrument/settings combination.
- **Nominal sweep duration** (fallback) everywhere else, computed as
  `abs(end - start) / configured speed`; this deliberately excludes
  preparation, waiting, processing, and cleanup.

The scan panel uses the application's Connect-time configured speed
(`scan_defaults.speed_nm_s`), **not** the editable requested speed field in
the panel. That field is acquisition metadata and does not command the laser.
The configured speed is a submitted command, **not device readback**.
The tooltip identifies the estimate type. The simulated CT400 has no ETA.

## Empirical model (lab calibration, October 2026)

Four independently started operator-supervised campaigns on the lab CT400
provided 51 real completed scan timings: 12 repetitions each of cases A, B, C,
and E at 10 nm/s, and three repetitions of D at 5 nm/s. All executed runs
returned their expected number of samples and recorded no cleanup failures.
Twelve optional four-detector F records were explicitly skipped; therefore
**multi-detector scan duration is not calibrated**.

The fit includes completed success outcomes and complete-sample, nonfatal
warning outcomes (vendor codes 117 and 101), with warnings retained distinctly
in the raw journal. Code 117 was observed when no optical input was coupled;
all three 5 nm/s runs returned code 101 (`scan speed too low`), so that speed
correction should be considered *provisional*, not device speed verification.
The raw campaigns are not checked into this repository.

The bounded empirical estimate, in seconds, is:

```text
T = span_nm / configured_speed_nm_s
    + 5.45
    + 0.0000824 * ceil(1000 * span_nm / resolution_pm)
    + (2.04 if configured_speed_nm_s == 5 else 0)
    + (0.42 if resolution_pm == 10 else 0)
```

This formula describes **total GUI start-to-completion time** rather than
physical sweep time. It is enabled only for one detector (DE1), with start/end
wavelengths within 1530–1565 nm, span from 5 to 35 nm, and these exact
`(Connect-speed nm/s, resolution pm)` pairs: `(10,1)`, `(10,10)`, `(5,1)`.
It does not extrapolate to other speeds, larger scan ranges, other resolutions,
or additional detectors. Any unsupported setting falls back to the clearly
labelled nominal estimate instead of silently extrapolating.

| Case | Speed | Resolution | Measured mean total duration | Model |
| --- | --- | --- | --- | --- |
| A (5 nm span) | 10 nm/s | 1 pm | 6.359 s | 6.362 s |
| B (15 nm span) | 10 nm/s | 1 pm | 8.199 s | 8.186 s |
| C (35 nm span) | 10 nm/s | 1 pm | 11.835 s | 11.834 s |
| E (35 nm span) | 10 nm/s | 10 pm | 9.660 s | 9.658 s |
| D (35 nm span) | 5 nm/s | 1 pm | 17.382 s | 17.374 s |

Rounded coefficients yield a 0.046 s mean absolute training error over the 51
measurements. With **one entire 10 nm/s campaign held out at a time**, mean
absolute prediction errors were 0.033–0.080 s. These figures describe only the
narrow, deliberately repeated conditions; they do **not** establish precision
for arbitrary new scans, computers, vendor versions, detector arrangements or
laser operating conditions. The 5 nm/s offset has not been independently
validated on a second campaign.

## Behavior and limitations

Remaining time rounds up to whole seconds and elapsed time rounds down.
An estimate being exceeded is **not** proof that the scan failed or finished.
The progress bar remains indeterminate: no supported instrument telemetry
establishes a trustworthy scan-completion percentage. Stop displays a stopping
state until the worker actually finishes; the estimator does not affect scan
commands, concurrency, cancellation, hardware safety, or ownership.

The scan worker emits structured per-stage timing logs for setup commands,
stabilization, `ScanWaitEnd`, retrieval, and cleanup. A separate completion
measurement captures GUI dispatch latency. A DLL-call wall time does not
independently distinguish hardware execution from transport, driver or host
processing.

The operator-supervised campaign writer retains both the **nominal** sweep and
**actual** GUI durations. Its summary includes timing-valid warnings 101/117
only when expected and returned sample counts match and no failure flags
occurred. The summary still distinguishes `successful_measurements` from
`timing_valid_measurements` and `timing_valid_warnings`. Other vendor
warnings and failed/incomplete samples remain excluded.

Further physical qualification should repeat case D in an independent
campaign, obtain multi-detector F timings with approved wiring, verify the
warning-101 implications with the manufacturer if material, and cover scan
ranges, speeds, wavelengths and resolutions outside this initial bounded fit.
