# CT400 connection-state laboratory acceptance

**Status: pending supervised validation on the laboratory PC.** Automated tests use fake CT400 devices and DummyCT400 only; they do not qualify physical hardware.

1. Have the laboratory operator confirm the CT400 model, installed driver/DLL, laser source, selected input, safe power, and lab safety procedure. Keep the laser output disabled until the operator directs otherwise.
2. Start IOPanel with the physical CT400 backend and wait for device initialization. Confirm the status reports **Ready (Disconnected)** and Scan and Monitor remain disabled.
3. Use the GUI **Connect CT400** action. While it is connecting, confirm Scan and Monitor stay disabled. After success, confirm both become available only when their selected input matches the input configured by Connect.
4. Select a different laser input in each panel. Confirm its action disables. Return to the connected input and confirm its action re-enables.
5. Use the GUI **Disconnect CT400** action. Confirm Scan and Monitor disable during disconnection and stay disabled after success. Reconnect and disconnect again to check repeated transitions.
6. If the operator can safely arrange a failed connection or disconnection without changing the approved lab procedure, confirm a failed connection never enables actions and a failed disconnection leaves them disabled. Record any failure; do not induce an unsafe laser state.
7. With the operator's approval, start a normal scan and use **Stop Scan**; confirm Stop remains available while active and Monitor stays unavailable. Repeat for Monitor's stop behavior if approved.
8. Record the IOPanel revision, CT400 model, driver/DLL version, connected input, observed states, operator, and any deviations. Stop and hand control back to the operator if observed behavior differs from the expected states.

Do not use the calibration foundation's physical Start control as part of this acceptance. Its physical-start gate remains out of scope for this change.
