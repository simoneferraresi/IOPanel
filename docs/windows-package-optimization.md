# Windows package size baseline and optimization

The one-folder package is built from the locked `uv.lock` with PyInstaller. Its
manifest can be reproduced with `uv run --no-sync python
tools/inventory_frozen.py dist/IOPanel --archive <artifact.zip> --output
<inventory.json>`. The JSON records the revision, lockfile hash, build versions,
per-file sizes, directory totals, dependency groups, and largest 50 files.

## Baseline and candidate

Measurements use the same x64 Windows host, CPython 3.12.8, PyInstaller 6.22.3,
and lockfile SHA-256 `6319d9346e57b4e88284670c83a085fba2e114bf61027dbcee68a397a55c395d`.
The baseline is merged `main` at `397e4eb726017a408797bb4ac67a70d49e65990b`.
ZIPs were made with PowerShell `Compress-Archive -CompressionLevel Optimal`;
their sizes are local comparisons and may differ from GitHub artifact ZIPs.
The clean baseline build took about 135 seconds wall time; PyInstaller reported
110.9 seconds for the final candidate build. These are single-run timings.

| Metric | Baseline | Candidate | Change |
| --- | ---: | ---: | ---: |
| Extracted folder | 368,123,186 bytes (350.9 MiB) | 337,247,565 bytes (321.5 MiB) | −30,875,621 bytes (−8.39%) |
| ZIP artifact | 146,871,692 bytes (140.0 MiB) | 133,868,835 bytes (127.7 MiB) | −13,002,857 bytes (−8.85%) |

The candidate excludes only `opencv_videoio_ffmpeg*.dll` from PyInstaller's
collected binaries. That optional OpenCV backend supports video-file capture and
writing. IOPanel receives camera frames from its camera backends and uses
OpenCV for array transforms; it does not use `VideoCapture` or `VideoWriter`.
The OpenCV extension remains bundled at 81.9 MiB, preserving frame conversion
and image processing. This reduces both extracted and compressed sizes.

## Retained components

The baseline's largest files are OpenCV `cv2.pyd` (81.9 MiB), Qt's software GL
renderer `opengl32sw.dll` (19.7 MiB), NumPy and SciPy OpenBLAS DLLs (19.6 MiB and
19.3 MiB), the executable (15.9 MiB), Qt Core/Gui/Widgets libraries, and SciPy's
native modules. The software GL renderer is retained for supported fallback
contexts. Qt plugins and all collected translations are unchanged. NumPy,
SciPy, PySide6, OpenGL, OpenCV itself, and their dynamic imports are unchanged.

The baseline inventory is
[`windows-package-baseline-397e4eb.json`](windows-package-baseline-397e4eb.json);
the candidate inventory is
[`windows-package-candidate-397e4eb.json`](windows-package-candidate-397e4eb.json).
Five fresh packaged processes per revision measured median time to the
resource-ready marker at **1.753 s baseline / 1.760 s candidate**. Median
process wall time was **2.360 s / 2.414 s**. These differences are smaller than
the observed run-to-run spread and do not show a meaningful startup change. The candidate smoke also waits for
and saves the first simulated frame, so its full process wall time includes
extra work and should not be read as a startup regression. Raw timings are in
[`windows-package-startup-397e4eb.json`](windows-package-startup-397e4eb.json).

## Validation

The baseline and candidate were each built from a fresh PyInstaller analysis
with the same locked dependency environment. The baseline packaged simulation
smoke passed. The candidate smoke also passed from an unrelated working
directory, verified bundled resources and simulated CT400/camera startup,
waited for a displayed simulated camera frame and saved its PNG, closed cleanly,
and rejected a physical configuration before constructing the GUI. No
physical hardware or MATLAB Engine was started.
