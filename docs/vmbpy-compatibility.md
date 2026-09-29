# VmbPy compatibility profiles

IOPanel documents a preferred validated laboratory profile, a historical rollback profile, and driver-free development. Their Python API overlap does not make native runtime pairings interchangeable.

## Supported profiles

| Profile | Binding/runtime | Support statement |
|---|---|---|
| Preferred laboratory candidate | Windows x64; Python 3.12.8; Vimba X 2026-2; VmbPy 1.2.2; VmbC 1.3.1; VmbImageTransform 2.3 | Physically validated with two Allied Vision Mako G-125B cameras using the Vimba/Vimba X backend in an isolated project/candidate environment. |
| Historical rollback | Windows x64; Python 3.12.8; Vimba X 2024.1.0.3916; VmbC 1.0.6; VmbPy 1.0.5 | Previously validated profile retained for rollback/reference. |
| Driver-free development | Project-local environment without the camera extra | Supported for simulated-camera and non-camera work. VmbPy is optional. |

Keep the preferred VmbPy 1.2.2 environment isolated from the historical VmbPy 1.0.5 / VmbC 1.0.6 rollback environment. Vimba 6 may remain installed for rollback/reference; current IOPanel camera operation has been shown not to require its GenTL transport layers.

## Validation scope

The preferred profile passed standalone physical streaming on Top and Side; IOPanel startup with both cameras and DummyCT400; opening, streaming and converted-frame display on both; clean shutdown and VmbSystem release; manual Exposure and Gamma on each; and one-shot Auto Exposure on both. One-shot Auto Gain also succeeded on both after the Vimba X update. VmbPy 1.2.2 / VmbC 1.3.1 worked with the existing Vimba X installation and continued to work after Vimba X was updated to 2026-2. A final isolated GenTL test removed legacy Allied Vision Vimba 6 transport-layer paths from `GENICAM_GENTL64_PATH`, retained Basler Pylon paths unchanged, and retained the Vimba X GenTL transport layer; both cameras opened, streamed, displayed frames and closed normally. This demonstrates practical independence from legacy Vimba 6 GenTL transport layers for the tested IOPanel camera path, without forensically excluding every possible DLL-loading mechanism. Dual-camera operation was also observed during normal use, but no separately timed formal endurance gate was run. This camera-only qualification used DummyCT400 and by itself did not validate physical CT400 operation.

The preferred profile was subsequently exercised concurrently with the physical CT400 in IOPanel during two natural-completion scans. Both physical camera feeds remained visibly live and updating before, during and after both CT400 sweeps, with no observed camera freeze or stream failure. This establishes the tested concurrent workflow only; it was not a timed endurance test, and cancellation while both cameras streamed was not run. See the [current laboratory qualification summary](laboratory-validation-report.md#current-qualification-summary) for the CT400 settings, results and scope limits.

## API audit: IOPanel usage

The 1.0.5 column was checked against the read-only system-Python installation on the laboratory PC. The 1.2.2 column was checked against Allied Vision's tagged 1.2.2 source and package metadata. API matches below are source-level findings; physical evidence for 1.2.2 is summarized above.

| IOPanel API surface | VmbPy 1.0.5 | VmbPy 1.2.2 | Assessment |
|---|---|---|---|
| Imported names: `COLOR_PIXEL_FORMATS`, `MONO_PIXEL_FORMATS`, `OPENCV_PIXEL_FORMATS`, `Camera`, `Frame`, `FrameStatus`, `PixelFormat`, `Stream`, `VmbCameraError`, `VmbSystem`, `VmbSystemError`, `intersect_pixel_formats` | Exported from `vmbpy` | Exported from `vmbpy` | Confirmed compatible at import/API surface. |
| `VmbSystem.get_instance()` and `VmbSystem` context manager | Present; context enters and starts the VmbC API | Present; same context-managed lifecycle | Confirmed compatible at API surface. Runtime pairing remains version-specific. |
| `get_all_cameras()` and `get_camera_by_id(id)` | Present on active system context | Present on active system context | Confirmed compatible at API surface. |
| Camera context management | `Camera.__enter__` opens; `__exit__` closes | Same public lifecycle | Confirmed compatible at API surface. |
| `get_feature_by_name(name)` | Inherited from `FeatureContainer` | Inherited from `FeatureContainer` | Confirmed compatible at API surface. |
| Feature `get()`, `set(value)`, `get_range()`, `is_readable()`, `is_writeable()` | Present on applicable feature types/base feature | Same methods on applicable feature types/base feature | Confirmed compatible at API surface. Device-specific feature availability and behavior are not established across profiles. |
| `get_pixel_formats()` / `set_pixel_format(format)` | Present; returns supported `PixelFormat` values and accepts one | Present with the same public purpose | Confirmed compatible at API surface. |
| Pixel-format constants and `intersect_pixel_formats()` | `MONO_PIXEL_FORMATS`, `COLOR_PIXEL_FORMATS`, `OPENCV_PIXEL_FORMATS`, helper exported | Same names and helper exported | Confirmed compatible at API surface. |
| `FrameStatus.Complete`, `Frame.as_opencv_image()` | Present | Present | Confirmed compatible at API surface; actual format conversion depends on installed optional numeric/image dependencies and runtime. |
| Callback streaming: `start_streaming(handler, buffer_count=...)`, `queue_frame(frame)`, `stop_streaming()` | Present; frame handler receives camera, stream, frame | Same public handler shape and operations | Confirmed compatible at API surface. The tested open/stream/display/shutdown path passed; no separately timed endurance gate was run. |
| Camera identity getters `get_id()`, `get_serial()`, `get_model()`, `get_name()` | Present | Present | Confirmed compatible at API surface. |
| Direct `Camera` construction | Constructor takes the older internal camera-info form | Constructor now also receives its interface | Confirmed internal API difference; IOPanel obtains cameras through `VmbSystem` and does not construct `Camera` directly. |
| Native runtime check | Expects VmbC 1.0.6; compatible with recorded laboratory VmbC 1.0.6 | Expects VmbC 1.3.1 in the inspected 1.2.2 source | Confirmed material runtime difference. Do not pair these profiles by substituting only the Python package. |

**Scope limit:** these results establish the listed IOPanel camera path and controls only. They do not establish every VmbPy API, every camera feature, or long-duration endurance.

No meaningful physical-compatibility CI matrix is claimed: testing each wheel without its matching VmbC runtime and installed transport layer would not establish camera compatibility. Hardware-independent tests cover the adapter's expected import/API surface and optional-binding failure behavior only.

## Optional import behavior

VmbPy can raise its public `vmbpy.error.VmbSystemError` while importing when VmbC or the required Vimba X runtime cannot be loaded or its version check fails. The camera adapter treats only `ImportError` and that specific VmbPy system error as unavailable optional camera support. Other exceptions are re-raised so programming errors are not hidden. This fallback does not attempt SDK discovery or initialize `VmbSystem`.

## Source references

- [Allied Vision VmbPy 1.2.2 source](https://github.com/alliedvision/VmbPy/tree/1.2.2)
- [Allied Vision VmbPy release history](https://github.com/alliedvision/VmbPy/releases)
- [Allied Vision VmbPy installation and wheel guidance](https://github.com/alliedvision/VmbPy#installation)
