# CT400 initialization retry: physical acceptance checklist

This checklist is for a later, attended lab session. Driver-free tests and the
simulation-only packaged smoke do not qualify CT400 hardware behavior. Do not
run these steps during offline development.

- Confirm the lab's approved safe-state procedure and operator are present.
- With the CT400 powered off or disconnected, start the application using the
  normal physical profile. Confirm the UI reports initialization unavailable
  and leaves Connect, Scan, Monitor, and Alignment unauthorized.
- Restore the CT400 and select **Instruments → Retry CT400 Initialization**.
  Confirm the retry status is visible and the action cannot start an overlapping
  attempt.
- Confirm successful native initialization is reported as **Ready
  (Disconnected)**. It proves that `CT400_Init` returned a usable handle; it
  does not prove the laser is connected, enabled, or in a safe optical state.
- Confirm no retry automatically connects a laser input or enables laser output.
- Repeat one failed attempt and one later successful retry only when the
  operator-approved setup permits it. Confirm the old device is not closed while
  a scan, monitor, connection, alignment, or recovery operation owns it.
- Close the application during a deliberately slow initialization only if the
  vendor call and lab procedure allow it. Confirm shutdown waits for the worker
  to finish and does not terminate the native call.
- Record CT400 model, DLL version and architecture, Windows version, result,
  operator, and any vendor error details available from the device. Do not infer
  error-code detail that the DLL does not provide.
