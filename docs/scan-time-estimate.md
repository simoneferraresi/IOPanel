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
down, matching its existing display. At the nominal duration the UI reports
“Estimated duration exceeded” while the scan is active. It does not claim that
the scan completed.

The progress bar remains indeterminate because the application has no supported
instrument telemetry for trustworthy percent complete. Cancellation replaces
the ETA with a stopping state until the worker finishes. The simulated CT400
uses a fixed test duration independent of configured sweep speed, so its
estimated remaining time is unavailable.

This is a planning aid, not a validated prediction of total scan execution time.
Physical lab measurements are still needed to assess how closely nominal sweep
time corresponds to observed CT400 behavior across wavelength ranges and speeds.
