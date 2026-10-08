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
and text updates. It also times repeated widget grabs as a static repaint
proxy, ten range changes as a pan/zoom proxy, a Qt event-loop callback, and
opening the overlay manager. It reports process CPU time and Windows working
set. `--mouse-events` adds a burst of Qt mouse moves. Solid pens, finite-value
handling, line widths, cosmetic pens, antialiasing, disabled downsampling,
hidden legends, and disabled crosshair handling can be profiled independently.
It prints JSON and optionally writes it with `--output`.

## Native Windows follow-up

The follow-up measurements below were taken on the same Windows x64 host in an
interactive RDP session, using Qt's native `windows` platform plugin, not the
offscreen plugin. They use distinct schema-v2 scans with shifted resonance
centers and wavelength intervals, plus alternating detector subsets. Each
profile contains a 100,000-point/four-detector foreground and two or four
overlays; the plotted overlays include mixed NaN and Inf samples. The current
and candidate configurations used the same benchmark, machine, data generator,
window dimensions, and Qt event loop.

| Overlays | Configuration | Displayed curve points | Repaint p50 / p95 | Range-change p50 / p95 | Event callback p95 | Manager open | Working set |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | Previous 1.4 px noncosmetic pens; `finite` + skip check | 23,482 | 136 / 180 ms | 210 / 220 ms | 10.2 ms | 10.1 ms | 284 MB |
| 2 | 1 px cosmetic patterned pens; automatic finite handling | 23,482 | 27.3 / 29.8 ms | 34.8 / 36.6 ms | 1.2 ms | 9.4 ms | 284 MB |
| 4 | Previous 1.4 px noncosmetic pens; `finite` + skip check | 38,364 | 278 / 297 ms | 609 / 624 ms | 29.5 ms | 9.4 ms | 310 MB |
| 4 | 1 px cosmetic patterned pens; automatic finite handling | 38,364 | 27.7 / 31.5 ms | 42.0 / 46.6 ms | 1.7 ms | 9.8 ms | 307 MB |
| 8 | Previous 1.4/1.8 px noncosmetic pens; `finite` + skip check | 67,732 | 624 / 653 ms | 1,794 / 2,025 ms | 99.0 ms | 12.3 ms | 299 MB |
| 8 | 1 px cosmetic patterned pens; automatic finite handling | 67,732 | 34.6 / 36.2 ms | 52.7 / 56.3 ms | 1.5 ms | 11.6 ms | 295 MB |

The static repaint proxy is repeated `QWidget.grab()` time. Range-change timings
use `ViewBox.setRange()` and process Qt events; they are not physical mouse-drag
measurements. Event callback latency is a zero-delay `QTimer` callback. Manager
opening includes construction and modal display closed by a timer. Process CPU
time over each run decreased from 5.39 to 1.94 seconds with two overlays,
11.02 to 1.86 seconds with four, and 29.23 to 2.58 seconds with eight. The
reported CPU-to-wall ratio is per-core equivalent and can exceed 100% if
multiple threads use CPU simultaneously. Working set was essentially
unchanged, varying by about 1–4 MB between matched runs. The fixture source
arrays for the foreground plus four overlays occupy roughly 20 MB before Qt
and Python overhead; eight overlays use about 36 MB of source arrays.

### Finite values

The old overlay options explicitly combined `connect="finite"` with
`skipFiniteCheck=True`. In a direct Qt path probe with trailing finite samples
after a NaN interval, pyqtgraph 0.14 dropped those trailing valid samples under
that combination. With finite checking enabled, the path contains separate
subpaths around the missing interval. The installed version's documented
automatic `connect="auto"` mode checks once, uses the all-finite fast path when
possible, and uses finite connections when NaN or Inf is present. The
[pyqtgraph 0.14 PlotDataItem documentation](https://pyqtgraph.readthedocs.io/en/pyqtgraph-0.14.0/api_reference/graphicsItems/plotdataitem.html)
warns that bypassing the finite check with nonfinite input can cause incorrect
rendering or a performance penalty.

Native tests across no gaps, occasional NaN, occasional Inf, mixed values, and
long NaN intervals found no meaningful speed difference between the old
setting, explicit finite checking, and automatic handling. The switch to
automatic handling is for correct discontinuities while retaining pyqtgraph's
finite-data fast path. Tests inspect the actual path segments and verify source
arrays remain unchanged.

### Pen rendering and display detail

The more substantial measured gain came from line width and cosmetic rendering.
With safe finite handling and mixed nonfinite data, the four-overlay repaint
p50 / pan-range p50 comparisons were:

| Pen | Repaint p50 | Range-change p50 |
| --- | ---: | ---: |
| Existing dashed/dotted, 1.4 px noncosmetic | 278 ms | 609 ms |
| Dashed/dotted, 1.4 px cosmetic | 42.1 ms | 53.6 ms |
| Dashed/dotted, 1 px cosmetic | 27.7 ms | 42.0 ms |
| Solid, 1 px cosmetic | 30.6 ms | 39.4 ms |
| Dashed/dotted, 1 px cosmetic, antialias enabled | 33.6 ms | 42.3 ms |

The final style uses opaque 1 px cosmetic pens, the existing detector colors,
the existing four Qt dash patterns, then four distinct custom dash patterns
for overlays five through eight. Antialiasing remains off. Solid lines were not
materially faster and are less useful for telling same-color scans apart. The
benchmark screenshots show overlay views at overview and resonance zoom:
[2 overlay overview](screenshots/2-overlays-overview.png),
[2 overlay resonance zoom](screenshots/2-overlays-resonance-zoom.png),
[4 overlay overview](screenshots/4-overlays-overview.png), and
[4 overlay resonance zoom](screenshots/4-overlays-resonance-zoom.png),
[8 overlay overview](screenshots/8-overlays-overview.png), and
[8 overlay resonance zoom](screenshots/8-overlays-resonance-zoom.png).

### Resolution strategy

The earlier wavelength work from commits `606d594` and `1ad17b5` already
provided clipping, peak-preserving PyQtGraph downsampling, range preservation,
coalesced crosshair updates, and incremental legend work. The Power Monitor
design in `7a82cfa` keeps raw acquisition data and a bounded peak/gap summary.
That fixed-point-budget display is not directly suitable for wavelength scans,
where visible wavelength range changes with zoom. A multiresolution wavelength
cache was considered but not added: native range-change p95 fell below 48 ms
with four overlays and to 56 ms at the eight-overlay limit, while retaining
PyQtGraph clipping/downsampling. Displayed peaks remain visible in the zoom
screenshots. The source scan arrays are still attached unchanged to
PlotDataItems. At close zoom, clipping and peak downsampling operate on the
original sample arrays; there is no interpolation, synthetic data, or second
decimation layer.

This is a substantial improvement in a synthetic Windows workload, not a
guarantee for every instrument-generated scan or desktop. Verify with the
operator's actual sanitized scan patterns if lag persists; do not commit
measurement files without authorization.

## First-round offscreen profile (prior commit)

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
uv run --no-sync python -m tools.benchmark_scan_overlays --points 100000 --detectors 4 --overlay-counts 2 4 --finite-mode current --pen-width 1.4 --non-cosmetic --output overlay-before.json
uv run --no-sync python -m tools.benchmark_scan_overlays --points 100000 --detectors 4 --overlay-counts 2 4 --screenshots-dir docs/screenshots --output overlay-after.json
```
