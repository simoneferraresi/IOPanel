# CT400 output-power plot overlay

The wavelength-scan plot provides a **checkable laser toolbar icon** to display
the current completed scan's CT400 output-power trace (provisionally dBm).

- Off by default, disabled if no finite output-power samples were acquired.
- The trace has its **own right-hand Y-axis** labeled `Laser output power (dBm)`.
  The detector transfer functions remain on the existing left-hand dB axis.
- Both views share the same wavelength/X range, but have **independent Y
  ranges**; enabling the output trace must not rescale detector transmission.
- The existing wavelength crosshair appends a `Laser output: … dBm` readout
  when the trace is enabled; nonfinite readings appear as `n/a`.
- It works for completed measurements and imported CSV/MAT scans that contain
  `Laser_output_power_[dBm]` / `laser_output_power_dBm`.
- It displays **only the current scan**. Frozen references and imported
  overlays still display their detector transfer functions only.
- Hidden/visible state never changes the acquired arrays or exported data.
  No new hardware calls occur when clicking the control.

The control reuses the existing `:/icons/laser.svg` resource; no new
compiled Qt resources or external dependencies are required.

Tests are hardware-independent Qt tests. The reference power's physical
measurement plane and assumed dBm units still require lab verification.
