# Camera performance and image quality

IOPanel prefers an OpenCV-compatible 8-bit mono format when available. It
starts cameras in continuous, untriggered acquisition and does not change ROI
or request an acquisition frame rate at startup. Optional performance features
are inspected separately because GenICam feature names and availability vary
with camera firmware and transport layers.

The Allied Vision Mako G-125B is specified for about 30.3 frames/s at its full
1292 × 964 sensor size. Cropping to a shorter vertical ROI may permit higher
acquisition rates, but the supported dimensions, increments, offsets, and
sustained rates must be queried and measured on each camera. ROI cropping is
different from binning: cropping reads a smaller sensor region, while binning
combines neighboring sensor pixels. IOPanel does not enable either automatically.

Acquisition FPS and display FPS describe different stages. The camera can
deliver frames faster than the GUI paints them. The presentation pipeline keeps
the newest frame and coalesces obsolete presentation work, so high-rate
acquisition does not promise that every frame is painted. Camera timestamps and
frame IDs should be used for acquisition pacing; GUI FPS is a display metric.
Screenshots preserve the full acquired ROI at native resolution and omit the
preview FPS overlay.

### Camera display pipeline characterization

The standalone `tools/camera_display_characterize.py` diagnostic reports camera
acquisition FPS, conversion throughput, `presentation_call_fps`, and Qt paint
events as separate stages. `presentation_call_fps` measures calls into the
production presentation method. Paint reporting preserves the event count and
timestamp interval statistics, and distinguishes
`paint_events_per_wall_second` (count divided by the full measurement duration)
from `active_span_paint_event_fps` (cadence between the first and last captured
paint timestamps). `paint_timestamp_span_seconds` and
`paint_timestamp_coverage_fraction` show how much of the measurement interval
those timestamps cover. Low coverage adds the machine-readable observation
`paint_timestamps_do_not_span_measurement`; it does not invalidate acquisition,
conversion, presentation, heartbeat, or CPU measurements. QWidget paint events
can depend on window exposure and occlusion, and Qt or the operating system can
coalesce paint work. These paint metrics do not measure monitor refresh rate.
Latest-frame coalescing is intentional: the GUI presents recent images instead
of requiring every acquired frame to appear on screen. The diagnostic also
counts conversion-worker submissions and completions, converted images accepted
by CameraPanel, and GUI-mailbox coalescing independently; these counts can
differ. It reports event-loop heartbeat timings and process CPU time divided by
wall time; the latter is not a machine-wide profiler.

The tool requires one or two explicit physical camera IDs and an operator-visible
window. It deliberately uses the real `VimbaCam.open()` path for every selected
camera, which applies IOPanel's standard startup configuration (acquisition/trigger and auto
selectors, gamma, and preferred pixel format). Before opening that path, the
diagnostic snapshots each readable affected feature, ROI, and frame-rate
settings; after closing the stream it restores and independently reads them
back. Manual Gain and Exposure values are not written. Restoration proceeds
best-effort in dependency order: make the current frame rate legal if it was
changed, restore pixel format, restore ROI, restore the exact original frame
rate, restore independent startup selectors and gamma, then restore the
original frame-rate enable state. Each failure is reported while later
restoration steps continue.

ROI and maximum frame-rate requests change camera settings and require
`--authorize-settings-changes`. A nonzero exit status indicates a measurement,
restoration, or cleanup failure. The diagnostic performs no CT400, laser,
piezo, or NIC operations and does not alter normal application display
behavior.

```powershell
uv run python tools/camera_display_characterize.py --camera-id <CAMERA_ID> `
  --window-size 960x720 --duration 30 --output display-characterization.json

uv run python tools/camera_display_characterize.py --camera-id <CAMERA_ID> `
  --roi 1292x480 --maximize-frame-rate-for-roi `
  --authorize-settings-changes --duration 30 --output display-roi.json

uv run python tools/camera_display_characterize.py `
  --camera-id <TOP_ID> <SIDE_ID> `
  --camera-roi <TOP_ID>=1292x240 --camera-roi <SIDE_ID>=1292x480 `
  --maximize-frame-rate-for-roi --authorize-settings-changes `
  --window-size 1800x720 --duration 30 --output dual-display.json
```

In dual-camera mode, `--roi WIDTHxHEIGHT` is accepted as a shorthand that
applies the same ROI to both cameras. Use `--camera-roi CAMERA_ID=WIDTHxHEIGHT`
for independent ROIs. Both panels appear side by side and use their production
acquisition and conversion paths. The diagnostic waits until both panels have
displayed a frame, then measures them over one interval with one GUI heartbeat
and one process CPU metric. ROI writes and maximum frame-rate queries are
independent per camera. Exposure and gain are not modified, so Side may remain
exposure-limited at a small ROI. Settings and restoration readbacks are reported
independently per camera.

## Exposure and gain

Longer exposure collects more light but can increase motion blur and reduce the
available frame interval. Gain amplifies the signal and its noise. Maximum gain
may therefore be undesirable, but specifications alone cannot establish that a
lower-gain setting is better for the lab scene. Compare settings at similar
useful brightness and measure temporal noise, SNR, clipping, feature stability,
frame pacing, and motion blur. Static fine alignment and dynamic coarse
positioning may need different operating points. Do not infer a preferred
setting until physical characterization is complete.

The manual Gain control uses the feature range reported by the camera. Auto
Exposure and Auto Gain remain one-shot operations; no continuous auto mode or
quality-first controller is introduced here.

## Read-only and operator modes

Physical GigE cameras can appear in Vimba after the system context has already
been entered. Production camera opening and current diagnostic tools therefore
poll for the exact requested camera ID for up to ten seconds, at 250 ms
intervals, and stop as soon as it appears. The manual discovery dialog scans
on a worker and updates its list as devices arrive. The older `vimba_b2`,
`vimba_b3`, and `vimba_b4` qualification scripts retain their fixed ten-second
wait because they are historical, manually operated lab records; current
application and diagnostic paths use bounded polling instead.

Run the read-only capability report for one explicitly selected camera:

```powershell
uv run python tools/camera_capabilities.py --camera-id <CAMERA_ID>
```

The report does not start acquisition or write camera features. A prepared
operator-supervised timing test is:

```powershell
uv run python tools/camera_characterize.py --camera-id <CAMERA_ID> --stream-test --duration 30
```

For simultaneous timing, pass both explicitly selected IDs in one run:

```powershell
uv run python tools/camera_characterize.py --camera-id <TOP_ID> <SIDE_ID> --stream-test --duration 30
```

A one-camera or simultaneous two-camera ROI timing comparison requires explicit
authorization and restores each original ROI after the run:

```powershell
uv run python tools/camera_characterize.py --camera-id <CAMERA_ID> --stream-test `
  --roi 1292x480 --authorize-settings-changes --duration 30
```

An exposure/gain sweep requires an explicit settings-change authorization and
values valid for the queried camera range and increment:

```powershell
uv run python tools/camera_characterize.py --camera-id <CAMERA_ID> --quality-sweep `
  --authorize-settings-changes --gain-values <QUERIED_VALUES> `
  --exposure-values-us <QUERIED_VALUES> --frames 200 --output results.json
```

The existing one-shot auto modes can be measured separately, with restoration
of the original exposure, gain, and auto selectors:

```powershell
uv run python tools/camera_characterize.py --camera-id <CAMERA_ID> --auto-once exposure `
  --authorize-settings-changes --frames 200
```

Use initial short exposure/high gain as a reference. For each camera, query the
actual Gain and Exposure ranges and increments first. Keep ROI, pixel format,
lighting, aperture, focus, scene, frame count, and camera temperature as stable
as practical. First vary gain at a fixed exposure to characterize response;
then compare lower-gain/longer-exposure pairs selected to produce similar
median signal brightness. Keep exposure fixed during the gain-only comparison;
keep gain fixed during the exposure-only comparison. For paired comparisons,
change only the intended exposure/gain pair and report both values. Record
motion blur separately using a repeatable moving target or stage motion. Preserve
the original camera settings and verify restoration before returning the camera
to service.

For a conservative first sweep, derive values from the queried limits and
increments rather than assuming a dB scale. For Gain, start with the current
value, the minimum, and one or two intermediate points at roughly one-quarter
and one-half of the reported range; align each point to the reported increment
and omit duplicates. Keep Exposure fixed for that sweep. For Exposure, start
with the current value and two modest increases (about 1.5× and 2×), clamp them
to the queried range, align them to the reported increment, and omit a point if
the queried acquisition timing or scene motion makes it unsuitable. Keep Gain
fixed for that sweep. Then choose paired values from those measurements that
have comparable median signal brightness; compare noise, SNR, clipping,
frame-interval percentiles, and blur. This is a measurement sequence, not a
recommended operating point.

ROI timing comparisons should keep pixel format, exposure, gain, lighting,
transport path, and duration fixed while testing full sensor and valid centered
ROIs. Run each camera alone and then both together. Confirm ROI, offsets,
frame-rate limits, delivered FPS, frame interval percentiles, frame-ID gaps,
and incomplete frames after each change.

## GigE host checks

The report includes camera-side packet size, packet delay, throughput limit, and
interface identifiers where the SDK exposes them. Host network changes remain
manual: check the dedicated camera interface, link speed, jumbo packet/MTU
support end-to-end, receive buffers, interrupt moderation, and energy-saving
settings. Do not change NIC properties during ordinary diagnostics. No packet,
bandwidth, or operating-system transport setting is automatically tuned.
The prepared stream tool records delivered/incomplete frames and frame-ID gaps.
It does not claim packet resend or loss counters where the installed Vimba stream
API does not expose them.

The physical tools are prepared for a later authorized lab session; running
them is separate from software tests. Capability reports and synthetic metrics
cannot establish the physical cameras' sustained ROI rates, transport quality,
noise, or preferred exposure/gain points.

## Automated read-only baseline

Run the complete baseline for explicitly named cameras with:

```powershell
.\.venv\Scripts\python.exe tools\camera_lab_validate.py `
  --camera DEV_000F315B9CE1=Top `
  --camera DEV_000F315BA8F9=Side `
  --baseline --duration 30 --network-audit
```

The runner requires exact camera IDs, shares one bounded discovery deadline,
audits capabilities without starting acquisition, then measures each camera
alone and both together. Streaming is skipped unless the existing camera
state reads `AcquisitionMode=Continuous` and `TriggerMode=Off`; it does not
change those settings to make a test pass. Results go to a timestamped
`camera-validation-results` directory by default or to `--output-dir`. JSON
stage statuses are `PASS`, `PASS WITH OBSERVATIONS`, `FAIL`, or `NOT RUN`.
Exit code 0 means required stages passed, 1 a validation failure, and 2 an
environment precondition failure. The optional network audit uses read-only
PowerShell queries and records unavailable optional fields as observations.

The default runner never writes camera features or NIC settings. GenICam enum
values may wrap native ctypes state, so reports must not use recursive
`dataclasses.asdict()` serialization. The capability layer keeps raw values
for internal logic and converts them only at the JSON report boundary, keeping
numeric values numeric and representing enum values by their semantic names.

ROI, exposure/gain sweeps, and Auto Once are later opt-in characterization
stages and require `--authorize-settings-changes`. They are not part of a
baseline run; see `camera_characterize.py` for the operator-supervised
restoration workflow.

For a later settings-changing run, the lab runner requires explicit authorization
and delegates the operation to the existing characterized workflow. Examples
(the commands below are not part of the baseline):

```powershell
.\.venv\Scripts\python.exe tools\camera_lab_validate.py `
  --camera DEV_000F315B9CE1=Top --roi 1292x480 `
  --authorize-settings-changes --duration 30

.\.venv\Scripts\python.exe tools\camera_lab_validate.py `
  --camera DEV_000F315B9CE1=Top --quality-sweep `
  --authorize-settings-changes --gain-values 0,6,12 `
  --exposure-values-us 1000,2000 --frames 200

.\.venv\Scripts\python.exe tools\camera_lab_validate.py `
  --camera DEV_000F315B9CE1=Top --auto-once exposure `
  --authorize-settings-changes --frames 200
```

These modes must only be used after reviewing the capability report. The
characterization workflow snapshots and restores settings; inspect its output
for restoration confirmation before returning a camera to service.
