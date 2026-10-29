# CT400 wavelength-resolved output power

The legacy Yenista CT400 SDK declares `CT400_ScanGetPowerResampledArray`
(`uint64_t handle, double array[], int32_t size`) and the corresponding native
`CT400_ScanGetWavelengthResampledArray`. The output-power trace is read **after**
a successful or warning-classified scan and is aligned with the existing
resampled wavelength grid and detector traces.

IOPanel stores the vendor array unchanged as `laser_output_power_db` on the
immutable `ScanMeasurement`. The **dB unit is provisional**, at the user's
request, pending a lab comparison to the vendor software and an independently
calibrated reading. Do not infer dBm or mW, use the output trace as chip-facet
power, or convert/normalize detector values using it until the units and
measurement plane are verified. The existing scalar `final_pout` remains
separate and retains its prior semantics.

On new acquisitions with this channel available:

- CSV schema v2 adds the trailing `Laser_output_power_[dB]` column after
  `WL_[nm]` and four detector transfer-function columns.
- MAT schema v2 adds `laser_output_power_dB`.
- The importer strictly accepts both legacy five-column files and the exact
  six-column extension; older MAT files without the key still import.
- If an optional reference-power retrieval fails, the failure is logged and
  detector data remain available; the power column is **absent**, never filled
  with fictitious zero values.

The simulated DummyCT400 supplies a deterministic, clearly synthetic trace.
The new API is read-only and performs no additional scan, laser command, or
instrument initialization. Automated tests use fake native functions and the
simulator; no physical CT400 was operated. Validate the dB unit, physical
measurement plane, native returned sample count, and output values on the lab
PC before treating the data quantitatively.
