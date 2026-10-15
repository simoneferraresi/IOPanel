# Windows x64 one-folder distribution

The package targets 64-bit Windows and CPython 3.12. It is built from the locked core, `dev`, and `test` dependency sets, explicitly excluding the default `lab` group (VmbPy and MATLAB Engine). The PyInstaller spec is `packaging/IOPanel.spec`, and output is the unpacked `dist/IOPanel/` directory.

## Reproduce the build

From a clean Windows x64 checkout at the repository root, install the pinned uv release and locked dependencies, then run:

```powershell
uv sync --locked --no-default-groups --extra dev --extra test
uv run --no-sync pyinstaller --clean --noconfirm packaging/IOPanel.spec
Copy-Item packaging/config.simulation.ini dist/IOPanel/config.ini
uv run --no-sync python tools/smoke_frozen.py dist/IOPanel/IOPanel.exe
```

Distribute the entire `dist/IOPanel/` folder together and launch `IOPanel.exe` from any working directory. The external `config.ini` beside the EXE is a driver-free example profile. Edit a copy for site use; never substitute the repository's lab-specific `config.ini` into a general distribution. The CI smoke launches this actual executable from a different CWD, verifies Qt resources and simulated device startup, waits for and saves a displayed simulated camera frame, and requires logged graceful shutdown before its 45-second timeout.

## Configuration and writable paths

In a frozen build, the default config is `config.ini` beside `IOPanel.exe`; an absolute `--config` path takes precedence. In source mode, config paths remain relative to the process working directory. Frozen relative log paths (from the config or `--log-file`) resolve under `%LOCALAPPDATA%\IOPanel`; if that location cannot be created, the application uses the system temporary directory. Absolute `--log-file` paths are preserved. Source mode keeps existing current-working-directory log behavior.

Frozen export dialogs default to `%LOCALAPPDATA%\IOPanel\output` (or the system temporary directory if that cannot be created); source mode keeps its current-working-directory default. Users can select another writable folder in each dialog. The executable does not write into PyInstaller's `_internal` directory.

## Profiles and external integrations

`packaging/config.simulation.ini` explicitly selects the simulated CT400 and one simulated camera. `--smoke-test` refuses a physical CT400 backend or any enabled non-simulated camera before constructing the main window. It suppresses MATLAB prewarming and closes through the normal Qt window lifecycle. CI does not install hardware drivers or SDKs.

**This specific frozen EXE is a simulation-only distribution for camera and MATLAB integration.** Its build excludes the VmbPy and MATLAB Engine Python modules, so installing Vimba X, MATLAB, or their Python packages on the destination computer does **not** make them available inside this executable. To enable physical camera functionality or MATLAB `.fig` export in a frozen application, create a **separately built and validated packaging profile** containing the compatible Python modules and required PyInstaller hooks, alongside the externally installed Vimba X SDK or MATLAB application, dependencies, and licenses. Do not use this simulation package as evidence of those integrations.

The CT400 native DLL and Thorlabs PM5020 wrapper/native DLL are external SDK files: a site INI may configure their paths, but they are not bundled. The PM5020 loader stages configured vendor files in a per-user cache only when the operator explicitly connects. These physical paths have **not** been qualified in this package. This milestone includes no physical-device operation, no cross-host SDK compatibility claim, and no proprietary SDK redistributions.

## Build scope

PyInstaller's analysis and platform hooks package the locked application's imported Python and Qt runtime dependencies, including the already compiled Qt resources. The spec explicitly excludes MATLAB, MATLAB Engine, and VmbPy. The output is an unpacked one-folder application with a visible console for startup diagnostics. No installer, updater, signing, or proprietary SDK redistribution is included.

The spec also excludes OpenCV's optional FFmpeg video I/O plugin. IOPanel receives camera frames through its camera backends and uses the retained OpenCV extension for array transforms; it does not open or write video files. See [the measured package inventory and size comparison](windows-package-optimization.md).
