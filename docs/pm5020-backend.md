# PM5020 backend

The PM5020 backend provides a small device contract, a deterministic simulator,
and an optional physical adapter. The standalone monitoring panel is available
as the **Thorlabs PM5020** tab. It operates independently of CT400 scans and
does not coordinate a CT400 scan.

## API and data

`TLPMXPM5020` and `DummyPM5020` implement `AbstractPM5020`. Channel readings
and burst power values are in watts. Single-reading timestamps are timezone
aware UTC datetimes. Burst timestamps are retained as unsigned 32-bit
instrument ticks: the reference notebook does not document their time scale or
epoch. Burst results carry separate CH1 and CH2 arrays with an enforced shared
sample count.

The physical adapter loads the configured `TLPMX.py` only after an operator
selects **Connect PM5020**. The backend mode is configured in `[Instruments]`;
the default is `simulation`. Physical mode never connects during startup. Device calls are serialized. A separate
read-only `PM5020State` (`DISCONNECTED`, `IDLE`, `ARMED`, `RETRIEVING`, or
`ERROR`) is shared by the simulator and physical adapter. `is_connected()` may
remain true in `ERROR` while the driver handle exists; `is_ready` is true only
in `IDLE`. State inspection does not wait for a blocking vendor call, so a
consumer can observe `RETRIEVING`. Once a burst is armed, monitoring reads are
rejected until retrieval completes. Failed or cancelled physical burst
retrieval requires closing and reconnecting to establish a known state.

`retrieve_burst(max_samples=None)` uses the active configuration's capacity.
An explicit limit must be positive, no greater than 15,000, and no greater than
the configured capacity. Invalid local limits are rejected before any device
call and leave the burst `ARMED`; a device-reported count beyond the effective
limit fails retrieval and moves the backend to `ERROR`.

## Verified burst behavior

The authoritative laboratory notebook's third-to-last code cell sets CW mode,
disables autorange and the input filter, configures power measurement on both
channels, and sets trigger source 3, delay 0, one sample per trigger, and one
average. It calls `startBurstArrayMeasurement`, gets the recorded count through
`getBurstArraySamplesCount(byref(c_uint32))`, and retrieves timestamp (`c_uint32`)
and CH1/CH2 power (`c_float`) buffers with `getBurstArraySamples` in 100-sample
chunks. The configured safety capacity is 15,000 samples; the adapter validates
the count and allocates each chunk to its requested length, including a partial
last chunk.

The notebook comment says `getBurstArraySamples()` aborts the measurement, but
the comment alone does not establish that as a documented wrapper guarantee.
This backend does not depend on the abort as its completion signal. Physical
behavior, timestamps, trigger edges, chunk retrieval, and recovery still need
validation with the laboratory instrument before experimental use.

Single-channel optical power reads use the official Thorlabs Python example's
`measPower(byref(c_double), channel)` signature. The official PM5020 example
also demonstrates channel-specific wavelength and range settings. The locally
installed TLPMX wrapper and native SDK are not available in the development
environment, and the documented API used here does not provide a verified
typed classification for sensor absence versus timeout, disconnection, or
other vendor failures. No vendor error-code mapping is inferred. Therefore any
vendor exception during a measurement or configuration operation is treated
conservatively as instrument-wide `ERROR`; the simulator follows the same
recovery behavior for configured missing sensors and injected communication
failures. Close and reconnect to recover.

## Windows requirements

Install Thorlabs Optical Power Meter software/SDK on the laboratory Windows
machine. Configure `pm5020_wrapper_path` and `pm5020_dll_path` independently.
IOPanel copies the configured files into a per-user `%LOCALAPPDATA%/IOPanel/pm5020-sdk`
cache on first physical connection. The cache directory name includes both
source file hashes, and each staged file is hash-checked and atomically
replaced if it does not match. This gives the vendor wrapper and native DLL a
shared directory without changing the vendor installation. DLL search-path
handles remain active through native driver shutdown and are released afterward,
including failed connection and close paths. The wrapper is imported lazily
and is never added to `PYTHONPATH`.

The Thorlabs wrapper and DLL are proprietary SDK files and are not bundled or
committed. IOPanel selects `TLPMX_64.dll` or `TLPMX_32.dll` according to the
Python process bitness. Install the matching Thorlabs VISA runtime and use
matching wrapper/DLL architectures. Missing source files, staging failures,
architecture problems, and missing native dependencies produce actionable
connection errors. The application-managed copy does not validate vendor
signatures or qualify SDK compatibility; it is a file staging mechanism only.

Portable code defaults use simulation and empty resource, wrapper, and DLL
paths. In physical mode, leaving **both** SDK paths empty preserves the legacy
lazy `import TLPMX` behavior, provided the vendor wrapper is importable and
its native DLL can be loaded by that wrapper. Supplying exactly one SDK path
is rejected; configure both when the files are in separate directories.
The checked-in `config.ini` contains this site's configured resource
and SDK paths; adjust them for other installations.
Example laboratory settings:

```ini
[Instruments]
pm5020_backend = simulation
pm5020_resource_name = USB0::0x1313::0x80BB::M01213392::INSTR
pm5020_wrapper_path = C:/Program Files (x86)/IVI Foundation/VISA/WinNT/TLPMX/Examples/Python/TLPMX.py
pm5020_dll_path = C:/Program Files/IVI Foundation/VISA/Win64/Bin/TLPMX_64.dll
```

The simulator and all backend imports work without that SDK. Unit tests use a
fake wrapper and do not establish compatibility with a physical meter.

## Standalone monitoring panel

The **Instruments** menu owns PM5020 connection. It shows **Connect PM5020** or
**Disconnect PM5020** from worker-confirmed state and disables the action while
an operation is pending. The measurement tab follows the CT400 tabs with
a plain, non-scrollable QWidget/QVBoxLayout control column, using native Fusion
QGroupBox styling rather than forced colors or palette overrides, plus an
expanding right column containing two vertically stacked live plots. The plots
remain visible beside the controls. At very short window heights, operators
should expand the lower tab region to avoid clipping controls.
Instrument state lives in the permanent
bottom-right PM5020 status-bar indicator (with resource and backend details in
its tooltip); connection errors stay visible in the controls and log. Simulation
remains explicitly labeled so simulated readings are never mistaken for
physical readings. Connect/Disconnect stays in the Instruments menu. Simulation and physical mode are
selected by `pm5020_backend = simulation` or `physical` in `[Instruments]`;
resource and SDK paths are configured there as well. No physical meter is
connected until the operator chooses **Connect PM5020** from the menu.

Connect deliberately through the **Instruments** menu after configuring the
physical or simulation backend and SDK paths in `config.ini`. On a confirmed
connection, valid idle channel settings synchronize automatically through the
worker. Start becomes available only after the worker acknowledges the latest
configuration; monitoring still requires an explicit **Start** action. Editing
settings while disconnected is local and does not access the meter. Invalid
inputs are reported without sending them to the worker, and a rejected
configuration remains unapplied until the operator corrects a setting. A
terminal backend error requires deliberate disconnection and reconnection.
Each channel combines its enable checkbox and large Geist Sans CH1/CH2 label
with the Geist Mono measurement and display-unit selector on a single row
(disabled channels are not read). The validated wavelength field displays
whole-number defaults with one decimal place (1550.0 and 775.0 nm), while
preserving explicitly entered finer wavelength precision rather than rounding
instrument calibration settings. Like the CT400 panels, the instrument menu
uses connect/disconnect/spinner icons and confirmed connection state.
Automatic or manual range remains on one row, with a W/mW/µW/nW selector. Changing
the manual-range display unit preserves the requested physical range in watts.
Wavelengths are editable; 1550 nm and 775 nm are example defaults only. The GUI
checks that wavelength and manual range are positive and finite, but sensor
heads have different supported wavelength and power ranges. Vendor-side
validation remains authoritative and any rejected setting is shown in the
panel. Start remains disabled until the worker confirms all requested channel
settings for the current connection. Editing wavelength, range, filter, or the
enabled-channel selection invalidates that confirmation; display-unit changes
do not. Input filtering and automatic or manual ranging use the shared backend
configuration. A channel excluded from monitoring is not read. The low-pass
icons control the PM5020's native input filter; they do not filter samples in
software after acquisition. The worker acknowledges the requested input-filter
state along with the other channel settings.

Start monitoring with an **Update every** value in milliseconds or seconds,
representing the same 250–5000 ms requested interval. Changing the time unit
preserves the requested duration. A valid interval change during acquisition
updates the existing worker-owned timer without ending the session, clearing
history, or triggering an immediate read. While paused, a valid interval is
stored and used by the explicit Resume action; it does not resume acquisition.
Hardware channel settings remain locked during monitoring and pause. Stop
monitoring before changing wavelengths, ranges, enabled channels, or native
input filters. Reads run sequentially on one persistent
worker thread; CH1 and CH2 timestamps may differ. Actual sampling also
depends on instrument latency. Each channel has an independent Y-axis and
display-unit selector beside its numeric readout; the selected units apply
immediately to the Y-axis and stored trace without altering the underlying
watts data or channel settings. Plot history changes are presentation-only and
do not modify the meter. X-axis elapsed-time windows stay aligned.
Zero and negative measurements have no dBm display. History is limited to
the selected time window and a 2,400 point ring buffer per channel.

**Pause** stops new requests but keeps the session and history. **Resume**
continues that session. **Stop** ends monitoring, preserves its history, and
leaves the meter connected. A successful new Start begins a fresh elapsed-time
session and clears the previous plot and latest-reading labels. Each plot's
eraser toolbutton clears only that channel's trace and latest-reading display,
without stopping monitoring, clearing the other channel, or disconnecting.
**Disconnect PM5020** in the Instruments menu stops monitoring and closes the backend. After a communication
or configuration failure that leaves the backend in `ERROR`, monitoring stops
and the UI requests deliberate disconnect/reconnect recovery; it does not
silently reconnect.

The panel uses a long-lived `TaskRunner`/`QThread` worker. That worker creates
and owns exactly one backend for a connection and serializes all configuration,
reads, and close operations. A worker-thread Qt timer prevents polling requests
from accumulating. The GUI receives readings and states through Qt signals.
Application shutdown queues an orderly close and waits asynchronously for the
worker thread to finish; a blocked native call cannot be interrupted safely.

The simulator path, unit conversion, UI, and worker lifecycle are covered by
automated tests. Physical connection, sensor compatibility, vendor-side range
validation, and physical sampling rate have not been exercised by this panel
change. Hardware-triggered burst acquisition and synchronized SHG workflows
remain future milestones. Recording/export and persistent panel settings are
also deferred.
