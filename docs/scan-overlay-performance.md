# Scan overlay performance

## Method

`tools/benchmark_scan_overlays.py` creates deterministic schema-v2 CSV and
MAT files, imports them through `logic.scan_import.load_scan`, and exercises the
real `PlotWidget` without initializing a driver or instrument. The files contain
1500–1600 nm wavelength sweeps, smooth transfer functions, narrow resonances,
and nonfinite detector samples. The importer size limit is checked before each
fixture is used. The default matrix covers 10,000, 50,000, and 100,000 samples;
one and four detectors; and zero, one, two, four, and eight overlays.

The harness uses `perf_counter` around parsing, plot item creation, `setData`,
display configuration, legend add/clear/refresh, `autoRange`, crosshair work,
and text updates. It also times a widget grab as a static repaint proxy, ten
range changes as a pan/zoom proxy, and a Qt event-loop callback. `--mouse-events`
adds a burst of Qt mouse moves. Solid pens, disabled downsampling, hidden
legends, and disabled crosshair handling can be profiled independently with
the corresponding command-line options. The harness prints JSON and optionally
writes it with `--output`.

## Findings and changes

The baseline profile on this Windows host used Qt offscreen rendering at
1200×760. For 100,000 points × four detectors, adding eight overlays measured
about 370 ms in overlay setup. Rebuilding the legend took 322 ms, adding legend
rows took 243 ms, and repeated `autoRange` calls took 31 ms. Static repaint
proxy time was 232 ms and range-change p50 was 188 ms. The crosshair callback
itself was 0.27 ms p95. CSV parsing averaged about 0.50 s and MAT parsing about
25 ms, but parsing is separate from pointer and pan responsiveness.

After the changes, the same dimensions and overlay count measured about
158 ms for setup, 4.6 ms for a full legend refresh, and 19 ms in measured
`autoRange` calls. Static repaint proxy was 93 ms and range-change p50 was
90 ms. Crosshair callback p95 was 0.13 ms. The default optimized run displayed
about 77,400 curve points across 36 items, compared with about 3.6 million
points when downsampling was disabled: the static repaint proxy then rose to
1.46 s and range-change p50 to 552 ms. Pyqtgraph peak downsampling and clipping
therefore have a large measured effect. A controlled solid-pen run was slower
than the existing dashed/dotted styles (115 ms repaint and 113 ms range p50 at
eight overlays), so the scan patterns were retained.

The principal setup bottlenecks were full legend reconstruction and repeated
range calculations. New overlays now append only their own legend rows,
visibility and removal update existing entries incrementally, and the scan
legend is rebuilt only for structural changes that require it. Adding overlays
does not auto-range after the operator manually changes the view. Crosshair
updates are coalesced through a single-shot 40 ms Qt timer, retaining only the
latest pointer position. The readout shows the foreground scan, frozen
reference, and one explicitly focused overlay. Repeated identical text is not
laid out again.

All source wavelength and detector arrays remain unchanged and read-only.
Display downsampling uses pyqtgraph's peak-preserving method; it does not
replace or decimate the stored arrays. The synthetic 100,000-point/four-detector
fixture contains 3.2 MB of detector values and 0.8 MB of wavelengths per scan
before Python/Qt object overhead. Eight overlays plus the foreground therefore
retain about 36 MB of these source arrays. The benchmark allocates only the
requested matrix dimensions and no unbounded mouse-event queue.

## Interpretation and operator check

These numbers are operation-level comparisons, not a real interactive frame
rate. Qt used `QT_QPA_PLATFORM=offscreen`, so the measurements do not include a
physical Windows display compositor or operator input device. A burst of 100
Qt mouse moves with eight overlays resulted in two crosshair handler calls
after timer coalescing; with the crosshair event connection disabled it
resulted in zero. The timer bounds crosshair processing frequency to 25 Hz,
though a slow repaint can reduce the observed rate.

Before treating the reported real-world lag as resolved, run the application in
the operator's Windows desktop session with representative saved scans. Compare
one, two, four, and eight overlays while moving the pointer inside and outside
the plot, inspecting each focused overlay, zooming, panning, switching tabs,
and using the overlay manager. Confirm the view stays responsive and narrow
resonances are visible when zoomed. Do not infer desktop smoothness from CI or
offscreen timings.

Example commands:

```powershell
uv run --no-sync python -m tools.benchmark_scan_overlays --output overlay-profile.json
uv run --no-sync python -m tools.benchmark_scan_overlays --points 100000 --detectors 4 --mouse-events --output overlay-events.json
uv run --no-sync python -m tools.benchmark_scan_overlays --points 100000 --detectors 4 --no-crosshair --mouse-events --output overlay-no-crosshair.json
uv run --no-sync python -m tools.benchmark_scan_overlays --points 100000 --detectors 4 --no-downsampling --no-legend --output overlay-no-downsampling.json
```
