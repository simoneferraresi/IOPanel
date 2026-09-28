# VmbPy compatibility profiles

IOPanel has two camera-development profiles. Their Python API overlap does not make their native runtime pairings interchangeable.

## Supported profiles

| Profile | Binding/runtime | Support statement |
|---|---|---|
| Laboratory SDK-managed | Windows x64; Python 3.12.8; Vimba X 2024.1.0.3916; VmbC 1.0.6; VmbPy 1.0.5 | Physically validated for the bounded B1–B4 path below. VmbPy is supplied and managed by the installed Allied Vision SDK. Keep it outside normal project dependency synchronization. |
| Development packaged wheel | Project-local virtual environment; `uv sync --extra camera`; VmbPy version from `uv.lock` (currently 1.2.2) | Supported for development and API-level tests. It is not evidence of physical-camera compatibility with the laboratory installation. The platform-specific wheel may bundle its matching VmbC runtime; transport layers and device drivers still come from Vimba X. |
| Driver-free development | Project-local environment without the camera extra | Supported for simulated-camera and non-camera work. VmbPy is optional. |

Do not install or synchronize the packaged camera extra into the laboratory's SDK-managed system interpreter. In particular, VmbPy 1.2.2 expects VmbC 1.3.1 in its source-level compatibility check, while the validated laboratory runtime is VmbC 1.0.6. The 1.2.2 wheel profile should use its isolated project environment and a matching VmbC runtime (bundled in the selected binary wheel if supplied). Do not combine the 1.2.2 binding with the laboratory VmbC 1.0.6 runtime.

## Validation scope

The recorded physical evidence for the 1.0.5 tuple covers B1 discovery of both cameras, B2 standalone Top streaming, B3 standalone Side streaming, and B4 application startup, opening both cameras, starting both streams and conversion workers, delivering initial converted images to both panels, and normal shutdown. This is bounded evidence only. It does not establish every IOPanel camera control or feature operation, every VmbPy API, or long-duration stability. VmbPy 1.2.2 has not received physical-camera validation in this laboratory.

## API audit: IOPanel usage

The 1.0.5 column was checked against the read-only system-Python installation on the laboratory PC. The 1.2.2 column was checked against Allied Vision's tagged 1.2.2 source and package metadata. API matches below are source-level findings; only the laboratory profile has the physical evidence described above.

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
| Callback streaming: `start_streaming(handler, buffer_count=...)`, `queue_frame(frame)`, `stop_streaming()` | Present; frame handler receives camera, stream, frame | Same public handler shape and operations | Confirmed compatible at API surface. Timing, shutdown reliability and long-duration behavior are not established for 1.2.2. |
| Camera identity getters `get_id()`, `get_serial()`, `get_model()`, `get_name()` | Present | Present | Confirmed compatible at API surface. |
| Direct `Camera` construction | Constructor takes the older internal camera-info form | Constructor now also receives its interface | Confirmed internal API difference; IOPanel obtains cameras through `VmbSystem` and does not construct `Camera` directly. |
| Native runtime check | Expects VmbC 1.0.6; compatible with recorded laboratory VmbC 1.0.6 | Expects VmbC 1.3.1 in the inspected 1.2.2 source | Confirmed material runtime difference. Do not pair these profiles by substituting only the Python package. |

**Not established:** physical discovery, opening, feature writes, image conversion, streaming stability, or shutdown using VmbPy 1.2.2 with the laboratory cameras and Vimba X transport layers. Source-level API matches do not answer those hardware/runtime questions.

No meaningful physical-compatibility CI matrix is claimed: testing each wheel without its matching VmbC runtime and installed transport layer would not establish camera compatibility. Hardware-independent tests cover the adapter's expected import/API surface and optional-binding failure behavior only.

## Optional import behavior

VmbPy can raise its public `vmbpy.error.VmbSystemError` while importing when VmbC or the required Vimba X runtime cannot be loaded or its version check fails. The camera adapter treats only `ImportError` and that specific VmbPy system error as unavailable optional camera support. Other exceptions are re-raised so programming errors are not hidden. This fallback does not attempt SDK discovery or initialize `VmbSystem`.

## Source references

- [Allied Vision VmbPy 1.2.2 source](https://github.com/alliedvision/VmbPy/tree/1.2.2)
- [Allied Vision VmbPy release history](https://github.com/alliedvision/VmbPy/releases)
- [Allied Vision VmbPy installation and wheel guidance](https://github.com/alliedvision/VmbPy#installation)
