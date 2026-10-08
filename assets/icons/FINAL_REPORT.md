# IOPanel icon reconstruction — final review report

Date: 8 October 2026. Both candidates have editable vector masters, transparent
RGBA exports, Windows ICOs and reproducible evidence. They have **not** been
selected or installed in IOPanel. Neither reaches the aspirational 0.995 SSIM
target; neither is described as pixel-perfect or proven indistinguishable.

## Repository baseline and scope

The source assets were prepared in a clean clone of `simoneferraresi/IOPanel` at
`1f30e2f22452c025e38c7259d4273cdb7b9b45db`. They are being imported on
`design/vector-app-icons`, based on the fetched current `main`. No application
code, Qt resource bundle, packaging behavior, or laboratory hardware was changed.
The active `:/icons/laser.svg` application/window icon remains unchanged.

The repository changes are limited to `.gitignore`, `assets/icons/`, and
`tools/icons/`. Generated PNG exports and diagnostic previews are reproducible
and excluded from version control; approved source PNGs, SVGs, ICOs, tools,
measurements and documentation are retained.

## References and provenance

Both references are PNG, 1254 × 1254, RGB, 8-bit per channel, with no alpha,
transparency metadata or embedded ICC profile. Color comparison assumes sRGB.

Optical Burst original attachment: `image-gen-3(1).png`; preserved as `source/optical_burst_reference.png`.

SHA-256: `c0d0e0509c5246ac7eb3f4ab72005d6bca648787762a4e82f7632af22a7dc136`

Prism Spectrum original attachment: `image-gen-2(1).png`; preserved as `source/prism_spectrum_reference.png`.

SHA-256: `ac953fa5170d99bac3f264b764a93dfbe98943f787b968cf95d3d5ea041bd25c`

Original attachments and `source/*_reference.png` copies were verified
byte-identical at completion. They were never recolored, resized, blurred,
retouched, edited, or replaced in the Library. The user explicitly confirmed
preserving **eight** prism rays and spectral bands in the supplied reference.

Canvas dimensions, file hashes, channel/profile properties, eight-ray count,
five burst-component count, and RGB point samples are directly observed facts.
The original Bézier controls, mathematical squircle formula and continuous
gradient definitions cannot be recovered exactly from raster pixels. Their
fitted vector representations are approximations with measured errors.

## Reconstruction and controlled iterations

Optical Burst uses subpixel contour extraction and recursive cubic Bézier
fitting. Its asymmetry, rounded negative-space endpoints and five disconnected
components are preserved. Separate color fields for the smaller pieces corrected
visible errors in the initial shared gradient model. The large lobes use a smooth
tensor B-spline RGB fit; small pieces use independently fitted quadratic fields.
The fields are emitted as standard vector gradients with opacity masks.

Prism Spectrum uses eight separately sampled, nearly horizontal beam paths and
color ramps. Its measured slight taper and spacing variation are retained. Side
lines are fitted from unobscured rows and bridged through beam intersections.
The white compound outline is drawn above the beams; rounded corners are fitted
from the source. All masters use square `viewBox="0 0 1254 1254"`, named layers,
internal gradients/masks/clips, and no raster images or external dependencies.

Intermediate direct-render measurements improved Optical Burst from MAE
1.009616 / SSIM 0.984549 to its final direct-render values. A clean early prism
fit measured MAE 1.688412 / SSIM 0.962595; subsequent beam-boundary, outline-corner
and gradient corrections improved it. The final direct and production metrics
are recorded below and in `MEASUREMENTS.json`.

An alpha audit found beam-edge ringing mistakenly classified as exterior,
creating transparent notches. Boundary classification was corrected only in the
central prism beam band; corner classifications remain unchanged. The final
interior is opaque. Restoring opaque dark background can slightly worsen black
RGB comparison near source ringing while correctly preserving appearance on
light backgrounds. This was retained for alpha correctness, not hidden.

Production antialiasing uses resvg at 4× dimensions followed by BOX area
integration to the requested size. A 1×/2×/4× renderer comparison informed that
choice. Sources remain untouched; principal metrics use the entire native
1254 × 1254 frame. No mask or downsample hides errors in the principal scores.

## Full-frame quantitative fidelity

RGB comparison: production RGBA render composited on black against the original
opaque PNG. MAE/RMSE are unnormalized channel intensity levels.

| Metric | Optical Burst | Prism Spectrum |
| --- | ---: | ---: |
| SSIM (full native frame) | 0.98598460 | 0.96811775 |
| MAE (0–255 levels) | 0.91220058 | 1.34824277 |
| RMSE (0–255 levels) | 1.89206673 | 3.87369600 |
| Maximum channel difference | 89.00000000 | 172.00000000 |
| Normalized MAE (MAE/255) | 0.00357726 | 0.00528723 |

| Pixel exceedance criterion | Optical Burst | Prism Spectrum |
| --- | ---: | ---: |
| > 1 levels in any channel | 34.560284% | 32.424344% |
| > 5 levels in any channel | 2.384523% | 6.405722% |
| > 10 levels in any channel | 0.803235% | 3.118442% |
| > 25 levels in any channel | 0.234910% | 0.861613% |

Direct-native resvg supplemental scores: Optical Burst SSIM
0.98573870,
MAE 0.90355731;
Prism Spectrum SSIM
0.96658540,
MAE 1.39553599.
Downsampled SSIM diagnostics are included but are explicitly **not MS-SSIM**.
A formal multiscale-SSIM calculation was not performed.

## Geometry, color and alpha assessed separately

Burst component contours: mean symmetric subpixel distances range approximately
0.065–0.112 px; the largest component 95th percentile is below 0.28 px, and the
worst localized contour distance is below 0.75 px. This includes the radial-cut
boundaries and detached components. Detailed per-component measurements are in
`validation/localized_geometry.json`.

Prism ray boundaries at x=100 and x=1100 differ by at most approximately 0.17 px.
Its visible apex and base-bottom threshold rows match the original in the
independent measurements. Fitted side lines and extrapolated sharp-apex locations
are recorded separately; the visible apex is rounded, so a sharp-line intersection
is not called the visible vertex.

Whole-frame Canny symmetric chamfer distances: Optical Burst
0.113214 px and Prism Spectrum
0.033763 px. This pixel-grid diagnostic
does not replace the localized subpixel measurements.

Color-only interior ΔE2000 (three-pixel edge exclusion, reported separately from
full-frame pixel metrics): Optical Burst mean
0.599696, 95th percentile
1.459130; Prism Spectrum mean
0.434286, 95th percentile
1.187626. Broad foreground-region
errors, including edges, are also retained in the raw report.

Squircle edge diagnostics are estimates because source images have no alpha
ground truth and their dark edges have low contrast. The independent RGB-projection
comparison reports burst mean/p95/max distances
0.245/
1.192/
2.072 px;
prism mean/p95/max
0.167/
0.388/
7.970 px.
These values include ambiguity from source quantization; they are not measurements
against an original alpha mask.

All nine PNG export sizes have zero alpha at exterior corners and opaque centers.
The native central square is entirely opaque, and the deeper interior contains
zero partially transparent pixels. Partial alpha is confined to the curved or
canvas-clipped exterior boundary. White/dark compositing and native Win32 checks
support the absence of black rectangular padding or stray internal transparency.

## Visual evidence and native-size assessment

`validation/report.html` contains actual original/render comparisons, a full-size
split slider, side-by-side PNGs, 50% overlays, difference heatmaps, edge overlays,
enlarged discrepancy crops, contact sheets, and all nine actual-size PNGs against
white, light gray, dark gray, black and a labelled synthetic Windows-blue background.
No comparison was generated by an image-generation model.

Optical Burst: the silhouette, main cuts and detached piece remain recognizable
at small sizes. At 16 px, the narrowest gaps and the detached piece approach
single-pixel features and lose detail. The warm gradient remains coherent.

Prism Spectrum: the triangular prism stays identifiable at 24 px and above;
at 16 px the horizontal rays dominate. The eight incident rays and spectral bands
merge or lose separation at 16–24 px, and separation is still limited at 32 px.
The arrangement becomes materially clearer at 48–64 px and above. These are
visual judgments from actual exports, not quantitative aesthetic guarantees.

No master geometry was changed to optimize 16 px. No simplified variant was
created or chosen. A separate approved size-specific prism variant would be
beneficial if small taskbar/menu legibility is a selection priority.

## Windows ICO production and checks

Each ICO has 16, 24, 32, 48, 64, 128 and 256 px entries, each 32-bit RGBA PNG
payload, with valid ICONDIR/ICONDIRENTRY metadata. Payload bytes are identical to
the corresponding retained PNG exports. They are genuine ICO containers, not
renamed PNG files. Native Win32 `LoadImageW`/`DrawIconEx` loaded and composited all
seven sizes of both ICOs on white and dark gray: 28 checks passed, maximum channel
deviation from expected PNG compositing was
1 intensity level.

Windows taskbar, Explorer caching, packaged executable resources, installer and
live IOPanel UI checks were **not performed**. Only resvg was used as an SVG
renderer; cross-renderer/editor/Qt SVG compatibility remains untested. For later
Qt integration, validate SVG mask support or use the retained RGBA PNG resources.

## Tests executed

* Hash verification of both originals and byte-identical reference copies: pass.
* XML parse, exact square viewBox, vector-only content, no embedded rasters or
  external resources, unique IDs and resolving internal references: pass.
* Both SVG renders at native size and all requested PNG sizes: pass.
* Alpha exterior/corners, central opaque square and full deeper interior: pass.
* ICO structural parsing, intended dimensions/32bpp, exact PNG payloads: pass.
* Repeated native renders, master reconstruction and production export hashes:
  deterministic in the tested pinned Windows environment.
* Full-frame metrics, visual reports and report-local link checks: pass.
* Localized burst/prism/squircle geometry measurements: executed and reported.
* Native Win32 ICO loading/compositing on light/dark backgrounds: pass.
* Ruff 0.16.10 checks/format checks and Python compilation for the new tools: pass.
* `git diff --check` with the bundled Git: pass.

Application pytest/Qt resource tests were not run: application behavior/resources
were not changed, and the isolated icon environment does not contain the full
application test dependencies. No hardware-dependent tests or imports were run.

## Files and artifacts

Reviewable sources/production files: `assets/icons/source/*.png`,
`assets/icons/svg/*.svg`, `assets/icons/windows/*.ico`, `README.md`, this report,
`REFERENCE_MANIFEST.json`, `MEASUREMENTS.json`, and `tools/icons/` containing
four Python utilities and isolated requirements. Modified: `.gitignore` only.

Generated locally: `assets/icons/png/` (18 RGBA exports), `assets/icons/validation/`
(all diagnostic evidence), plus a review ZIP and two-candidate preview in the
outer workspace `outputs/` folder. Generated diagnostics/PNG exports are ignored
in the repository; the ZIP retains them for review.

## Final evaluation and next step

Optical Burst has strong geometric and color agreement and useful small-size
legibility. Its remaining errors are source grain, edge coverage, and small
smooth-gradient/background differences. No large silhouette discrepancy remains.
Its ICO is technically usable; artistic approval of the measured reconstruction
is still required before installing it. More work is needed only if the reviewer
requires near-literal full-resolution grain/edge reproduction or the 0.995 target.

Prism Spectrum has strong geometric alignment and low interior color error, but
larger full-frame raster differences around ray edges and color transitions.
Its ICO is technically usable; small-size legibility is weaker. Additional
ray-edge/transition work would be appropriate before claiming full-resolution
visual indistinguishability. A separate approved small-size variant is optional.

Fine source texture and ringing are not reproduced with thousands of microscopic
paths or a raster-backed SVG. Doing so would add substantial complexity for
limited visible benefit; the remaining measured differences are disclosed rather
than hidden. No final candidate is selected automatically.

After reviewing the candidates, select one for a separate Qt and Windows
packaging integration change. IOPanel's current icon remains active.
