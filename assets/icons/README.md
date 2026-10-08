# IOPanel application-icon candidates

These two candidates are deliberately separate from the active application icon.
No application resource, window-icon call, acquisition code, or packaging behavior
has been changed. Selection and integration require a later review.

## Authoritative references

Both originals are 1254 × 1254 PNG, RGB (8 bits per channel), fully opaque, with
no embedded ICC profile, PNG transparency, or alpha channel. RGB values are
interpreted as sRGB for SVG gradients and perceptual comparisons. This color-space
interpretation is an assumption because the originals contain no profile.

| Candidate | Original attachment filename | SHA-256 |
| --- | --- | --- |
| Optical Burst | `image-gen-3(1).png` (stored as `source/optical_burst_reference.png`) | `c0d0e0509c5246ac7eb3f4ab72005d6bca648787762a4e82f7632af22a7dc136` |
| Prism Spectrum | `image-gen-2(1).png` (stored as `source/prism_spectrum_reference.png`) | `ac953fa5170d99bac3f264b764a93dfbe98943f787b968cf95d3d5ea041bd25c` |

`source/*_reference.png` are byte-identical copies of these attachments. The user
confirmed that the **eight** rays in the supplied prism image are authoritative,
overriding the seven-ray description in the initial brief. The Library originals
have not been edited or replaced.

## Files

* `source/`: immutable approved reference copies.
* `svg/`: editable square `viewBox="0 0 1254 1254"` vector masters.
* `windows/`: Windows multi-image ICOs, containing 16, 24, 32, 48, 64, 128 and 256 px.
* `png/<candidate>/`: RGBA exports at 16, 24, 32, 48, 64, 128, 256, 512 and 1024 px.
* `validation/`: regenerated metrics, full-resolution renders, comparisons,
  heatmaps, overlays, crops, native-size sheets, and HTML report.
* `FINAL_REPORT.md`: measured assessment and scope audit for this reconstruction.
* `../../tools/icons/`: reconstruction, export, and independent geometry/Windows
  validation utilities; isolated development requirements.

PNG exports and diagnostic outputs are retained locally but ignored by Git to
avoid versioning large derived duplicates. Approved PNG references, SVG masters,
ICO production files, scripts, documentation and the final report are retained as reviewable source and production assets.

## Reconstruction method and observed geometry

### Optical Burst

Thresholding and connected-component analysis locate **five** disconnected colored
components. Approximate reference bounding boxes `(x, y, width, height)` are:
`(402,219,484,560)`, `(237,368,521,500)`, `(945,410,67,367)`,
`(757,808,184,155)`, and `(401,914,319,75)`.

The colored silhouette is recovered at a fractional edge-coverage level, using
the locally fitted dark background and the nearly constant red channel as a
coverage estimate. Subpixel contours are fitted to tangent-constrained cubic
Bézier segments recursively, with a 0.24 px fitting tolerance. The reference's
diagonal cut, two rounded internal cut ends, narrow vertical separation, lower
horizontal gap, and detached lower-right component are contained in those contour
paths. They are not rebuilt from evenly spaced radial primitives. Component paths
use approximately 93, 85, 41, 44 and 42 cubic/line spans, respectively, before
the separated gradient groups are applied.

The two large lobes share a smooth tensor B-spline RGB field fitted only to
foreground interior samples. The three smaller components have independently
fitted local quadratic RGB fields: their gradients differ visibly from the large
lobes. Example untouched source samples are `(254,212,3)` at `(350,400)`,
`(253,147,15)` at `(550,400)`, and `(250,41,46)` at `(850,600)`.
The background varies from near `(3,5,10)` around central negative space to
roughly `(12,16,28)` in outer navy regions; it is not a flat fill.

The fitted color fields are expressed as ordinary horizontal SVG gradients and
vertical opacity blends in named groups. This permits smooth two-dimensional
color variation without raster embeds, SVG mesh-gradient extensions, or external
assets. The field samples are widely spaced compared with the reference pixels;
this is a smooth color-field model, not a per-pixel vector mosaic.

### Prism Spectrum

The outline is a filled compound path with a transparent interior and rounded
outer corners, drawn after all eight beams. Visible apex height is about 324 px;
the inner apex is near `(627,385)`, the inner base near 843 px, and the outer
base near 877 px. Outer corner positions are rounded rather than ideal sharp
triangle vertices. Side lines are measured from unobscured reference rows and
used to bridge the short spans covered by the beams. Localization seed vertices
in the reconstruction JSON are approximate seeds, not the final measured vertices.

Measured source side lines (`x = m*y + b`, subpixel image coordinates):

| Boundary | m | b |
| --- | ---: | ---: |
| Left outer | -0.550449 | 800.689331 |
| Left inner | -0.552120 | 839.488955 |
| Right inner | 0.530394 | 423.477894 |
| Right outer | 0.528780 | 462.287299 |

At the left beam sample column (`x=100`), the measured centers are about
474.458, 512.092, 550.314, 588.406, 626.290, 664.854, 703.148 and 742.005 px.
Separation is approximately 37.6–38.9 px, not exactly uniform in the original.
Beam thickness is approximately 21.4–22.4 px at the left and 24.2–24.6 px at the
right. The beams are nearly horizontal; extracted subpixel paths preserve their
slight edge variation instead of imposing an unrelated perfect grid.

| Output band | RGB at source x=1100, nearest measured center |
| --- | --- |
| Red | 253, 11, 19 |
| Orange | 254, 159, 26 |
| Yellow | 246, 234, 36 |
| Green | 74, 228, 36 |
| Turquoise | 0, 207, 148 |
| Cyan | 0, 205, 255 |
| Blue | 30, 94, 253 |
| Violet | 139, 9, 254 |

These exact point samples describe the original; gradient stops use robust
near-center row samples and interpolation across pixels occluded by the white
outline. White-to-color onset and plateau positions differ by band. They are
sampled independently rather than applying one arbitrary transition to all rays.
The outline is approximately RGB `(254,254,254)`; its normal side thickness is
about 34 px, and the base is about 34 px thick. The prism background is subtly
textured near-black navy, often around RGB `(1,4,12)`.

## Transparency reconstruction

The originals' exterior corners are opaque nearly black pixels. A smooth dark
background model is fitted from valid non-artwork pixels. The reference's local
color projection onto that model estimates fractional squircle coverage. The
largest external contour is retained, internal artwork holes are filled for this
boundary extraction only, and the boundary is fitted with 0.45 px Bézier tolerance.
For the prism only, a 15 px morphological closing of the boundary classification,
restricted to the central beam band (rows 400–799), fills thin false exterior
channels caused by beam-edge ringing while preserving corner classifications. The original
pixels and artwork geometry are untouched by this classification operation.
The resulting path clips the entire artwork. No rectangle is added behind it.

Each icon has its own measured boundary. Optical Burst has roughly 13 px left
and 7 px top inset; the prism's squircle reaches the canvas edges. The boundaries
are not replaced with a common rounded rectangle. The low contrast and raster
noise make the original mathematical squircle formula unrecoverable; the fitted
visible edge is an approximation. Exterior corner alpha is genuinely zero, the
interior is opaque, and fractional alpha is confined to antialiased boundaries.

Fidelity metrics compare final RGBA vectors composited on black with the untouched
opaque references, over the **entire 1254 × 1254 frame**. Alpha is assessed
separately because the references contain no authoritative alpha mask. There is
no claim that reconstructed alpha matches a nonexistent original alpha channel.

## Regeneration

Use Python 3.12 in an isolated tools environment. From the repository root:

```powershell
python -m venv .icon-tools
.icon-tools\Scripts\python -m pip install -r tools/icons/requirements-icon-tools.txt
.icon-tools\Scripts\python tools/icons/build_icons.py
.icon-tools\Scripts\python tools/icons/measure_geometry.py
.icon-tools\Scripts\python tools/icons/windows_preview.py
```

The last command requires Windows. It loads each ICO using `LoadImageW` and
composites it with `DrawIconEx` into offscreen DIBs, without opening application
windows. This verifies native loading and transparency, not taskbar behavior.

The default export command reads the maintained SVG masters; it does not replace
their geometry. To explicitly reconstruct them again from the hash-verified PNGs:

```powershell
.icon-tools\Scripts\python tools/icons/reconstruct.py
.icon-tools\Scripts\python tools/icons/build_icons.py
```

Back up manual SVG edits before invoking reconstruction. Different numerical
library versions or platforms can shift floating-point fitted coordinates;
the pinned environment and recorded reference hashes define this reconstruction.
Changing approved artwork requires updating reference hashes deliberately and
rerunning the comparisons; never edit the reference to improve a metric.

## Validation methodology and limitations

The deterministic renderer is resvg-py/resvg. Production exports use 4× spatial
supersampling followed by area integration (Pillow BOX) for edge coverage; the
final full-frame comparison remains exactly 1254 × 1254. The original is neither
blurred nor resized for the principal metric. Direct-native resvg metrics were
also measured and are reported for transparency. Supersampling is the export
antialiasing rule, not a post-comparison blur.

The export utility checks SVG parseability, square viewBox, genuine vector
content, absence of images/external references, IDs/resource references, PNG
dimensions/alpha, ICO structure/frame payloads, and deterministic rerendering.
It records MAE, RMSE, maximum channel error, pixel threshold exceedance, SSIM,
edge-distance statistics and perceptual color errors. Localized geometry tests
add per-component burst contour distances, prism side/apex/base measurements,
and eight-ray edge positions at left and right sample columns.

The source images contain random fine texture and ringing/brightness bands near
high-contrast edges. The masters preserve smooth geometry and measured gradients,
but do not reproduce that random grain pixel by pixel. Remaining local gradient
and edge-coverage errors also exist. These are measured reconstructions, not
pixel-perfect or mathematically proven visually indistinguishable copies.

At 16 and 24 px, the prism's eight rays approach or fall below one pixel of
separation; their independence cannot be guaranteed by full-resolution geometry.
The authoritative master is unchanged. A separate, explicitly approved small-size
variant could improve legibility; no such variant has been created or selected.

## Later integration audit

At baseline HEAD `1f30e2f22452c025e38c7259d4273cdb7b9b45db`,
`ui/main_window.py:679` sets `QIcon(":/icons/laser.svg")` after importing the
compiled Qt resources. `app.py` configures QApplication but does not set an
application-wide icon. `resources/resources.qrc` contains UI SVG aliases, and
`resources/resources_rc.py` is tracked generated output. The README documents
resource regeneration with `pyside6-rcc`. There is no checked-in ICO, PyInstaller
specification, installer, or executable-icon build configuration.

After selecting a candidate, a separate change can add the selected SVG to qrc,
set the window/application icon, regenerate the compiled resource module, and
pass the selected ICO to PyInstaller's executable-icon build configuration.
Resource tests and a packaged Windows/taskbar smoke test belong to that later
change. No candidate is currently selected or installed.
