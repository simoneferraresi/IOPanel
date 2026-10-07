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
