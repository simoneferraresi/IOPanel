# CT400 scan time estimate

The scan panel shows a nominal wavelength sweep estimate alongside the actual
elapsed wall-clock time. It computes `abs(end wavelength - start wavelength) /
speed` in nm and seconds. The speed is the configured `scan_defaults.speed_nm_s`
value sent to `CT400.set_laser` when Connect is used. The editable Speed field
in the scan panel is recorded as requested acquisition metadata; changing that
field does not update the connected laser's configured sweep speed. An issue
should track making the requested and applied speeds consistent and visible.

The estimate describes only the nominal sweep. It excludes setup, stabilization,
CT400 preparation, data retrieval, and vendor processing. Actual elapsed time
includes the complete scan operation and can exceed the estimate. The remaining
duration rounds up to the next whole second; elapsed time continues to round
down, matching its existing display. “Estimated” communicates that this is a
nominal value. At the nominal duration the UI reports “Estimated duration
exceeded” while the scan is active. It does not claim that the scan completed.

The progress bar remains indeterminate because the application has no supported
instrument telemetry for trustworthy percent complete. Cancellation replaces
the ETA with a stopping state until the worker finishes. The simulated CT400
uses a fixed test duration independent of configured sweep speed, so its
estimated remaining time is unavailable.

The scan worker now emits a compact JSON timing summary. It measures each
existing setup command, the fixed stabilization delay (both configured and
observed duration), `ScanWaitEnd`, data retrieval, cleanup, and total worker
duration. It also records the requested range, Connect-time configured speed,
editable scan-panel speed and whether it differs, resolution, detector count,
sample count, backend and outcome. A separate UI log measures the delay from
worker completion to the scan thread's finished handler. Partial summaries
contain only reached stages and a `failed_stages` list. No detector arrays are
logged and no extra instrument calls are made.

The configured speed in the summary is the value the application passed to
`set_laser` at Connect, not a speed read back from the instrument. The scan
panel's editable speed remains requested acquisition metadata. The app has no
supported speed readback, and the timing summary cannot prove that the physical
laser applied the requested Connect-time value. The simulated backend has a
fixed test duration and does not model the configured wavelength sweep speed.

Stage measurements separate application-side preparation and known delay from
time spent in CT400 calls, but a DLL-call duration is only an observed call
duration. `ScanWaitEnd` includes the instrument's scan and the driver's wait
behavior; retrieval calls include their full call durations. The measurements
do not identify how much of a call was instrument execution versus transport,
vendor processing, or host-side work. GUI dispatch latency is reported
separately from worker duration.

The physical tests reported for this refinement found total acquisition time
above the nominal sweep estimate for the tested scans. The available report
does not include a repeated measurement series or per-stage timing records, so
it does not establish which stages account for the difference or how overhead
scales with scan settings.

No empirical overhead correction is applied. Calibration requires multiple
physical scans across short and long wavelength spans, several configured
speeds, resolutions, and detector selections. A lab validation should:

1. Record CT400/laser identity and firmware, application revision, configured
   speed, scan-panel speed, wavelength range, resolution, and selected detectors.
2. For a fixed resolution and detector selection, measure short and long spans
   at slow, middle, and fast configured speeds. Repeat each condition at least
   three times.
3. Hold span and speed fixed while varying resolution, then repeat while varying
   detector selection. Again collect at least three runs per condition.
4. Save each `Scan timing summary` log and record the operator-observed interval
   from Start Scan to the terminal UI status. Keep the separate UI completion
   timing with its corresponding worker record.
5. Compare operator-observed total time and worker duration with nominal sweep
   duration, then inspect setup, `ScanWaitEnd`, retrieval, cleanup, and UI
   completion durations separately. Report medians and run-to-run spread.

Only after measurements show repeatable relationships should a model be
evaluated for fixed setup costs or dependence on span, resolution, or detector
count. Validate any proposed model against separate runs before using it for an
ETA. Until then, the UI remains a nominal sweep estimate and physical validation
is outstanding.
